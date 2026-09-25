"""Offline guards for the opt-in Metabase stack and least-privilege grants."""
import pytest

from tools.bootstrap_bi_local import add_viewer, bootstrap, rotate_reader
from tools.setup_bi_reader import grant_bi_reader


class FakeCursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.calls.append((statement, params))


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.committed = False

    def cursor(self):
        return FakeCursor(self.calls)

    def commit(self):
        self.committed = True


def test_bi_reader_is_limited_to_enriched_view_select_privileges():
    conn = FakeConnection()

    grant_bi_reader(conn, "industrial_energy", "energy_bi_reader", "a-strong-password-123")

    grant_sql = conn.calls[-1][0]
    assert "'energy_bi_reader'@'%%'" in conn.calls[0][0]
    assert "'energy_bi_reader'@'%%'" in conn.calls[1][0]
    assert "'energy_bi_reader'@'%'" in grant_sql
    assert "GRANT SELECT, SHOW VIEW ON `industrial_energy`.`v_energy_enriched`" in grant_sql
    assert "fact_energy_consumption" not in grant_sql
    assert "UPDATE" not in grant_sql
    assert conn.committed


@pytest.mark.parametrize("database,username,password", [
    ("bad-db; DROP DATABASE x", "reader", "a-strong-password-123"),
    ("industrial_energy", "reader", "short"),
])
def test_bi_reader_rejects_unsafe_identifiers_or_weak_password(database, username, password):
    with pytest.raises(ValueError):
        grant_bi_reader(FakeConnection(), database, username, password)


def test_metabase_is_opt_in_pinned_and_loopback_only():
    from pathlib import Path

    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "metabase/metabase:v0.63.18" in compose
    assert "profiles: [bi]" in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert "metabase-db:/var/lib/postgresql/data" in compose


def test_local_bi_credentials_are_created_once_and_rotated_independently(tmp_path):
    env_file = tmp_path / ".env"
    bootstrap(env_file)
    initial = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                   if line and not line.startswith("#"))
    assert len(initial["BI_DB_PASSWORD"]) >= 16
    assert len(initial["METABASE_DB_PASSWORD"]) >= 16
    assert initial["BI_DB_PASSWORD"] != initial["METABASE_DB_PASSWORD"]
    with pytest.raises(FileExistsError):
        bootstrap(env_file)

    rotate_reader(env_file)
    rotated = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                   if line and not line.startswith("#"))
    assert rotated["BI_DB_PASSWORD"] != initial["BI_DB_PASSWORD"]
    assert all(rotated[key] == value for key, value in initial.items()
               if key != "BI_DB_PASSWORD")

    add_viewer(env_file)
    with_viewer = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                       if line and not line.startswith("#"))
    assert with_viewer["METABASE_VIEWER_EMAIL"]
    assert len(with_viewer["METABASE_VIEWER_PASSWORD"]) >= 16
    assert all(with_viewer[key] == value for key, value in rotated.items())
    with pytest.raises(ValueError):
        add_viewer(env_file)
