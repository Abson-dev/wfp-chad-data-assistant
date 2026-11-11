"""Score the expert grading of the agent against the model alone, as in IFPRI's evaluation of RIAPA-AI.

Input: the grading sheet written by `run_evals.py --baseline --sheet`, filled by the graders (correctness, depth,
objectivity: 1 robust, 0.5 incomplete, 0 fail; blank when it does not apply), and its key.

For each system and dimension: the share of "ready to use" answers (score 1) with its exact (Clopper-Pearson) 95%
interval. Between the two systems, on the questions graded for both: McNemar's exact test on ready vs. not ready, and
the Wilcoxon signed-rank test on the scores (normal approximation, zero differences dropped, ties corrected).
With some 25 questions these tests have little power: read the shares and the answers first.

    python evals/score_rubric.py evals/results/grading_sheet_X.csv evals/results/grading_key_X.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

DIMENSIONS = ("correctness", "depth", "objectivity")
SCORES = {1.0, 0.5, 0.0}


def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial interval, by bisection (no scipy needed)."""
    if n == 0:
        return (math.nan, math.nan)

    def solve(f) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2

    lower = 0.0 if x == 0 else solve(lambda p: 1 - _binom_cdf(x - 1, n, p) < alpha / 2)
    upper = 1.0 if x == n else solve(lambda p: _binom_cdf(x, n, p) > alpha / 2)
    return lower, upper


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact test on the discordant pairs (b: only the first system ready, c: only the second)."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * _binom_cdf(min(b, c), n, 0.5))


def wilcoxon(diffs: list[float]) -> float:
    """Two-sided signed-rank test, normal approximation with tie correction; zero differences are dropped."""
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        return 1.0
    ranks = pd.Series([abs(x) for x in d]).rank().tolist()
    w_plus = sum(r for r, x in zip(ranks, d, strict=True) if x > 0)
    mean = n * (n + 1) / 4
    ties = pd.Series([abs(x) for x in d]).value_counts()
    var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties) / 48
    if var <= 0:
        return 1.0
    z = (w_plus - mean) / math.sqrt(var)
    return math.erfc(abs(z) / math.sqrt(2))


def read(sheet: Path, key: Path) -> pd.DataFrame:
    s = pd.read_csv(sheet, dtype=str, encoding="utf-8-sig").fillna("")
    k = pd.read_csv(key, dtype=str)
    df = s.merge(k[["item", "system"]], on="item", how="left", validate="1:1")
    if df["system"].isna().any():
        raise ValueError("Some items of the sheet are not in the key.")
    for d in DIMENSIONS:
        v = pd.to_numeric(df[d].str.replace(",", "."), errors="coerce")
        bad = df[d].ne("") & ~v.isin(SCORES)
        if bad.any():
            raise ValueError(f"{d}: scores must be 1, 0.5, 0 or blank; see items {', '.join(df.loc[bad, 'item'])}.")
        df[d] = v
    return df


def score(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    systems = sorted(df["system"].unique())
    for d in DIMENSIONS:
        for s in systems:
            v = df.loc[df["system"] == s, d].dropna()
            x, n = int((v == 1).sum()), len(v)
            lo, hi = clopper_pearson(x, n)
            rows.append({"dimension": d, "system": s, "n": n, "ready": x, "ready_pct": round(100 * x / n, 1) if n else math.nan,
                         "ci95_low": round(100 * lo, 1), "ci95_high": round(100 * hi, 1), "mean_score": round(v.mean(), 3) if n else math.nan})
        if {"agent", "baseline"} <= set(systems):
            w = df.pivot_table(index="id", columns="system", values=d, aggfunc="first").dropna(subset=["agent", "baseline"])
            b = int(((w.agent == 1) & (w.baseline < 1)).sum())
            c = int(((w.agent < 1) & (w.baseline == 1)).sum())
            rows.append({"dimension": d, "system": "agent vs baseline", "n": len(w), "mcnemar_p": round(mcnemar_exact(b, c), 4),
                         "wilcoxon_p": round(wilcoxon((w.agent - w.baseline).tolist()), 4),
                         "only_agent_ready": b, "only_baseline_ready": c})
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("sheet", type=Path)
    p.add_argument("key", type=Path)
    a = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out = score(read(a.sheet, a.key))
    pd.set_option("display.width", 160)
    print(out.to_string(index=False))
    f = a.sheet.with_name(a.sheet.stem.replace("grading_sheet", "rubric_scores") + ".csv")
    out.to_csv(f, index=False, encoding="utf-8")
    print(f"\n{f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
