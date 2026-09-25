"""Read-only FastAPI facade over the project's reproducible analysis outputs."""
from __future__ import annotations

import csv
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(
    title="Industrial Energy Analysis API",
    version="1.0.0",
    description=(
        "只读服务。指标、异常与摘要来自最近一次成功生成的 output/Q*.csv；"
        "本 API 不连接或修改 MySQL/Hive。"
    ),
)


def output_dir() -> Path:
    """Resolve per-request to support isolated deployments and test fixtures."""
    return Path(os.getenv("ENERGY_OUTPUT_DIR", ROOT / "output")).resolve()


def read_csv(filename: str) -> list[dict[str, str]]:
    path = output_dir() / filename
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return [
                {(key or "").strip(): (value or "").strip()
                 for key, value in row.items()}
                for row in csv.DictReader(stream)
            ]
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"分析产物缺失：{filename}。请先运行数据仓库分析任务。",
        ) from exc
    except (OSError, csv.Error, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail=f"无法读取分析产物：{filename}") from exc


def number(row: dict[str, str], key: str) -> float:
    try:
        value = float(row[key])
        if not math.isfinite(value):
            raise ValueError("non-finite number")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"产物字段 {key} 缺失或无效") from exc
    return value


def generated_at(filenames: list[str]) -> str | None:
    paths = [output_dir() / filename for filename in filenames]
    if not all(path.is_file() for path in paths):
        return None
    timestamp = max(path.stat().st_mtime for path in paths)
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@app.get("/health", tags=["运行状态"])
def health() -> dict[str, Any]:
    required = ["Q01_能源消费总览.csv", "Q02_剔除公用工程后的能耗总览.csv",
                "Q03_各车间综合能耗排名.csv",
                "Q16_单耗异常日检测_2sigma.csv", "Q22_能耗突增预警_环比超25pct.csv",
                "Q28_各车间日度能耗与产量.csv"]
    missing = [name for name in required if not (output_dir() / name).is_file()]
    return {"status": "degraded" if missing else "ok", "missing_artifacts": missing}


