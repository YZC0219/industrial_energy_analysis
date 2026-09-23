"""运行固定归因评测集，重点验证证据不足降级与引用来源。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.attribution_assistant import analyze, load_evidence


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--cases",default="ml/evals/attribution_cases.json")
    parser.add_argument("--metric-dictionary",default="docs/指标字典.md")
    parser.add_argument("--sql-dir",default="output")
    parser.add_argument("--output",default="output/attribution_eval.json")
    args=parser.parse_args()
    cases=json.loads(Path(args.cases).read_text(encoding="utf-8"))
    evidence=load_evidence(Path(args.metric_dictionary),Path(args.sql_dir))
    results=[]; failures=[]
    for case in cases:
        result,_=analyze(case["question"],evidence,analysis_id=case["case_id"])
        passed=result["insufficient_evidence"] is case["expect_insufficient"]
        expected_source=case.get("expected_source_contains")
        citations=[citation for claim in result["claims"] for citation in claim["citations"]]
        if expected_source:
            passed=passed and any(expected_source in citation["source_path"] for citation in citations)
        results.append({"case_id":case["case_id"],"passed":passed,
                        "insufficient_evidence":result["insufficient_evidence"],
                        "citation_count":len(citations)})
        if not passed:
            failures.append(case["case_id"])
    report={"cases":results,"passed":len(results)-len(failures),"total":len(results),
            "failed_cases":failures}
    output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    if failures:
        raise SystemExit("归因评测失败: "+",".join(failures))


if __name__=="__main__":
    main()
