"""Run only the canonical report CLI and existing Airflow report operator.

Uses existing immutable CSV artifacts and separate output folders. Does not
trigger a DAG, load warehouses, edit scheduler metadata or replace deployments.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from uuid import uuid4
from tools.build_analysis_report import ROOT, hashes


def run():
    def current_sources():
        return {**hashes(), **{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                              ('dags/energy_pipeline_dag.py','tools/verify_report_entry.py')}}
    source_hashes=current_sources()
    metadata=ROOT/'output/extensions/semantic_runtime.json'
    metadata_bytes=metadata.read_bytes()
    semantic=json.loads(metadata_bytes)
    source=(ROOT/semantic['evidence_directory']).resolve()
    if not semantic.get('success') or not source.is_relative_to((ROOT/'output/extensions').resolve()):
        raise ValueError('Unverified analysis input')
    folder=ROOT/'output/extensions'/('report_entry_'+uuid4().hex[:12])
    folder.mkdir(parents=True)
    destination=folder/'local/report.html'
    result=subprocess.run([sys.executable,str(ROOT/'src/make_report.py'),'--input-dir',str(source),
                           '--output',str(destination)],cwd=ROOT,capture_output=True,timeout=90)
    (folder/'local.log').write_bytes(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('Report CLI failed; inspect retained local.log')
    remote_source='/opt/airflow/project/'+source.relative_to(ROOT).as_posix()
    remote_target='/opt/airflow/project/'+(folder/'airflow/report.html').relative_to(ROOT).as_posix()
    code=("import sys;sys.path.insert(0,'/opt/airflow/project');"
          "from dags.energy_pipeline_dag import dag;task=dag.get_task('build_report');"
          f"task.bash_command += ' --input-dir '+{shlex.quote(remote_source)!r}+' --output '+{shlex.quote(remote_target)!r};"
          "task.execute(context={})")
    result=subprocess.run(['docker','exec','industrial_energy_analysis-airflow-scheduler-1',
                           'python','-c',code],capture_output=True,timeout=90)
    (folder/'airflow_operator.log').write_bytes(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('Airflow report operator failed; inspect retained airflow_operator.log')
    local=destination.read_text(encoding='utf-8')
    remote=(folder/'airflow/report.html').read_text(encoding='utf-8')
    if local!=remote:
        raise AssertionError('Host and Airflow report contents differ')
    for token in ('window.ENERGY_ANALYSIS_CONTRACT=', 'window.EnergyAnalysis.months(monthly)',
                  'window.EnergyAnalysis.average(days,vals)', 'window.EnergyAnalysis.pareto(VIEW.energy_mix)'):
        assert token in remote,token
    assert 'fonts.googleapis.com' not in remote
    assert current_sources()==source_hashes
    assert metadata.read_bytes()==metadata_bytes,'Semantic input record changed during verification'
    report={'success':True,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'source_hashes':source_hashes,'input_directory':str(source),
            'source_semantic_report_sha256':hashlib.sha256(metadata_bytes).hexdigest(),
            'canonical_cli_passed':True,'airflow_build_report_operator_passed':True,
            'host_and_container_html_equal':True,'normalized_html_sha256':hashlib.sha256(remote.encode()).hexdigest(),
            'formula_engines_included':3,'evidence_directory':str(folder.relative_to(ROOT)),
            'scope':'report operator only; no DAG trigger, warehouse update, deployment replacement or scheduler acceptance'}
    (folder/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'verification.md').write_text(
        '# 报告生成入口验收\n\n结论：通过。\n\n'
        '- 原报告 CLI 生成成功。\n- 现有 Airflow build_report 操作器单独执行成功。\n'
        '- 两个运行环境生成的 HTML 内容一致，包含月环比、七日历日均值和费用帕累托。\n'
        '- 没有触发 DAG、更新数仓或替换运行中的 CDC 部署。\n\n'
        f'源码摘要在验收前后保持一致。报告位于：`{destination}`。\n',encoding='utf-8')
    print(json.dumps({'success':True,'report':str(destination),'verification':str(folder/'verification.json')},ensure_ascii=False))
    return report


if __name__=='__main__':
    run()
