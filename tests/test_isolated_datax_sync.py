"""Validate the reproducible DataX probe contract without requiring a cluster."""

import pytest

from tools.verify_isolated_datax_sync import probe_names, source_ddl


def test_probe_names_are_narrow_and_pair_source_with_target():
    assert probe_names("20260927") == (
        "industrial_energy_datax_probe_20260927", "energy_datax_probe_20260927"
    )
    for invalid in ("2026-09-27", "20260927;DROP", "energy_ods", ""):
        with pytest.raises(ValueError):
            probe_names(invalid)


def test_mysql_fixture_has_timestamp_and_tombstone_columns():
    sql = source_ddl("industrial_energy_datax_probe_20260927")
    assert "updated_at DATETIME(6) NOT NULL" in sql
    assert "is_deleted TINYINT NOT NULL DEFAULT 0" in sql
    assert "2024-03-15" in sql
    assert "industrial_energy.fact_energy_consumption" not in sql
    with pytest.raises(ValueError):
        source_ddl("industrial_energy")
