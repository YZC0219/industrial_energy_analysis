"""Ensure scheduler probes cannot route dimension writes to existing storage."""
import pytest
from datax.run_sync import validate_probe_destination
from tools.run_remote_lakehouse import remote_environment

STAMP = '20261001090000'
TARGET = 'energy_cdc_business_probe_' + STAMP
SOURCE = 'industrial_energy_cdc_business_' + STAMP


def test_cdc_probe_destinations_must_match():
    assert validate_probe_destination(TARGET, 'jdbc:mysql://localhost:13307/' + SOURCE,
                                      '/warehouse/' + TARGET) == TARGET
    for source, stage in [('industrial_energy', '/warehouse/' + TARGET),
                          (SOURCE, '/warehouse/energy_ods')]:
        with pytest.raises(ValueError):
            validate_probe_destination(TARGET, 'jdbc:mysql://localhost/' + source, stage)


def test_remote_probe_environment_requires_matching_source():
    values = {'LAKEHOUSE_REMOTE_MYSQL_HOST': '127.0.0.1', 'MYSQL_DB': SOURCE,
              'MYSQL_USER': 'reader', 'MYSQL_PASSWORD': 'secret', 'CDC_TARGET_DATABASE': TARGET}
    assert remote_environment(values)['HIVE_STAGE_PATH'] == '/warehouse/' + TARGET
    with pytest.raises(ValueError):
        remote_environment({**values, 'MYSQL_DB': 'industrial_energy'})
