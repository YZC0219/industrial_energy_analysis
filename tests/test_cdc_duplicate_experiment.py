import json

import pytest

from tools import experiment_cdc_duplicate as experiment
from tools.export_mysql_cdc_batch import project


def test_duplicate_experiment_saves_oracle_inputs_and_reconciliation(tmp_path):
    directory = tmp_path / 'evidence'
    result = experiment.run(directory)
    assert result['status'] == 'projection_passed_warehouse_not_run'
    assert result['warehouse']['status'] == 'not_run'
    assert result['live_cdc']['status'] == 'not_run'
    original = json.loads((directory / 'original.json').read_text())
    repeated = json.loads((directory / 'duplicate.json').read_text())
    assert original['end_offset'] == 3
    assert repeated['end_offset'] == 6
    assert repeated['rows'] == original['rows']
    assert repeated['row_offsets'] == original['row_offsets']
    assert result['projection']['negative_control']['naive_cost'] == '94.00'
    archived = json.loads((directory / 'report.json').read_text())
    for filename, sha in archived['artifacts_sha256'].items():
        import hashlib
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == sha
    with pytest.raises(FileExistsError):
        experiment.run(directory)


def test_same_binlog_version_with_changed_image_is_not_a_duplicate():
    original, _ = experiment.fixtures()
    from copy import deepcopy
    conflict = deepcopy(original[-1])
    conflict['offset'] = 3
    conflict['value']['after']['cost'] = '99.00'
    with pytest.raises(ValueError, match='Conflicting images'):
        project(original + [conflict], experiment.TOPIC, 4)


def test_oracle_catches_wrong_projector_and_preserves_failure(tmp_path, monkeypatch):
    real = experiment.project

    def broken(*args):
        snapshot = real(*args)
        snapshot['rows'][0]['cost'] = '99.00'
        return snapshot

    monkeypatch.setattr(experiment, 'project', broken)
    directory = tmp_path / 'failed'
    with pytest.raises(ValueError, match='Reconciliation failed'):
        experiment.run(directory)
    archived = json.loads((directory / 'report.json').read_text())
    assert archived['status'] == 'failed'
    assert (directory / 'expected.json').exists()
    assert (directory / 'input.json').exists()


@pytest.mark.parametrize('database', [None, 'energy', 'energy_cdc_business_probe_bad'])
def test_warehouse_rejects_unsafe_namespace_before_creating_output(tmp_path, database):
    directory = tmp_path / 'unsafe'
    with pytest.raises(ValueError, match='fresh'):
        experiment.run(directory, warehouse=True, database=database)
    assert not directory.exists()
