"""Run an isolated Airflow DAG with good/bad coverage and prove downstream gating.

Execute inside the existing Airflow container. All files use the mounted project.
Creates unique DAG runs in the metadata DB, never invokes the main ETL pipeline.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify():
    from airflow import DAG
    from airflow.operators.bash import BashOperator
    from airflow.operators.python import PythonOperator
    from airflow.utils.state import TaskInstanceState
    run_id = uuid4().hex[:12]
    work = ROOT / "output/extensions" / ("airflow_" + run_id)
    work.mkdir(parents=True)
    manifest = work / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "workshops": {"W01": ["E01", "E02"]}}))
    production = work / "production.csv"
    production.write_text("record_date,workshop_code\n2026-09-27,W01\n")
    results = []
    for mode in ("good", "missing_energy"):
        energy = work / f"{mode}.csv"
        energy.write_text("record_date,workshop_code,energy_code\n2026-09-27,W01,E01\n" +
                          ("2026-09-27,W01,E02\n" if mode == "good" else ""))
        marker = work / f"{mode}_downstream.txt"
        quality_report = work / f"{mode}_report.json"
        with DAG(dag_id=f"energy_quality_probe_{run_id}_{mode}", start_date=datetime(2026,9,27,tzinfo=timezone.utc),
                 schedule=None, catchup=False, default_args={"retries": 0}) as dag:
            gate = BashOperator(task_id="coverage_gate", bash_command=(
                f"python {ROOT}/tools/check_energy_coverage.py --energy {energy} --production {production} "
                f"--manifest {manifest} --output {quality_report}"))
            downstream = PythonOperator(task_id="downstream_report", python_callable=marker.write_text,
                                        op_args=["generated"])
            gate >> downstream
        dag_run = dag.test(execution_date=datetime(2026,9,27,tzinfo=timezone.utc))
        instances = {ti.task_id: ti.state for ti in dag_run.get_task_instances()}
        expected_gate = TaskInstanceState.SUCCESS if mode == "good" else TaskInstanceState.FAILED
        expected_downstream = TaskInstanceState.SUCCESS if mode == "good" else TaskInstanceState.UPSTREAM_FAILED
        if instances["coverage_gate"] != expected_gate or instances["downstream_report"] != expected_downstream or marker.exists() != (mode == "good"):
            raise RuntimeError("Airflow did not enforce the gate")
        results.append({"mode": mode, "task_states": instances, "downstream_file_exists": marker.exists(),
                        "dag_id": dag.dag_id, "run_id": dag_run.run_id})
    report = {"success": True, "generated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "isolated DAG.test in real Airflow; not scheduler deployment", "cases": results}
    (ROOT / "output/extensions/airflow_quality_runtime.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    verify()
