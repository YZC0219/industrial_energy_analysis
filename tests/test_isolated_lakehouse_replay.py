"""Guard the isolated execution boundary for production lakehouse SQL."""

import pytest

from tools.check_isolated_lakehouse_replay import isolated_sql, validate_database


DB = "energy_contract_probe_20260927"


def test_probe_database_must_be_new_narrowly_named_namespace():
    assert validate_database(DB) == DB
    for name in ("energy_dwd", "energy_contract_probe", "energy_contract_probe_2026-09-27",
                 "energy_contract_probe_20260927.other", "energy_contract_probe_20260927;DROP"):
        with pytest.raises(ValueError):
            validate_database(name)


def test_sql_rewrites_all_production_schemas_and_storage_locations():
    sql = "\n".join(
        f"CREATE TABLE {name}.example (id INT) LOCATION '/warehouse/{name}/example';"
        for name in ("energy_ods", "energy_dwd", "energy_dws", "energy_ads")
    )
    isolated = isolated_sql(sql, DB)
    assert isolated.count(f"{DB}.example") == 4
    assert isolated.count(f"hdfs://localhost:9000/warehouse/{DB}/example") == 4
    assert "energy_ods." not in isolated
    assert "energy_dwd." not in isolated
    assert "energy_dws." not in isolated
    assert "energy_ads." not in isolated


@pytest.mark.parametrize("sql", [
    "CREATE TABLE x LOCATION '/warehouse/energy_other/escape'",
    "CREATE TABLE x LOCATION 'file:/tmp/escape'",
])
def test_sql_refuses_unmapped_table_locations(sql):
    with pytest.raises(ValueError, match="non-isolated"):
        isolated_sql(sql, DB)
