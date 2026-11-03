"""将 ODS 嵌套 JSON 展平为 Spark 友好的 hourly parquet/jsonl。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cleaning.rules import AQ_COLS, WEATHER_COLS  # noqa: E402
from lakehouse_local.paths import ensure_layout  # noqa: E402
from preprocess.pipeline import load_ods  # noqa: E402


def flatten(ods_dir: Path) -> Path:
    flat_dir = ods_dir / "flat"
    flat_dir.mkdir(parents=True, exist_ok=True)
    df = load_ods(ods_dir)
    df["ts_local"] = pd.to_datetime(df["ts_local"], errors="coerce")
    out_parquet = flat_dir / "hourly_raw.parquet"
    out_csv = flat_dir / "hourly_raw.csv"
    df.to_parquet(out_parquet, index=False)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    meta = {
        "rows": len(df),
        "cities": sorted(df["city_id"].dropna().unique().tolist()),
        "weather_cols": [c for c in WEATHER_COLS if c in df.columns],
        "aq_cols": [c for c in AQ_COLS if c in df.columns],
        "parquet": str(out_parquet),
        "csv": str(out_csv),
    }
    (flat_dir / "flat_manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[flat] rows={len(df)} → {out_parquet}")
    return out_parquet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ods-dir", default="")
    args = parser.parse_args()
    if args.ods_dir:
        ods = Path(args.ods_dir)
    else:
        meta = ensure_layout()["meta"] / "latest_ods_batch.json"
        ods = Path(json.loads(meta.read_text(encoding="utf-8"))["ods_dir"])
    flatten(ods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
