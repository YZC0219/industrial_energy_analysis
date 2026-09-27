"""Guard the optional SSH execution path without requiring a VM in CI."""

from pathlib import Path

import pytest

from tools.run_remote_lakehouse import (
    checkout_sha, redact, remote_environment, remote_script,
)


SHA = "a" * 40


def test_checkout_sha_reads_only_a_local_commit_ref(tmp_path: Path):
    ref = tmp_path / ".git" / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    ref.write_text(SHA + "\n")
    assert checkout_sha(tmp_path) == SHA
    ref.write_text("not-a-commit\n")
    with pytest.raises(ValueError):
        checkout_sha(tmp_path)


def test_remote_environment_uses_vm_mysql_endpoint_and_validates_it():
    source = {"LAKEHOUSE_REMOTE_MYSQL_HOST": "192.168.21.1",
              "LAKEHOUSE_REMOTE_MYSQL_PORT": "3307", "MYSQL_DB": "industrial_energy",
              "MYSQL_USER": "demo", "MYSQL_PASSWORD": "secret"}
    environment = remote_environment(source)
    assert environment["MYSQL_JDBC_URL"] == (
        "jdbc:mysql://192.168.21.1:3307/industrial_energy"
    )
    assert environment["HDFS_DEFAULT_FS"] == "hdfs://localhost:9000"
    with pytest.raises(ValueError):
        remote_environment({**source, "LAKEHOUSE_REMOTE_MYSQL_PORT": "-1"})


def test_remote_script_pins_checkout_and_quotes_credentials():
    script = remote_script("hostname; whoami", project="/home/yzc/project",
                           expected_sha=SHA, variables={"MYSQL_PASSWORD": "don't print"})
    assert "git rev-parse HEAD" in script
    assert "export MYSQL_PASSWORD='don'\"'\"'t print'" in script
    assert script.endswith("hostname; whoami\n")
    assert redact("password=don't print", ("don't print",)) == "password=***"
    with pytest.raises(ValueError):
        remote_script("hostname", project="/warehouse/energy_ods", expected_sha=SHA,
                      variables={})
