"""本地数仓路径约定（全部落在 D 盘项目 output 下）。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAKE_ROOT = PROJECT_ROOT / "output" / "data_collection"


def ensure_layout() -> dict[str, Path]:
    paths = {
        "root": LAKE_ROOT,
        "ods": LAKE_ROOT / "ods",
        "dwd": LAKE_ROOT / "dwd",
        "reports": LAKE_ROOT / "reports",
        "meta": LAKE_ROOT / "meta",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def batch_dir(layer: str, batch_id: str) -> Path:
    base = ensure_layout()[layer]
    out = base / batch_id
    out.mkdir(parents=True, exist_ok=True)
    return out
