"""Run the agent on evals/questions.csv and grade the answers.

A question passes when
  - the answer contains the expected value as merged (read from data/indicators.csv at run time, so the
    eval stays valid after the dashboard data are rebuilt), within +/- 0.15, if an indicator is given; and
  - the answer contains at least one of the must_contain alternatives (|-separated), if given; and
  - the agent called at least one of the must_call tools (|-separated), if given.

    python evals/run_evals.py --dry-run     # show questions and expected values, no API calls
    python evals/run_evals.py               # calls the API once per question (costs money)
    python evals/run_evals.py --only q01,q06
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from wfp_agent import tools  # noqa: E402

NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def expected(row, ind: pd.DataFrame) -> float | None:
    if not isinstance(row.indicator, str) or not row.indicator:
        return None
    m = ind[(ind.indicator == row.indicator) & (ind.breakdown == row.breakdown) & (ind.group == row.group)
            & (ind.round_id == row.round_id)]
    if len(m) != 1 or pd.isna(m.as_merged.iloc[0]):
        raise ValueError(f"{row.id}: no single value for {row.indicator}/{row.breakdown}/{row.group}/{row.round_id}")
    return float(m.as_merged.iloc[0])


def grade(text: str, value: float | None, must: str, must_call: str = "", called: tuple[str, ...] = ()) -> tuple[bool, str]:
    reasons = []
    if must_call and not set(must_call.split("|")) & set(called):
        reasons.append(f"called none of [{must_call}]")
    if value is not None:
        nums = [float(x.replace(",", ".")) for x in NUM.findall(text.replace(" ", "").replace(" ", ""))]
        if not any(abs(n - value) <= 0.15 for n in nums):
            reasons.append(f"expected {value:.1f} not found")
    if isinstance(must, str) and must and not any(alt.lower() in text.lower() for alt in must.split("|")):
        reasons.append(f"none of [{must}]")
    return not reasons, "; ".join(reasons)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", default="")
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    qs = pd.read_csv(HERE / "questions.csv", dtype=str).fillna("")
    if a.only:
        qs = qs[qs.id.isin(a.only.split(","))]
    ind = pd.read_csv(tools.DATA / "indicators.csv", dtype={"round_id": str})
    qs["expected"] = [expected(r, ind) for r in qs.itertuples()]

    if a.dry_run:
        for r in qs.itertuples():
            exp = "" if r.expected is None or pd.isna(r.expected) else f"{r.expected:.1f}"
            print(f"{r.id}  expected={exp or '-':>6}  must=[{r.must_contain}]  {r.question}")
        return 0

    from wfp_agent.agent import WfpAgent
    results = []
    for r in qs.itertuples():
        agent = WfpAgent()  # fresh conversation per question
        ans = agent.ask(r.question)
        exp = None if r.expected is None or pd.isna(r.expected) else r.expected
        ok, why = grade(ans.text, exp, r.must_contain, r.must_call, tuple(c["tool"] for c in ans.tool_calls))
        results.append({"id": r.id, "pass": ok, "why": why, "tools": len(ans.tool_calls),
                        "input_tokens": ans.input_tokens, "output_tokens": ans.output_tokens,
                        "question": r.question, "answer": ans.text})
        print(f"{'PASS' if ok else 'FAIL'}  {r.id}  {why}")
    res = pd.DataFrame(results)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    f = out / f"results_{datetime.now():%Y%m%d_%H%M%S}.csv"
    res.to_csv(f, index=False, encoding="utf-8")
    print(f"\n{int(res['pass'].sum())}/{len(res)} passed · "
          f"{int(res.input_tokens.sum()):,} input / {int(res.output_tokens.sum()):,} output tokens · {f}")
    return 0 if res["pass"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
