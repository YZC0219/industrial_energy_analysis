"""Execute one trusted DAG lakehouse command on a pinned SSH VM.

The private key and known_hosts are mounted read-only. Credentials travel via
the SSH channel's stdin, never through a shell command argument or log line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def checkout_sha(root: Path = ROOT) -> str:
    head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref = head[5:]
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", ref) or ".." in ref:
            raise ValueError("unsafe Git HEAD ref")
        ref_file = root / ".git" / ref
        if not ref_file.is_file():
            raise ValueError("Git HEAD ref is not a loose ref; set up a full checkout")
        head = ref_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("cannot resolve local Git commit")
    return head


def remote_environment(environ: dict[str, str]) -> dict[str, str]:
    mysql_host = environ["LAKEHOUSE_REMOTE_MYSQL_HOST"]
    mysql_port = environ.get("LAKEHOUSE_REMOTE_MYSQL_PORT", "3307")
    mysql_db = environ.get("MYSQL_DB", "industrial_energy")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", mysql_host):
        raise ValueError("invalid remote MySQL host")
    if not mysql_port.isdigit() or not 1 <= int(mysql_port) <= 65535:
        raise ValueError("invalid remote MySQL port")
    if not re.fullmatch(r"[A-Za-z0-9_]+", mysql_db):
        raise ValueError("invalid MySQL database")
    variables = {
        "MYSQL_HOST": mysql_host,
        "MYSQL_PORT": mysql_port,
        "MYSQL_USER": environ["MYSQL_USER"],
        "MYSQL_PASSWORD": environ["MYSQL_PASSWORD"],
        "MYSQL_DB": mysql_db,
        "MYSQL_JDBC_URL": f"jdbc:mysql://{mysql_host}:{mysql_port}/{mysql_db}",
        "HADOOP_CONF_DIR": "/usr/local/hadoop/etc/hadoop",
        "HDFS_DEFAULT_FS": "hdfs://localhost:9000",
        "HIVE_STAGE_PATH": "/warehouse/energy_ods",
        "HDFS_BIN": "/usr/local/hadoop/bin/hdfs",
        "SPARK_SQL": "/usr/local/spark/bin/spark-sql",
        "DATAX_ENTRY": "/home/yzc/apps/datax/bin/datax.py",
        "DATAX_PYTHON": "/usr/bin/python3",
    }
    for key in ('CDC_PYSPARK_PYTHON', 'CDC_SPARK_MASTER', 'SPARK_SUBMIT'):
        if environ.get(key):
            variables[key] = environ[key]
    return variables


def remote_script(command: str, *, project: str, expected_sha: str,
                  variables: dict[str, str]) -> str:
    if not project.startswith("/home/") or "\n" in project:
        raise ValueError("remote project must be a /home/... checkout")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("invalid expected commit")
    if not command.strip() or "\x00" in command:
        raise ValueError("empty or invalid remote command")
    lines = [
        "set -euo pipefail",
        f"cd {shlex.quote(project)}",
        f"test \"$(git rev-parse HEAD)\" = {shlex.quote(expected_sha)} || "
        "{ echo 'VM checkout differs from Airflow checkout'; exit 73; }",
    ]
    lines.extend(f"export {key}={shlex.quote(value)}" for key, value in variables.items())
    lines.append(command)
    return "\n".join(lines) + "\n"


def redact(line: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            line = line.replace(secret, "***")
    return line


def cdc_input_path(path: Path) -> tuple[Path, str]:
    resolved = path.resolve()
    relative = resolved.relative_to(ROOT.resolve()).as_posix()
    if not re.fullmatch(r'output/mysql_cdc_snapshot_[A-Za-z0-9_-]+\.json', relative):
        raise ValueError('Only project output/mysql_cdc_snapshot_*.json may be uploaded')
    from tools.export_mysql_cdc_batch import project
    doc = json.loads(resolved.read_text(encoding='utf-8'))
    rebuilt = project(doc['events'], doc['topic'], doc['end_offset'])
    if any(doc.get(key) != value for key, value in rebuilt.items()):
        raise ValueError('CDC upload does not match complete source history')
    return resolved, relative


def upload_cdc_snapshot(client, path: Path, project: str, expected_sha: str) -> str:
    local, relative = cdc_input_path(path)
    # Check the remote checkout before writing even the new input file.
    check = remote_script('true', project=project, expected_sha=expected_sha, variables={})
    channel = client.get_transport().open_session()
    try:
        channel.exec_command('bash -se')
        channel.sendall(check.encode('utf-8'))
        channel.shutdown_write()
        if channel.recv_exit_status() != 0:
            raise ValueError('VM checkout differs; refusing CDC input upload')
    finally:
        channel.close()
    digest = hashlib.sha256(local.read_bytes()).hexdigest()
    destination = project.rstrip('/') + '/' + relative
    with client.open_sftp() as sftp:
        try:
            existing = sftp.open(destination, 'rb')
        except FileNotFoundError:
            existing = None
        if existing is not None:
            with existing:
                if hashlib.sha256(existing.read()).hexdigest() != digest:
                    raise ValueError('Remote CDC input exists with different bytes')
        else:
            import uuid
            temporary = destination + '.part-' + uuid.uuid4().hex
            sftp.put(str(local), temporary)
            with sftp.open(temporary, 'rb') as uploaded:
                if hashlib.sha256(uploaded.read()).hexdigest() != digest:
                    raise ValueError('Uploaded CDC input checksum differs')
            sftp.rename(temporary, destination)
    return f"printf '%s  %s\\n' {shlex.quote(digest)} {shlex.quote(relative)} | sha256sum -c - && "


def run(command: str, input_file: Path | None = None) -> int:
    import paramiko

    host = os.environ["LAKEHOUSE_SSH_HOST"]
    user = os.environ["LAKEHOUSE_SSH_USER"]
    key = Path(os.environ["LAKEHOUSE_SSH_KEY"])
    known_hosts = Path(os.environ["LAKEHOUSE_SSH_KNOWN_HOSTS"])
    project = os.environ["LAKEHOUSE_REMOTE_PROJECT"]
    if not key.is_file() or not known_hosts.is_file():
        raise ValueError("mounted SSH key and known_hosts must exist")
    variables = remote_environment(os.environ)
    expected_sha = checkout_sha()
    script = remote_script(command, project=project,
                           expected_sha=expected_sha, variables=variables)
    client = paramiko.SSHClient()
    client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(host, username=user, key_filename=str(key),
                       look_for_keys=False, allow_agent=False, timeout=10,
                       auth_timeout=10, banner_timeout=10)
        if input_file is not None:
            prefix = upload_cdc_snapshot(client, input_file, project, expected_sha)
            script = remote_script(prefix + command, project=project,
                                   expected_sha=expected_sha, variables=variables)
        channel = client.get_transport().open_session()
        channel.set_combine_stderr(True)
        channel.exec_command("bash -se")
        channel.sendall(script.encode("utf-8"))
        channel.shutdown_write()
        pending = ""
        secrets = (variables["MYSQL_PASSWORD"],)
        try:
            while not channel.exit_status_ready() or channel.recv_ready():
                if channel.recv_ready():
                    pending += channel.recv(65536).decode("utf-8", errors="replace")
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        print(redact(line, secrets), flush=True)
                else:
                    time.sleep(0.05)
            if pending:
                print(redact(pending, secrets), flush=True)
            return channel.recv_exit_status()
        finally:
            channel.close()
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    parser.add_argument('--input', type=Path, help='validated immutable CDC snapshot to upload before execution')
    parser.add_argument("--report", type=Path,
                        help="optional non-secret JSON outcome for a read-only smoke check")
    args = parser.parse_args()
    exit_code = run(args.command, args.input)
    if args.report:
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "ssh_host": os.environ["LAKEHOUSE_SSH_HOST"],
            "ssh_user": os.environ["LAKEHOUSE_SSH_USER"],
            "expected_sha": checkout_sha(),
            "exit_code": exit_code,
            "success": exit_code == 0,
            "scope": "pinned SSH adapter smoke; command output is not stored",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
