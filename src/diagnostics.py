"""Evidence-backed workbench; feedback is isolated from warehouse inputs."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import re
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ml.attribution_assistant import (GroundingError, analyze, load_evidence,
                                     openai_compatible_llm, validate_grounding,
                                     SUMMARY_GROUNDED, SUMMARY_INSUFFICIENT, CAUSAL_WORDS)

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()


@contextmanager
def connection():
    path = Path(os.getenv("ENERGY_DIAGNOSTICS_DB", ROOT / "output/diagnostics/feedback.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, status TEXT NOT NULL,
        reason TEXT NOT NULL, action TEXT NOT NULL, operator TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS feedback_incident ON feedback(incident_id, id)")
    try:
        with db:
            yield db
    finally:
        db.close()


def history(incident_id):
    with connection() as db:
        rows = db.execute("SELECT * FROM feedback WHERE incident_id=? ORDER BY id DESC",
                          (incident_id,)).fetchall()
    return [dict(row) for row in rows]


def incidents(date_from=None, date_to=None, workshop_code=None):
    from src.api import anomalies
    # Read every page to avoid truncating evidence or feedback lookups.
    first = anomalies(date_from, date_to, workshop_code, 1000, 0)
    rows = first["items"]
    for offset in range(1000, first["total"], 1000):
        rows.extend(anomalies(date_from, date_to, workshop_code, 1000, offset)["items"])
    for item in rows:
        key = "|".join(str(item.get(k) or "") for k in
                       ("event_date", "period", "workshop_code", "workshop", "alert_type"))
        item["id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
        item["severity"] = "需优先核查" if (
            abs(item["z_score"] or 0) >= 3 or
            item["alert_type"] == "monthly_energy_mom" and item["value"] >= 50
        ) else "待核查"
        item["unit"] = "kgce/产品单位" if item["period"] == "day" else "%"
        item["direction"] = "偏高" if item["value"] > item["expected"] else "偏低"
        item["deviation_pct"] = (round((item["value"] / item["expected"] - 1) * 100, 2)
                                 if item["period"] == "day" and item["expected"] else None)
    return rows, first["generated_at"]


def find_incident(incident_id):
    rows, _ = incidents()
    item = next((row for row in rows if row["id"] == incident_id), None)
    if item is None:
        raise HTTPException(404, "异常记录不存在或已不在当前分析批次")
    return item


@router.get("/diagnostics", include_in_schema=False)
def workbench():
    return FileResponse(ROOT / "src/diagnostics.html")


@router.get("/diagnostics.js", include_in_schema=False)
def workbench_script():
    return FileResponse(ROOT / "src/diagnostics.js", media_type="text/javascript")


@router.get("/api/v1/diagnostics")
def list_incidents(date_from: date | None = None, date_to: date | None = None,
                   workshop_code: str | None = None,
                   status: Literal["pending", "confirmed", "false_positive", "resolved"] | None = None,
                   priority_only: bool = False,
                   sort: Literal["recent", "priority"] = "recent",
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from src.api import read_csv
    rows, updated = incidents(date_from, date_to, workshop_code)
    with connection() as db:
        latest = {row["incident_id"]: dict(row) for row in db.execute(
            "SELECT * FROM feedback WHERE id IN (SELECT MAX(id) FROM feedback GROUP BY incident_id)")}
    for row in rows:
        row["feedback"] = latest.get(row["id"])
        row["status"] = row["feedback"]["status"] if row["feedback"] else "pending"
    if priority_only:
        rows = [row for row in rows if row["severity"] == "需优先核查"]
    counts = Counter(row["status"] for row in rows)
    summary = {key: counts[key] for key in ("pending", "confirmed", "false_positive", "resolved")}
    summary["priority_open"] = sum(row["severity"] == "需优先核查" and
                                   row["status"] in ("pending", "confirmed") for row in rows)
    if status:
        rows = [row for row in rows if row["status"] == status]
    if sort == "priority":
        rows.sort(key=lambda row: (row["status"] in ("pending", "confirmed"),
                                  row["severity"] == "需优先核查", row["event_date"]), reverse=True)
    return {"items": rows[offset:offset+limit], "total": len(rows), "generated_at": updated,
            "summary": summary,
            "workshops": [{"code": r["车间编码"], "name": r["车间"]}
                          for r in read_csv("Q03_各车间综合能耗排名.csv")],
            "llm_configured": all(os.getenv(k) for k in ("LLM_API_URL", "LLM_MODEL"))}


@router.get("/api/v1/diagnostics/{incident_id}")
def incident_detail(incident_id: str):
    from src.api import read_csv
    item = find_incident(incident_id)
    source_rows = read_csv(item["evidence_source"])
    key = "日期" if item["period"] == "day" else "年月"
    value = item["event_date"] if item["period"] == "day" else item["event_date"][:7]
    evidence = [r for r in source_rows if r.get(key) == value and r.get("车间") == item["workshop"]]
    all_daily = read_csv("Q28_各车间日度能耗与产量.csv")
    daily = [r for r in all_daily
             if r.get("车间编码") == item["workshop_code"] and
             (r.get("日期") == value if item["period"] == "day" else r.get("日期", "").startswith(value))]
    start = (date.fromisoformat(item["event_date"]) - timedelta(days=13)).isoformat()
    trend = (sorted([r for r in all_daily if r.get("车间编码") == item["workshop_code"] and
                     start <= r.get("日期", "") <= item["event_date"]], key=lambda r: r["日期"])
             if item["period"] == "day" else sorted(daily, key=lambda r: r["日期"]))
    return {"incident": item, "evidence": evidence, "daily_context": daily, "trend": trend,
            "history": history(incident_id),
            "boundary": "检测信号尚不能确认设备故障；原因和措施来自操作员反馈，未经独立验证。",
            "checklist": ["核对计量与数据完整性", "核对产量、班次和停产记录", "核对设备运行及维修记录"]}


class Feedback(BaseModel):
    status: Literal["pending", "confirmed", "false_positive", "resolved"]
    reason: str = Field(default="", max_length=2000)
    action: str = Field(default="", max_length=2000)
    operator: str = Field(min_length=1, max_length=80)
    expected_revision: int | None = Field(default=None, ge=0)


@router.post("/api/v1/diagnostics/{incident_id}/feedback")
def save_feedback(incident_id: str, body: Feedback):
    find_incident(incident_id)
    if not body.operator.strip():
        raise HTTPException(422, "请填写处理人")
    if body.status in ("confirmed", "false_positive", "resolved") and not body.reason.strip():
        raise HTTPException(422, "请填写确认原因或判断依据")
    if body.status == "resolved" and not body.action.strip():
        raise HTTPException(422, "关闭异常前请填写处理措施")
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        revision = db.execute("SELECT COALESCE(MAX(id),0) FROM feedback WHERE incident_id=?",
                              (incident_id,)).fetchone()[0]
        if body.expected_revision is not None and body.expected_revision != revision:
            raise HTTPException(409, "这条异常已有新反馈，请刷新详情后核对并重新提交")
        db.execute("INSERT INTO feedback (incident_id,status,reason,action,operator,created_at) VALUES (?,?,?,?,?,?)",
                   (incident_id, body.status, body.reason.strip(), body.action.strip(),
                    body.operator.strip(), datetime.now(timezone.utc).isoformat()))
    return {"history": history(incident_id)}


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    incident_id: str | None = Field(default=None, max_length=24)


def scoped_offline_answer(question, item, evidence):
    """Answer supported questions by selecting exact records, never a workshop total."""
    codes = {c.upper() for c in re.findall(r"W\d{2}", question, re.I)}
    dates = re.findall(r"\d{4}-\d{2}(?:-\d{2})?", question)
    period = item["event_date"] if item["period"] == "day" else item["event_date"][:7]
    mismatch = bool(codes - {item["workshop_code"]}) or any(
        (d != period if item["period"] == "day" else d[:7] != period) for d in dates)
    chosen = []
    if not mismatch and not any(word in question for word in CAUSAL_WORDS):
        fields = []
        for words, field in ((('费用', '成本', '多少钱'), '能源费用_元'),
                             (('产量',), '产量'), (('碳排',), '碳排放_tCO2')):
            if any(word in question for word in words):
                fields.append(field)
        requested_days = {d for d in dates if len(d) == 10}
        if fields:
            chosen = [e for e in evidence if Path(e.source_path).name.startswith('Q28_') and
                      all(field in e.evidence_value for field in fields) and
                      (not requested_days or e.evidence_value.get('日期') in requested_days)]
        elif any(word in question for word in ('异常', '证据', '指标', '环比', '单耗', '能耗', '基线', '均值', 'Z值')):
            chosen = [e for e in evidence if Path(e.source_path).name == item['evidence_source']]
    result = {"analysis_id": str(uuid.uuid4()),
              "summary": SUMMARY_GROUNDED if chosen else SUMMARY_INSUFFICIENT,
              "claims": [{"statement": e.text, "citations": [e.citation()]} for e in chosen],
              "insufficient_evidence": not bool(chosen)}
    validate_grounding(result, evidence)
    audit = {"analysis_id": result["analysis_id"], "question": question,
             "retrieved_evidence": [dict(e.__dict__) for e in evidence], "result": result,
             "provider": "offline_guarded", "selection": "scoped_metric_rules"}
    return result, audit


@router.post("/api/v1/diagnostics/ask")
def ask(body: Question):
    from src.api import output_dir
    if not body.question.strip():
        raise HTTPException(422, "问题不能为空")
    try:
        evidence = load_evidence(ROOT / "docs/指标字典.md", output_dir())
        item = None
        if body.incident_id:
            item = find_incident(body.incident_id)
            period_key = "日期" if item["period"] == "day" else "年月"
            period_value = item["event_date"] if item["period"] == "day" else item["event_date"][:7]
            # A selected incident cannot silently retrieve another workshop or date.
            evidence = [e for e in evidence if isinstance(e.evidence_value, dict) and
                        e.evidence_value.get("车间") == item["workshop"] and
                        (e.evidence_value.get(period_key) == period_value or
                         (item["period"] == "month" and
                          str(e.evidence_value.get("日期", "")).startswith(period_value)) or
                         "Q03_" in e.source_path)]
            question = f"{item['workshop_code'] or ''} {item['workshop']} {period_value} {body.question}"
        else:
            question = body.question
        online = all(os.getenv(k) for k in ("LLM_API_URL", "LLM_MODEL"))
        if item and not online:
            result, audit = scoped_offline_answer(body.question, item, evidence)
        else:
            result, audit = analyze(question, evidence, openai_compatible_llm if online else None)
        if item and any(Path(c['source_path']).name.startswith('Q03_')
                        for claim in result['claims'] for c in claim['citations']):
            raise GroundingError("车间总览不能作为当前异常的回答证据")
        folder = Path(os.getenv("ENERGY_DIAGNOSTICS_AUDIT_DIR", ROOT / "output/diagnostics/audit"))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{result['analysis_id']}.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**result, "mode": audit["provider"],
                "mode_label": "在线模型选择证据" if audit["provider"] == "configured_llm" else
                "证据检索与规则守卫（非在线模型回答）"}
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, GroundingError) as exc:
        raise HTTPException(503, "问答服务或证据不可用，请检查数据与模型配置；未生成回答") from exc
