"""Content-addressed freshness manifest for phase-two artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(path: str | Path, inputs: dict[str, str | Path],
                   artifacts: dict[str, str | Path]) -> dict:
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {name: {"path": Path(value).resolve().as_posix(),
                          "sha256": sha256(value)} for name, value in inputs.items()},
        "artifacts": {name: {"path": Path(value).resolve().as_posix(),
                             "sha256": sha256(value)} for name, value in artifacts.items()},
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def verify_manifest(path: str | Path, inputs: dict[str, str | Path],
                    artifacts: dict[str, str | Path]) -> None:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    for section, expected in (("inputs", inputs), ("artifacts", artifacts)):
        recorded = manifest.get(section, {})
        if ((section == "inputs" and set(recorded) != set(expected))
                or (section == "artifacts" and not set(expected).issubset(recorded))):
            raise SystemExit(f"freshness manifest {section} 清单不匹配")
        for name, value in expected.items():
            path_value = Path(value).resolve().as_posix()
            if recorded[name].get("path") != path_value or recorded[name].get("sha256") != sha256(value):
                raise SystemExit(f"{section}/{name} 已变化，阶段二产物过期或来源不符")
