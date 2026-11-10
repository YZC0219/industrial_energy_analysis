import pytest
from governance import catalog as catalog_module
from governance import mysql_source
from governance.consumers import read_artifact


def test_mysql_source_needs_no_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv('ENERGY_DATA_BACKEND', 'mysql')
    entry = catalog_module.catalog()['queries'][0]
    expected = [{column['name']: '1' for column in entry['columns']}]
    monkeypatch.setattr(mysql_source, 'analytical_rows', lambda *args: expected)
    assert read_artifact(entry['artifact'], tmp_path) == expected


def test_mysql_failure_does_not_fall_back_to_csv(monkeypatch, tmp_path):
    monkeypatch.setenv('ENERGY_DATA_BACKEND', 'mysql')
    entry = catalog_module.catalog()['queries'][0]
    (tmp_path / entry['artifact']).write_text('stale,data\n1,2', encoding='utf-8')
    def fail(*args):
        raise OSError('Database offline')
    monkeypatch.setattr(mysql_source, 'analytical_rows', fail)
    with pytest.raises(OSError, match='Database offline'):
        read_artifact(entry['artifact'], tmp_path)


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv('ENERGY_DATA_BACKEND', 'typo')
    with pytest.raises(ValueError):
        mysql_source.enabled()
