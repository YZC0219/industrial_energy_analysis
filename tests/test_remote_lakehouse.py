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
    assert "HDFS_REPLICATION" not in environment
    with pytest.raises(ValueError):
        remote_environment({**source, "LAKEHOUSE_REMOTE_MYSQL_PORT": "-1"})


def test_remote_spark_config_selects_existing_catalog_explicitly():
    source = {'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1', 'MYSQL_USER': 'reader',
              'MYSQL_PASSWORD': 'secret',
              'LAKEHOUSE_REMOTE_SPARK_CONF_DIR': '/home/yzc/cdc_main/conf'}
    assert remote_environment(source)['SPARK_CONF_DIR'] == '/home/yzc/cdc_main/conf'
    with pytest.raises(ValueError):
        remote_environment({**source, 'LAKEHOUSE_REMOTE_SPARK_CONF_DIR': '/home/yzc/../other'})


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


def test_remote_cluster_endpoints_are_explicit_and_validated():
    source = {'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1', 'MYSQL_USER': 'reader',
              'MYSQL_PASSWORD': 'secret',
              'LAKEHOUSE_REMOTE_HADOOP_CONF_DIR': '/home/yzc/energy-cluster/conf',
              'LAKEHOUSE_REMOTE_HDFS_DEFAULT_FS': 'hdfs://192.168.21.131:19000',
              'CDC_SPARK_MASTER': 'spark://192.168.21.131:7077'}
    result = remote_environment(source)
    assert result['HDFS_DEFAULT_FS'] == 'hdfs://192.168.21.131:19000'
    assert result['HADOOP_CONF_DIR'] == '/home/yzc/energy-cluster/conf'
    assert result['CDC_SPARK_MASTER'] == 'spark://192.168.21.131:7077'
    for bad in ('file:///tmp/data', 'hdfs://host:99999', 'hdfs://host:1;id'):
        with pytest.raises(ValueError):
            remote_environment({**source, 'LAKEHOUSE_REMOTE_HDFS_DEFAULT_FS': bad})
    with pytest.raises(ValueError):
        remote_environment({**source, 'LAKEHOUSE_REMOTE_HADOOP_CONF_DIR': '/home/yzc/../old'})


@pytest.mark.parametrize('replication', ['1', '2', '512'])
def test_remote_hdfs_replication_is_forwarded_explicitly(replication):
    source = {'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1', 'MYSQL_USER': 'reader',
              'MYSQL_PASSWORD': 'secret', 'LAKEHOUSE_REMOTE_HDFS_REPLICATION': replication}
    result = remote_environment(source)
    assert result['HDFS_REPLICATION'] == replication
    assert f'export HDFS_REPLICATION={replication}\n' in remote_script(
        'true', project='/home/yzc/project', expected_sha=SHA, variables=result)


@pytest.mark.parametrize('replication', ['', '0', '-1', '513', '1.0', '1\n', '1;id', '１'])
def test_remote_hdfs_replication_rejects_invalid_values(replication):
    source = {'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1', 'MYSQL_USER': 'reader',
              'MYSQL_PASSWORD': 'secret', 'LAKEHOUSE_REMOTE_HDFS_REPLICATION': replication}
    with pytest.raises(ValueError, match='HDFS_REPLICATION'):
        remote_environment(source)