@app.get("/api/v1/metrics", tags=["指标"])
def metrics(
    date_from: date | None = Query(default=None, description="包含的起始业务日期"),
    date_to: date | None = Query(default=None, description="包含的结束业务日期"),
    workshop_code: str | None = Query(default=None, min_length=1, max_length=16),
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from 不能晚于 date_to")
    rows = read_csv("Q28_各车间日度能耗与产量.csv")
    selected = []
    for row in rows:
        record_date = row.get("日期", "")
        if date_from and record_date < date_from.isoformat():
            continue
        if date_to and record_date > date_to.isoformat():
            continue
        if workshop_code and row.get("车间编码") != workshop_code:
            continue
        selected.append(row)
    dates = {row["日期"] for row in selected if row.get("日期")}
    return {
        "filters": {"date_from": date_from, "date_to": date_to,
                    "workshop_code": workshop_code},
        "record_days": len(dates),
        "workshop_days": len(selected),
        "workshops": len({row.get("车间编码") for row in selected if row.get("车间编码")}),
        "tce": round(sum(number(row, "综合能耗_tce") for row in selected), 4),
        "cost_yuan": round(sum(number(row, "能源费用_元") for row in selected), 2),
        "co2_t": round(sum(number(row, "碳排放_tCO2") for row in selected), 3),
        "data_as_of": max(dates) if dates else None,
        "generated_at": generated_at(["Q28_各车间日度能耗与产量.csv"]),
    }


@app.get("/api/v1/anomalies", tags=["异常"])
def anomalies(
    date_from: date | None = Query(default=None, description="包含的起始日期；月度告警按月首日筛选"),
    date_to: date | None = Query(default=None, description="包含的结束日期；月度告警按月首日筛选"),
    workshop_code: str | None = Query(default=None, min_length=1, max_length=16),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from 不能晚于 date_to")
    code_by_name = {
        row.get("车间编码", ""): row.get("车间", "")
        for row in read_csv("Q03_各车间综合能耗排名.csv")
    }
    workshop_codes = {name: code for code, name in code_by_name.items() if code and name}
    items: list[dict[str, Any]] = []
    for row in read_csv("Q16_单耗异常日检测_2sigma.csv"):
        code = row.get("车间编码") or workshop_codes.get(row.get("车间", ""))
        items.append({
            "event_date": row.get("日期"), "period": "day", "workshop_code": code,
            "workshop": row.get("车间"), "alert_type": "unit_energy_2sigma",
            "metric": "单位产品能耗", "value": number(row, "单位产品能耗_kgce"),
            "expected": number(row, "车间均值"), "z_score": number(row, "Z值"),
            "evidence_source": "Q16_单耗异常日检测_2sigma.csv",
        })
    for row in read_csv("Q22_能耗突增预警_环比超25pct.csv"):
        month = row.get("年月", "")
        event_date = f"{month}-01" if len(month) == 7 else month
        items.append({
            "event_date": event_date, "period": "month",
            "workshop_code": workshop_codes.get(row.get("车间", "")),
            "workshop": row.get("车间"), "alert_type": "monthly_energy_mom",
            "metric": "月度能耗环比", "value": number(row, "环比_pct"),
            "expected": 25.0, "z_score": None,
            "evidence_source": "Q22_能耗突增预警_环比超25pct.csv",
        })
    selected = []
    for item in items:
        event_date = item["event_date"] or ""
        if date_from and event_date < date_from.isoformat():
            continue
        if date_to and event_date > date_to.isoformat():
            continue
        if workshop_code and item["workshop_code"] != workshop_code:
            continue
        selected.append(item)
    selected.sort(key=lambda item: (item["event_date"] or "", item["alert_type"],
                                    item["workshop"] or ""), reverse=True)
    return {"total": len(selected), "offset": offset, "limit": limit,
            "items": selected[offset:offset + limit],
            "generated_at": generated_at([
                "Q03_各车间综合能耗排名.csv", "Q16_单耗异常日检测_2sigma.csv",
                "Q22_能耗突增预警_环比超25pct.csv",
            ])}


@app.get("/api/v1/report-summary", tags=["报告"])
def report_summary() -> dict[str, Any]:
    totals_rows = read_csv("Q01_能源消费总览.csv")
    totals_ex_rows = read_csv("Q02_剔除公用工程后的能耗总览.csv")
    if not totals_rows or not totals_ex_rows:
        raise HTTPException(status_code=503, detail="总览查询结果为空")
    totals, totals_ex = totals_rows[0], totals_ex_rows[0]
    workshops = read_csv("Q03_各车间综合能耗排名.csv")
    daily = read_csv("Q28_各车间日度能耗与产量.csv")
    dates = [row.get("日期", "") for row in daily if row.get("日期")]
    return {
        "totals": {
            "days": int(number(totals, "统计天数")),
            "workshops": int(number(totals, "车间数")),
            "energy_types": int(number(totals, "能源品种数")),
            "tce": number(totals, "综合能耗_tce"),
            "tce_excluding_utility": number(totals_ex, "综合能耗_tce"),
            "cost_yuan": number(totals, "能源费用_元"),
            "co2_t": number(totals, "碳排放_tCO2"),
        },
        "top_workshops": [{
            "workshop_code": row.get("车间编码"), "workshop": row.get("车间"),
            "tce": number(row, "综合能耗_tce"),
            "cost_yuan": number(row, "能源费用_元"),
            "co2_t": number(row, "碳排放_tCO2"),
        } for row in workshops[:5]],
        "anomaly_count": len(read_csv("Q16_单耗异常日检测_2sigma.csv")),
        "monthly_spike_count": len(read_csv("Q22_能耗突增预警_环比超25pct.csv")),
        "data_range": {"from": min(dates) if dates else None,
                       "to": max(dates) if dates else None},
        "generated_at": generated_at([
            "Q01_能源消费总览.csv", "Q02_剔除公用工程后的能耗总览.csv",
            "Q03_各车间综合能耗排名.csv", "Q16_单耗异常日检测_2sigma.csv",
            "Q22_能耗突增预警_环比超25pct.csv", "Q28_各车间日度能耗与产量.csv",
        ]),
    }
