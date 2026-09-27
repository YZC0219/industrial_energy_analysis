"""Check the committed read-only Airflow-image-to-VM SSH smoke report."""

import json
import re
from pathlib import Path


REPORT = Path(__file__).resolve().parents[1] / "output" / "lakehouse_ssh_smoke_20260927.json"


def test_pinned_ssh_adapter_smoke_report_is_non_secret_and_successful():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert report["exit_code"] == 0
    assert report["ssh_host"] == "192.168.21.131"
    assert report["ssh_user"] == "yzc"
    assert re.fullmatch(r"[0-9a-f]{40}", report["expected_sha"])
    assert "password" not in REPORT.read_text(encoding="utf-8").lower()
