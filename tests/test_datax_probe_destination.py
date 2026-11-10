"""The optional DataX probe must not write into production ODS paths."""

import json
import sys

import pytest

from datax.run_sync import apply_hdfs_replication, main, validate_probe_destination


DB = "energy_datax_probe_20260927"
JDBC = "jdbc:mysql://192.168.21.1:3307/industrial_energy_datax_probe_20260927"
STAGE = f"/warehouse/{DB}"


def test_probe_destination_requires_matching_database_source_and_path():
    assert validate_probe_destination(DB, JDBC, STAGE) == DB
    for database, jdbc, stage in (
        ("energy_ods", JDBC, STAGE),
        ("energy_datax_probe_20260927;DROP", JDBC, STAGE),
        (DB, "jdbc:mysql://host/industrial_energy", STAGE),
        (DB, JDBC, "/warehouse/energy_ods"),
    ):
        with pytest.raises(ValueError):
            validate_probe_destination(database, jdbc, stage)


def test_probe_dry_run_renders_isolated_fact_sync(monkeypatch, capsys):
    monkeypatch.setenv("MYSQL_USER", "probe")
    monkeypatch.setenv("MYSQL_PASSWORD", "top-secret")
    monkeypatch.setenv("MYSQL_JDBC_URL", JDBC)
    monkeypatch.setenv("HDFS_DEFAULT_FS", "hdfs://localhost:9000")
    monkeypatch.setenv("HIVE_STAGE_PATH", STAGE)
    monkeypatch.setattr(sys, "argv", [
        "run_sync.py", "--table", "fact_energy_consumption",
        "--biz-date", "2026-09-27", "--window-start", "2026-09-27 10:00:00",
        "--window-end", "2026-09-27 12:00:00", "--probe-database", DB,
        "--dry-run",
    ])
    main()
    rendered = capsys.readouterr().out
    assert "top-secret" not in rendered
    job = json.loads(rendered)["job"]["content"][0]
    assert job["reader"]["parameter"]["connection"][0]["jdbcUrl"] == [JDBC]
    assert job["writer"]["parameter"]["path"] == (
        f"{STAGE}/ods_energy_consumption/dt=2026-09-27"
    )


def test_skip_recover_cannot_disable_production_partition_registration(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "run_sync.py", "--table", "fact_energy_consumption",
        "--biz-date", "2026-09-27", "--window-start", "2026-09-27 10:00:00",
        "--window-end", "2026-09-27 12:00:00", "--skip-recover", "--dry-run",
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_replication_override_preserves_existing_hadoop_settings():
    payload = json.dumps({'job': {'content': [{'writer': {'name': 'hdfswriter', 'parameter': {
        'hadoopConfig': {'dfs.replication': '3', 'dfs.nameservices': 'warehouse'},
        'path': '/warehouse/energy_ods/ods_workshop/dt=current',
    }}}]}})
    assert apply_hdfs_replication(payload, None) == payload
    result = json.loads(apply_hdfs_replication(payload, '1'))
    parameters = result['job']['content'][0]['writer']['parameter']
    assert parameters['hadoopConfig'] == {'dfs.replication': '1', 'dfs.nameservices': 'warehouse'}
    assert parameters['path'] == '/warehouse/energy_ods/ods_workshop/dt=current'


@pytest.mark.parametrize('replication', ['', '0', '-1', '513', '1.0', '1\n', '1;id', '１'])
def test_replication_override_rejects_invalid_values(replication):
    with pytest.raises(ValueError, match='HDFS_REPLICATION'):
        apply_hdfs_replication('{}', replication)


@pytest.mark.parametrize('full,replication', [(False, '1'), (True, '512')])
def test_replication_override_reaches_rendered_datax_job(monkeypatch, capsys, full, replication):
    monkeypatch.setenv('MYSQL_USER', 'probe')
    monkeypatch.setenv('MYSQL_PASSWORD', 'top-secret')
    monkeypatch.setenv('HDFS_REPLICATION', replication)
    arguments = ['run_sync.py', '--table', 'fact_energy_consumption',
                 '--biz-date', '2026-11-03', '--dry-run']
    arguments += ['--full'] if full else ['--window-start', '2026-11-03 00:00:00',
                                        '--window-end', '2026-11-04 00:00:00']
    monkeypatch.setattr(sys, 'argv', arguments)
    main()
    rendered = capsys.readouterr().out
    assert 'top-secret' not in rendered
    job = json.loads(rendered)['job']['content'][0]
    assert job['writer']['parameter']['hadoopConfig']['dfs.replication'] == replication
