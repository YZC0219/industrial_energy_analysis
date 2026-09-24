"""运行固定归因评测集，重点验证证据不足降级与引用来源。"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from ml.attribution_assistant import analyze, load_evidence, openai_compatible_llm


def evaluate(cases: list[dict], evidence, llm: Callable[[str],dict] | None=None) -> dict:
    results=[]; failures=[]
    started=time.perf_counter()
    for case in cases:
        case_started=time.perf_counter()
        error=None
        try:
            result,audit=analyze(case["question"],evidence,llm=llm,analysis_id=case["case_id"])
            passed=result["insufficient_evidence"] is case["expect_insufficient"]
            expected_source=case.get("expected_source_contains")
            citations=[citation for claim in result["claims"] for citation in claim["citations"]]
            if expected_source:
                passed=passed and any(expected_source in citation["source_path"] for citation in citations)
            expected_statement=case.get("expected_statement_contains")
            statements=[claim["statement"] for claim in result["claims"]]
            if expected_statement:
                passed=passed and any(expected_statement in statement for statement in statements)
            if not case["expect_insufficient"]:
                passed=passed and bool(citations) and bool(statements)
        except Exception as exc:  # 单个模型错误也必须进入评测报告
            passed=False; error=f"{type(exc).__name__}: {exc}"
            result={"insufficient_evidence":None}; citations=[]; statements=[]
            expected_statement=case.get("expected_statement_contains")
            audit={"provider":"configured_llm" if llm else "offline_guarded"}
        elapsed=round(time.perf_counter()-case_started,3)
        results.append({"case_id":case["case_id"],"passed":passed,
                        "provider":audit["provider"],"latency_seconds":elapsed,
                        "insufficient_evidence":result["insufficient_evidence"],
                        "citation_count":len(citations),"error":error,
                        "statement_matches_expected":(True if not expected_statement else
                            any(expected_statement in statement for statement in statements))})
        if not passed:
            failures.append(case["case_id"])
    total=len(results)
    return {"mode":"online" if llm else "offline", "cases":results,
            "passed":total-len(failures),"total":total,
            "pass_rate":round((total-len(failures))/total,4) if total else 0.0,
            "duration_seconds":round(time.perf_counter()-started,3),
            "failed_cases":failures}


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--cases",default="ml/evals/attribution_cases.json")
    parser.add_argument("--metric-dictionary",default="docs/指标字典.md")
    parser.add_argument("--sql-dir",default="output")
    parser.add_argument("--output",default="output/attribution_eval.json")
    parser.add_argument("--use-llm",action="store_true",
                        help="调用 LLM_API_URL 指向的 OpenAI 兼容模型服务")
    args=parser.parse_args()
    cases=json.loads(Path(args.cases).read_text(encoding="utf-8"))
    evidence=load_evidence(Path(args.metric_dictionary),Path(args.sql_dir))
    report=evaluate(cases,evidence,openai_compatible_llm if args.use_llm else None)
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    if report["failed_cases"]:
        raise SystemExit("归因评测失败: "+",".join(report["failed_cases"]))


if __name__=="__main__":
    main()
