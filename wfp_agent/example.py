"""A fixed example of an answer, written by the server from the tables: no model, no API key, no cost.

It shows a new user what an answer looks like (the text, the chart, the check of the figures, the sources and the
trace) before they spend anything. Its figures are read from the same tables as the assistant's, so it follows the
data after each rebuild.
"""
from __future__ import annotations

from . import grounding, tools
from .agent import SYSTEM, provenance

QUESTION = "How did emergency livelihood coping evolve, and in which regions did it improve or worsen most?"
INDICATOR = "lcs_em"


def _pick_indicator() -> str:
    """lcs_em, or the first indicator with values (the invented demo data may lack it)."""
    ids = set(tools._table("indicators.csv").dropna(subset=["as_merged"])["indicator"])
    if INDICATOR in ids:
        return INDICATOR
    return next(i for i in tools._table("indicator_catalog.csv")["id"] if i in ids)


def _regions(x, unit: str) -> str:
    return ", ".join(f"{g} ({v:+.1f} {unit})" for g, v in x.head(3).items()) or "no region"


def build() -> dict:
    """The example as the web app shows an answer: question, text, published chart, check, sources and trace."""
    tools.begin_question()
    indicator = _pick_indicator()
    tools.profile_indicator.call({"indicator": indicator})
    rid = next(iter(tools.RESULTS))
    prof = tools.indicator_profile(indicator)
    info, t = prof["info"], prof["table"].set_index("round_id")
    unit = "points" if info["unit"] == "%" else str(info["unit"])
    sign = " %" if info["unit"] == "%" else ""
    question = QUESTION if indicator == INDICATOR else f"How did '{info['label']}' evolve, and in which regions did it change most?"

    lines = ["A fixed example: the server wrote this answer from the tables, with no model and at no cost. "
             "Ask your own question below.", ""]
    for m in prof["movers"]:
        if m["n"] is None:
            b = t.loc[m["b"]]
            lines.append(f"- **{m['series']}**: {b['value']:.1f}{sign} in the {b['round']}, the only round of this series.")
            continue
        a, b = t.loc[m["a"]], t.loc[m["b"]]
        lines.append(f"- **{m['series']}**: {b['value']:.1f}{sign} in the {b['round']}, against {a['value']:.1f}{sign} in the "
                     f"{a['round']} ({b['change_since_previous']:+.1f} {unit}).")
        if m["n"]:
            names = ("improved most", "worsened most") if m["directed"] else ("rose most", "fell most")
            lines[-1] += (f" Among the {m['n']} regions surveyed in both rounds, it {names[0]} in {_regions(m['first'], unit)}, "
                          f"and {names[1]} in {_regions(m['second'], unit)}.")
    flagged = t[t["flagged"]]
    for rid_, r in flagged.iterrows():
        lines.append(f"- The {r['round']} ({rid_}) is flagged for a known defect: {r['flag_reason']}")
    lines += ["", "Compare a round only with the previous round of the same series: lean-season and post-harvest rounds "
              "differ by season. The figures are unweighted, over the households surveyed in WFP programme areas; the "
              "samples and the regions change between rounds (no panel), and no difference is tested for significance. "
              "The tables describe these changes; they do not explain them."]
    text = "\n".join(lines)

    tools.publish_result.call({"result_id": rid, "chart": "line", "x": "round", "y": "value", "series": "series",
                               "title": f"{info['label']}, all surveyed households", "note": "Unweighted. One line per series."})
    published = tools.PUBLISHED[0] if tools.PUBLISHED else None
    if published:
        published = {**published, "dashboard": tools.dashboard_link(published["source"]["tool"])}
    outputs = list(tools.OUTPUTS)
    return {"example": True, "question": question, "text": text, "published": published, "tool_calls": list(tools.TRACE),
            "grounding": grounding.check(text, outputs + [question, SYSTEM]), "provenance": {**provenance(), "model": "none (fixed example)"},
            "tool_outputs": outputs}
