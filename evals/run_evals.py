"""Run the agent on evals/questions.csv and grade the answers.

A question passes the automatic grading when
  - the answer contains the expected value as merged (read from data/indicators.csv at run time, so the
    eval stays valid after the dashboard data are rebuilt), within +/- 0.15, if an indicator is given; and
  - the answer contains at least one of the must_contain alternatives (|-separated), if given; and
  - the agent called at least one of the must_call tools (|-separated), if given.
Each result also records the automatic check of the answer's figures (wfp_agent/grounding.py): how many were
found in the tool results, and which were not.

The expert grading follows IFPRI's evaluation of RIAPA-AI (Discussion Paper 2424, 2026), see evals/RUBRIC.md:
--baseline also asks the same questions to the model alone, with no tools and no data (a "plain LLM"), and
--sheet writes a blind sheet where experts score both answers, unaware of which system wrote them, on correctness,
depth and objectivity (1, 0.5 or 0). evals/score_rubric.py then compares the two systems.

    python evals/run_evals.py --dry-run                 # show questions and expected values, no API calls
    python evals/run_evals.py                           # calls the API once per question (costs money)
    python evals/run_evals.py --only q01,q06
    python evals/run_evals.py --baseline --sheet        # agent and plain model, and a blind grading sheet (twice the cost)
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from wfp_agent import grounding, tools  # noqa: E402

NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
# The naive question a user would put to a general-purpose model, as in the RIAPA-AI evaluation.
BASELINE_PROMPT = ("This question is about the household surveys of WFP's Integrated Resilience Programme in Chad, "
                   "2018 to 2023, merged by IFPRI.\n\n{question}")
DIMENSIONS = ("correctness", "depth", "objectivity")


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
        nums = [float(x.replace(",", ".")) for x in NUM.findall(grounding.THOUSANDS_SPACE.sub("", text))]
        if not any(abs(n - value) <= 0.15 for n in nums):
            reasons.append(f"expected {value:.1f} not found")
    if isinstance(must, str) and must and not any(alt.lower() in text.lower() for alt in must.split("|")):
        reasons.append(f"none of [{must}]")
    return not reasons, "; ".join(reasons)


def ask_baseline(client, question: str) -> tuple[str, int, int]:
    """The model alone: no instructions, no tools, no data."""
    from wfp_agent.agent import EFFORT, MODEL
    m = client.beta.messages.create(model=MODEL, max_tokens=8000, output_config={"effort": EFFORT},
                                    messages=[{"role": "user", "content": BASELINE_PROMPT.format(question=question)}])
    text = "\n".join(b.text for b in m.content if b.type == "text").strip()
    return text, m.usage.input_tokens, m.usage.output_tokens


def write_sheet(rows: list[dict], out: Path, stamp: str, seed: int = 0) -> tuple[Path, Path]:
    """A blind grading sheet (answers in random order, labelled by item number only) and the key to unblind it."""
    items = [{"id": r["id"], "topic": r["topic"], "question": r["question"], "system": s, "answer": r[f"{s}_answer"]}
             for r in rows for s in ("agent", "baseline") if f"{s}_answer" in r]
    random.Random(seed).shuffle(items)
    for i, it in enumerate(items, 1):
        it["item"] = f"A{i:03d}"
    sheet = pd.DataFrame(items)[["item", "id", "topic", "question", "answer"]].assign(
        **{d: "" for d in DIMENSIONS}, comment="").sort_values(["id", "item"])
    key = pd.DataFrame(items)[["item", "id", "system"]]
    fs, fk = out / f"grading_sheet_{stamp}.csv", out / f"grading_key_{stamp}.csv"
    sheet.to_csv(fs, index=False, encoding="utf-8-sig")
    key.to_csv(fk, index=False, encoding="utf-8")
    return fs, fk


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", default="")
    p.add_argument("--baseline", action="store_true", help="also ask the model alone, with no tools and no data")
    p.add_argument("--sheet", action="store_true", help="write a blind grading sheet for the expert panel")
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
            print(f"{r.id}  [{r.topic}]  expected={exp or '-':>6}  must=[{r.must_contain}]  {r.question}")
        calls = len(qs) * (2 if a.baseline else 1)
        print(f"\n{len(qs)} questions, {calls} model runs{' (agent and baseline)' if a.baseline else ''}. Nothing was sent.")
        return 0

    from wfp_agent.agent import EFFORT, MODEL, WfpAgent
    build = tools.build_info()
    results = []
    client = None
    for r in qs.itertuples():
        agent = WfpAgent()  # fresh conversation per question
        ans = agent.ask(r.question)
        exp = None if r.expected is None or pd.isna(r.expected) else r.expected
        ok, why = grade(ans.text, exp, r.must_contain, r.must_call, tuple(c["tool"] for c in ans.tool_calls))
        row = {"id": r.id, "topic": r.topic, "pass": ok, "why": why, "tools": len(ans.tool_calls),
               "figures_checked": ans.grounding["checked"], "figures_unverified": "; ".join(ans.grounding["unverified"]),
               "input_tokens": ans.input_tokens, "output_tokens": ans.output_tokens,
               "question": r.question, "agent_answer": ans.text}
        if a.baseline:
            client = client or agent.client
            text, n_in, n_out = ask_baseline(client, r.question)
            row.update(baseline_answer=text, baseline_input_tokens=n_in, baseline_output_tokens=n_out)
        results.append(row)
        flag = f"  unverified: {row['figures_unverified']}" if row["figures_unverified"] else ""
        print(f"{'PASS' if ok else 'FAIL'}  {r.id}  {why}{flag}")
    res = pd.DataFrame(results).assign(model=MODEL, effort=EFFORT, merged_file=build.get("merged_file"),
                                       data_built_at=build.get("built_at"))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    f = out / f"results_{stamp}.csv"
    res.to_csv(f, index=False, encoding="utf-8")
    grounded = int((res.figures_unverified == "").sum())
    print(f"\n{int(res['pass'].sum())}/{len(res)} passed · {grounded}/{len(res)} answers with every figure found in the tools · "
          f"{int(res.input_tokens.sum()):,} input / {int(res.output_tokens.sum()):,} output tokens · {MODEL} · {f}")
    if a.sheet:
        fs, fk = write_sheet(results, out, stamp)
        print(f"Grading sheet: {fs}\nKey (keep it from the graders): {fk}\nThen: python evals/score_rubric.py {fs} {fk}")
    return 0 if res["pass"].all() else 1


if __name__ == "__main__":
    sys.exit(main())
