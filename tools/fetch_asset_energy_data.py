"""Fetch a public mirror of measured asset-level factory energy data.

Checks robots.txt for the repository and every redirect host before a download.
Stores files only under data/real/asset_energy on D: and records SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tools.fetch_real_industrial_data import USER_AGENT, check_robots


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "real" / "asset_energy"
BASE = "https://huggingface.co/datasets/renumics/industrial-asset-level-electrical-energy-dataset/resolve/main/"
PAGE = "https://huggingface.co/datasets/renumics/industrial-asset-level-electrical-energy-dataset"
SOURCE_DOI = "https://doi.org/10.5281/zenodo.19180972"
FILES = {
    "dim/dim_asset.parquet": "b0474d422ad117b39b251ac81165562529e67f70f31156e9ccf79d8436293574",
    "dim/dim_signal_info.parquet": None,
    "dim/dim_observation_window.parquet": None,
    "fact/fact_data_quality_event.parquet": None,
    "fact/fact_energy_15m.parquet": "c4946cd9909cbce5d215e261ce27a702b131b6337dfac69bae7ee9ff4148d19a",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    robots_checks = {}

    def require_allowed(url: str) -> None:
        result = check_robots(url)
        robots_checks[result["url"]] = result
        if not result["allowed"]:
            raise RuntimeError(f"robots.txt 禁止目标路径：{result['url']}")

    class CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            require_allowed(newurl)
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(CheckedRedirect())
    OUT.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for relative, expected_hash in FILES.items():
        source = BASE + relative
        require_allowed(source)
        target = OUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            actual_hash = sha256(target)
            if expected_hash and actual_hash != expected_hash:
                raise RuntimeError(f"已有文件的 SHA-256 不符：{relative}")
        else:
            part = target.with_suffix(target.suffix + ".part")
            request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
            with opener.open(request, timeout=180) as response, part.open("wb") as output:
                if response.status != 200:
                    raise RuntimeError(f"下载失败：{relative} HTTP {response.status}")
                shutil.copyfileobj(response, output)
            actual_hash = sha256(part)
            if expected_hash and actual_hash != expected_hash:
                raise RuntimeError(f"下载文件的 SHA-256 不符：{relative}")
            part.replace(target)
        downloaded.append({"file": relative, "size_bytes": target.stat().st_size,
                           "sha256": actual_hash})
        print(f"已核验 {relative}: {target.stat().st_size:,} bytes")
    provenance = {
        "source_page": PAGE,
        "source_doi": SOURCE_DOI,
        "license": "CC BY 4.0 (as stated in source data card and article)",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "robots_checks": list(robots_checks.values()),
        "files": downloaded,
        "note": "Mirror of Flynn et al. 2026 Gold-layer measured electricity data; no production volume, intervention, or measured post-project savings.",
    }
    (OUT / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
