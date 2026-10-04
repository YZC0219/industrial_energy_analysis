"""Reproduce Iceberg on D-backed WSL paths without installing Linux-wide packages."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def wsl_path(path):
    path = path.resolve()
    if path.drive.upper() != "D:":
        raise ValueError("This runtime verifier requires D-backed paths")
    return "/mnt/d/" + path.as_posix()[3:]


def main():
    spark = ROOT / "dependencies/spark"
    java = ROOT / "dependencies/jdk17-linux"
    jar = ROOT / "dependencies/iceberg-spark-runtime-3.5_2.12-1.9.1.jar"
    for path in (spark / "bin/spark-submit", java / "bin/java", jar):
        if not path.is_file():
            raise FileNotFoundError(f"Prepare the runtime on D first: {path}")
    # Maven Central checksum, checked before loading third-party code.
    sha1 = hashlib.sha1(jar.read_bytes()).hexdigest()
    if sha1 != "577f2d40043ab6af4387eef5e856bdde06a931e6":
        raise ValueError("Iceberg jar checksum mismatch")
    conf = ROOT / "dependencies/spark-poc-conf"
    scratch = ROOT / "tmp/iceberg-spark-local"
    output = ROOT / "output/extensions"
    for path in (conf, scratch, output):
        path.mkdir(parents=True, exist_ok=True)
    local_tmp = wsl_path(scratch)
    command = ["wsl", "-d", "Ubuntu", "--exec", "env",
               "JAVA_HOME=" + wsl_path(java), "SPARK_CONF_DIR=" + wsl_path(conf),
               "HADOOP_CONF_DIR=" + wsl_path(conf), "SPARK_LOCAL_DIRS=" + local_tmp,
               "TMPDIR=" + local_tmp, "PYSPARK_PYTHON=/usr/bin/python3", "SPARK_LOCAL_IP=127.0.0.1",
               wsl_path(spark / "bin/spark-submit"), "--master", "local[2]", "--driver-memory", "1g",
               "--conf", "spark.ui.enabled=false", "--conf", "spark.hadoop.fs.defaultFS=file:///",
               "--conf", f"spark.driver.extraJavaOptions=-Djava.io.tmpdir={local_tmp}",
               "--conf", f"spark.executor.extraJavaOptions=-Djava.io.tmpdir={local_tmp}",
               "--jars", wsl_path(jar), wsl_path(ROOT / "lakehouse/iceberg_poc.py"),
               "--warehouse", "file://" + wsl_path(output / "iceberg_warehouse"),
               "--output", wsl_path(output / "iceberg_report.json")]
    with (output / "iceberg_runtime.log").open("wb") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError("Iceberg failed; inspect output/extensions/iceberg_runtime.log")
    report_path = output / "iceberg_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update(iceberg_version="1.9.1", jar_sha1=sha1,
                  jar_sha256=hashlib.sha256(jar.read_bytes()).hexdigest(),
                  runtime="WSL Ubuntu / Spark local[2]", warehouse_storage="D drive",
                  generated_at=datetime.now(timezone.utc).isoformat())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
