# -*- coding: utf-8 -*-
"""Fetch a public real-world industrial energy dataset with robots checks.

The source is UCI's "Steel Industry Energy Consumption" dataset (id=851).
The script deliberately checks robots.txt before requesting the archive and
writes provenance beside the extracted CSV.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BASE_DIR / "data" / "real" / "uci_steel_energy"
DATASET_URL = (
    "https://archive.ics.uci.edu/static/public/851/"
    "steel+industry+energy+consumption.zip"
)
DATASET_PAGE = (
    "https://archive.ics.uci.edu/dataset/851/"
    "steel%2Bindustry%2Benergy%2Bconsumption"
)
DOI = "https://doi.org/10.24432/C52G8C"
USER_AGENT = "industrial-energy-analysis-research/1.0"
EXPECTED_MEMBER = "Steel_industry_data.csv"
EXPECTED_ROWS = 35_040


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def check_robots(url: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
    )
    status = None
    body = ""
    try:
        with urllib.request.urlopen(request(robots_url), timeout=30) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status not in (401, 403, 404, 410):
            raise RuntimeError(f"robots.txt request failed: HTTP {status}") from exc

    # RFC 9309: 4xx other than 429 means robots.txt is unavailable and the
    # crawler may access resources; 401/403 are treated conservatively here.
    if status in (401, 403):
        allowed = False
        interpretation = "access denied by robots endpoint; stop"
    elif status in (404, 410):
        allowed = True
        interpretation = "robots.txt unavailable; no crawl rules published"
    else:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(body.splitlines())
        allowed = parser.can_fetch(USER_AGENT, url)
        interpretation = "allowed by published robots.txt" if allowed else "disallowed"

    return {
        "url": robots_url,
        "http_status": status,
        "allowed": allowed,
        "interpretation": interpretation,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_csv(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = 0
        first_timestamp = None
        last_timestamp = None
        load_types: set[str] = set()
        for row in reader:
            rows += 1
            timestamp = datetime.strptime(row["date"], "%d/%m/%Y %H:%M")
            first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
            last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)
            if row.get("Load_Type"):
                load_types.add(row["Load_Type"])

    if rows != EXPECTED_ROWS:
        raise RuntimeError(f"expected {EXPECTED_ROWS:,} rows, got {rows:,}")
    required = {"date", "Usage_kWh", "CO2(tCO2)", "Load_Type"}
    missing = required.difference(fieldnames)
    if missing:
        raise RuntimeError(f"missing expected columns: {sorted(missing)}")
    return {
        "rows": rows,
        "columns": len(fieldnames),
        "fieldnames": fieldnames,
        "first_timestamp": first_timestamp.strftime("%d/%m/%Y %H:%M"),
        "last_timestamp": last_timestamp.strftime("%d/%m/%Y %H:%M"),
        "load_types": sorted(load_types),
    }


def main() -> int:
    robots = check_robots(DATASET_URL)
    print(
        f"robots: HTTP {robots['http_status']} - {robots['interpretation']}"
    )
    if not robots["allowed"]:
        raise SystemExit("download stopped because robots policy does not allow it")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / EXPECTED_MEMBER
    with tempfile.TemporaryDirectory(prefix="uci-steel-energy-") as temp_dir:
        archive_path = Path(temp_dir) / "dataset.zip"
        with urllib.request.urlopen(request(DATASET_URL), timeout=60) as response:
            if response.status != 200:
                raise RuntimeError(f"dataset request failed: HTTP {response.status}")
            with archive_path.open("wb") as output:
                shutil.copyfileobj(response, output)

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if EXPECTED_MEMBER not in names:
                raise RuntimeError(
                    f"archive does not contain {EXPECTED_MEMBER}; members={names}"
                )
            with archive.open(EXPECTED_MEMBER) as source, csv_path.open("wb") as output:
                shutil.copyfileobj(source, output)
        archive_hash = sha256(archive_path)

    profile = inspect_csv(csv_path)
    provenance = {
        "dataset": "Steel Industry Energy Consumption",
        "dataset_page": DATASET_PAGE,
        "download_url": DATASET_URL,
        "doi": DOI,
        "license": "CC BY 4.0",
        "publisher": "UCI Machine Learning Repository",
        "original_context": "DAEWOO Steel Co. Ltd, Gwangyang, South Korea",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "user_agent": USER_AGENT,
        "robots": robots,
        "archive_sha256": archive_hash,
        "csv_sha256": sha256(csv_path),
        "profile": profile,
    }
    provenance_path = OUT_DIR / "provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"saved: {csv_path}")
    print(f"rows: {profile['rows']:,}; columns: {profile['columns']}")
    print(f"provenance: {provenance_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
