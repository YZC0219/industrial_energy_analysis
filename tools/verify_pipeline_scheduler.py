"""Execute the complete main DAG twice through a real isolated LocalExecutor scheduler.

Copies current uncommitted source to D-backed files; separate MySQL and Airflow
databases prevent changes to existing business data or production run history.
Hive/CDC reconciliation/deep model switches are disabled explicitly and reported.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
AIRFLOW = "industrial_energy_analysis-airflow-scheduler-1"
POSTGRES = "industrial_energy_analysis-postgres-1"


def command(args, timeout=90):
    result = subprocess.run(args, check=False, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed (exit {result.returncode}); inspect isolated logs")
    return result.stdout.strip()


def run():
    stamp = uuid4().hex[:12]
    work = ROOT / "output/extensions" / ("scheduler_" + stamp)
    work.mkdir(parents=True)
    project = work / "project"
    project.mkdir()
    for name in ("src", "tools", "sql", "ml", "quality", "hive", "spark", "datax", "governance", "optimization"):
        shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("data", "output", "docs", "dags"):
        (project / name).mkdir()
    for path in (ROOT / "docs").glob("*.md"):
        shutil.copy2(path, project / "docs" / path.name)
    dag_id = "energy_pipeline_probe_" + stamp
    metadata = "airflow_pipeline_probe_" + stamp
    mysql_db = "industrial_energy_pipeline_probe_" + stamp
    original = (ROOT / "dags/energy_pipeline_dag.py").read_text(encoding="utf-8")
    if original.count('dag_id="energy_pipeline"') != 1 or original.count('schedule="0 2 * * *"') != 1:
        raise ValueError("Main DAG declaration changed; review probe adaptation")
    adapted = original.replace('dag_id="energy_pipeline"', f'dag_id="{dag_id}"').replace('schedule="0 2 * * *"', 'schedule=None')
    (project / "dags/energy_pipeline_dag.py").write_text(adapted, encoding="utf-8")
    for path in (project / "sql").glob("*.sql"):
        path.write_text(path.read_text(encoding="utf-8").replace("industrial_energy", mysql_db), encoding="utf-8")
    logs = work / "logs"
    logs.mkdir()
    config_code = "import os,json; print(json.dumps({k:os.getenv(k,'') for k in ['MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_PWD','AIRFLOW__DATABASE__SQL_ALCHEMY_CONN']}))"
    settings = json.loads(command(["docker", "exec", AIRFLOW, "python", "-c", config_code]))
    connection = settings["AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"]
    if "@postgres/" not in connection or not connection.startswith("postgresql+psycopg2://"):
        raise ValueError("This probe requires the existing local postgres service")
    env = {"AIRFLOW__CORE__EXECUTOR": "LocalExecutor", "AIRFLOW__CORE__LOAD_EXAMPLES": "false",
           "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": connection.rsplit("/", 1)[0] + "/" + metadata,
           "AIRFLOW__CORE__DAGS_FOLDER": "/opt/airflow/project/dags", "AIRFLOW__CORE__PARALLELISM": "2",
           "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION": "true", "AIRFLOW__SCHEDULER__DAG_DIR_LIST_INTERVAL": "5",
           "AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL": "5", "PROJECT_DIR": "/opt/airflow/project",
           "MYSQL_DB": mysql_db, "MYSQL_HOST": settings["MYSQL_HOST"], "MYSQL_PORT": settings["MYSQL_PORT"] or "3306",
           "MYSQL_USER": settings["MYSQL_USER"], "MYSQL_PWD": settings["MYSQL_PWD"],
           "LAKEHOUSE_ENABLED": "0", "STREAM_RECON_ENABLED": "0", "DEEP_LEARNING_ENABLED": "0"}
    private = ROOT / "tmp" / ("pipeline_scheduler_" + stamp)
    private.mkdir(parents=True)
    env_file = private / "runtime.env"
    if any("\n" in value or "\r" in value for value in env.values()):
        raise ValueError("Environment values must be single-line")
    env_file.write_text("".join(f"{key}={value}\n" for key,value in env.items()), encoding="utf-8")
    container = "energy-pipeline-scheduler-" + stamp
    report = {"success": False, "generated_at": datetime.now(timezone.utc).isoformat(), "dag_id": dag_id,
              "metadata_database": metadata, "mysql_database": mysql_db, "scheduler_container": container,
              "source_dag_sha256": hashlib.sha256(original.encode()).hexdigest(), "evidence_directory": str(work),
              "scope": "main DAG graph and scripts in isolated scheduler/databases; main deployment unchanged",
              "disabled": ["Hive/Spark branch", "Kafka history reconciliation", "deep models"], "runs": []}
    protected_sources = ["src/import_mysql.py", "src/clean_data.py", "src/generate_data.py", "src/make_report.py",
                         "governance/consumers.py", "governance/catalog.py", "governance/metrics.yaml",
                         "governance/browser.py", "src/semantic_metrics.js", "src/report_template.html",
                         "tools/check_energy_coverage.py", "tools/check_data_quality.py", "tools/run_phase2.py",
                         "quality/rules/energy_batch.json", "quality/rules/workshop_energy_coverage.json",
                         "sql/create_table.sql", "sql/analysis.sql"]
    report["source_hashes"] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in protected_sources}
    started = False
    def pg(sql):
        return command(["docker", "exec", POSTGRES, "psql", "-U", "airflow", "-d", metadata, "-At", "-c", sql])
    def warehouse():
        code = ("import os,json,pymysql; c=pymysql.connect(host=os.getenv('MYSQL_HOST'),port=int(os.getenv('MYSQL_PORT','3306')),"
                "user=os.getenv('MYSQL_USER'),password=os.getenv('MYSQL_PWD')); "
                f"cur=c.cursor(); cur.execute('SELECT COUNT(*), CAST(SUM(cost) AS CHAR) FROM `{mysql_db}`.fact_energy_consumption WHERE is_deleted=0'); "
                "r=cur.fetchone(); print(json.dumps({'active_rows':r[0],'total_cost_yuan':r[1]})); c.close()")
        return json.loads(command(["docker", "exec", AIRFLOW, "python", "-c", code]))
    try:
        relative = work.relative_to(ROOT).as_posix()
        remote_project = "/opt/airflow/project/" + relative + "/project"
        initializer = ("import os,sys; from types import SimpleNamespace; "
                       f"sys.path.insert(0,{remote_project + '/src'!r}); import import_mysql as m; "
                       "a=SimpleNamespace(host=os.getenv('MYSQL_HOST'),port=int(os.getenv('MYSQL_PORT','3306')),"
                       "user=os.getenv('MYSQL_USER'),password=os.getenv('MYSQL_PWD')); c=m.connect(a); "
                       f"m.run_script(c,{remote_project + '/sql/create_table.sql'!r},use_db=None); c.close()")
        command(["docker", "exec", AIRFLOW, "python", "-c", initializer])
        command(["docker", "exec", POSTGRES, "psql", "-U", "airflow", "-d", "postgres", "-c", f"CREATE DATABASE {metadata}"])
        base = ["docker", "run", "--pull", "never", "--network", "industrial_energy_analysis_default",
                "--env-file", str(env_file), "--mount", f"type=bind,source={project},target=/opt/airflow/project",
                "--mount", f"type=bind,source={logs},target=/opt/airflow/logs"]
        command([*base, "--name", "energy-pipeline-init-" + stamp,
                 "industrial-energy-airflow:2.10.5", "airflow", "db", "migrate"])
        command([*base, "-d", "--name", container, "industrial-energy-airflow:2.10.5", "airflow", "scheduler"])
        started = True
        deadline = time.monotonic() + 90
        while not pg(f"SELECT dag_id FROM dag WHERE dag_id='{dag_id}'"):
            if time.monotonic() > deadline:
                raise TimeoutError("Isolated main DAG did not parse")
            time.sleep(3)
        command(["docker", "exec", container, "airflow", "dags", "unpause", dag_id])
        baseline = None
        for phase in ("initial", "identical_replay"):
            run_id = f"verify_{stamp}_{phase}"
            command(["docker", "exec", container, "airflow", "dags", "trigger", dag_id, "--run-id", run_id])
            deadline = time.monotonic() + 480
            while True:
                state = pg(f"SELECT state FROM dag_run WHERE dag_id='{dag_id}' AND run_id='{run_id}'")
                if state in ("success", "failed"):
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Scheduler run exceeded 8 minutes")
                time.sleep(5)
            raw = pg(f"SELECT COALESCE(json_agg(json_build_object('task_id',task_id,'state',state,'start_date',start_date,'end_date',end_date) ORDER BY task_id),'[]') FROM task_instance WHERE dag_id='{dag_id}' AND run_id='{run_id}'")
            tasks = json.loads(raw)
            record = {"phase": phase, "run_id": run_id, "state": state, "tasks": tasks}
            report["runs"].append(record)
            if state != "success" or len(tasks) != 19 or any(task["state"] != "success" for task in tasks):
                raise RuntimeError("Main DAG scheduler tasks failed; inspect retained task logs")
            mapping = {task["task_id"]: task for task in tasks}
            if not (mapping["quality_batch"]["end_date"] <= mapping["quality_energy_coverage"]["start_date"] and
                    mapping["quality_energy_coverage"]["end_date"] <= mapping["load_warehouse"]["start_date"]):
                raise RuntimeError("Scheduled load started before quality gates completed")
            output = project / "output"
            query_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.glob("Q*.csv")}
            if len(query_hashes) != 29 or not (output / "report.html").is_file():
                raise RuntimeError("Scheduled report or 29 queries are missing")
            count = sum(1 for _ in csv.DictReader((output / "clean_batch_energy.csv").open(encoding="utf-8-sig")))
            totals = warehouse()
            if totals["active_rows"] != count:
                raise RuntimeError("Warehouse rows differ from clean batch")
            snapshot = {"query_sha256": query_hashes, "warehouse": totals}
            if baseline is None:
                baseline = snapshot
            elif baseline != snapshot:
                raise RuntimeError("Identical scheduler replay changed query results or warehouse totals")
            record.update(snapshot, quality_before_load=True)
            print(f"PIPELINE_SCHEDULER_PHASE_PASS {phase}: 19 tasks", flush=True)
        report.update(success=True, identical_replay_verified=True)
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if started:
            try:
                command(["docker", "stop", container])
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        (ROOT / "output/extensions/pipeline_scheduler_runtime.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "runs"},ensure_ascii=False,indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
