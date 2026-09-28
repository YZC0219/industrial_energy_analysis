"""Download the official ITAC database after checking robots.txt.

The archive and extracted workbook are kept under data/real/itac_2026 on D:.
No existing download is overwritten unless --refresh is explicitly supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from tools.fetch_real_industrial_data import USER_AGENT, check_robots


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://itac.university/storage/ITAC_Database.zip"
SOURCE_PAGE = "https://itac.university/download"
DEFAULT_DIR = ROOT / "data" / "real" / "itac_2026"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def extract_workbook(archive_path: Path, target_dir: Path, refresh: bool = False) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        members = [item for item in archive.infolist()
                   if not item.is_dir() and item.filename.lower().endswith(".xlsx")]
        if len(members) != 1:
            raise RuntimeError(f"预期压缩包恰有一个 XLSX 工作簿，实际 {len(members)} 个")
        member = members[0]
        # Never trust archive paths. Extract only a single workbook basename.
        name = Path(member.filename).name
        if name != member.filename:
            raise RuntimeError(f"不安全的工作簿路径：{member.filename}")
        target = target_dir / name
        if target.exists() and not refresh:
            return target
        part = target.with_suffix(target.suffix + ".part")
        with archive.open(member) as source, part.open("wb") as output:
            shutil.copyfileobj(source, output)
        part.replace(target)
        return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    robots = check_robots(SOURCE_URL)
    if not robots["allowed"]:
        raise SystemExit(f"robots.txt 不允许下载 {SOURCE_URL}：{robots['interpretation']}")

    target_dir = args.output_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / "ITAC_Database.zip"
    if archive_path.exists() and not args.refresh:
        workbook_path = extract_workbook(archive_path, target_dir)
        provenance_path = target_dir / "provenance.json"
        if provenance_path.exists():
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if digest(archive_path) != provenance["archive_sha256"]:
                raise RuntimeError("已有压缩包的 SHA-256 与来源记录不一致")
            provenance["workbook_sha256"] = digest(workbook_path)
            provenance_path.write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(f"已有数据，保留原文件：{archive_path}；工作簿：{workbook_path}")
        return

    part_path = target_dir / "ITAC_Database.zip.part"
    class CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            redirected_robots = check_robots(newurl)
            if not redirected_robots["allowed"]:
                raise RuntimeError(f"robots.txt 不允许重定向目标：{newurl}")
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(CheckedRedirect())
    with opener.open(request, timeout=120) as response, part_path.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"下载失败：HTTP {response.status}")
        shutil.copyfileobj(response, output)
    with zipfile.ZipFile(part_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"压缩包损坏：{bad_member}")
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members or not any(item.filename.lower().endswith(".xlsx") for item in members):
            raise RuntimeError("压缩包内没有 XLSX 工作簿")
        manifest = [{"name": item.filename, "size_bytes": item.file_size} for item in members]
    part_path.replace(archive_path)
    workbook_path = extract_workbook(archive_path, target_dir, refresh=args.refresh)
    provenance = {
        "source_url": SOURCE_URL,
        "source_page": SOURCE_PAGE,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "robots": robots,
        "archive_sha256": digest(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "workbook_sha256": digest(workbook_path),
        "members": manifest,
        "note": "Official public ITAC assessment/recommendation download; source estimates and implementation status are not metered post-intervention outcomes.",
    }
    (target_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"archive": str(archive_path), "workbook": str(workbook_path), "members": manifest,
                      "sha256": provenance["archive_sha256"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
