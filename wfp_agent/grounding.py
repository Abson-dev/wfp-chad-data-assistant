"""Check that every figure in an answer comes from the tool results.

The design follows IFPRI's RIAPA-AI (Discussion Paper 2424, 2026): the model's narrative must rest only on numbers the
deterministic engine produced. Here the engine is the dashboard's aggregate tables, and this module checks the
answer after the model has written it. It extracts every number the answer states and looks for it, at the
precision the answer gives, among the numbers of this question's tool outputs, the question itself, the earlier
turns of the conversation and the instructions (which state a few documented facts, such as 17,255 households).
A number not found is reported to the reader as unverified. It is a warning, not a proof: a number can be found by
chance, and the check cannot tell whether a found number is used in the right place.

Not checked: whole numbers from 0 to 12 (counts, ranks, months), years (1990 to 2035), dates, and numbers that are
part of a name (R10, v9, LhCSIStress1, q09).
"""
from __future__ import annotations

import bisect
import re

DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?\b")
# French thousands separators: a space, no-break or narrow no-break space before a group of exactly 3 digits.
THOUSANDS_SPACE = re.compile(r"(?<=\d)[   ](?=\d{3}(?!\d))")
NUMBER = re.compile(
    r"(?<![\w.,])(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:(?P<sep>[.,])(?P<dec>\d+))?(?![\w])"
    r"(?:\s*(?P<scale>milliards?|billions?|bn|millions?|mn|M)\b)?"
)
PLAIN = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
SCALES = {"milliard": 1e9, "milliards": 1e9, "billion": 1e9, "billions": 1e9, "bn": 1e9,
          "million": 1e6, "millions": 1e6, "mn": 1e6, "M": 1e6}


def _candidates(m: re.Match) -> list[tuple[float, float]]:
    """The values a written number can mean, each with the rounding tolerance of how it is written.
    "17,255" may be 17255 or (in French) 17.255; both are tried."""
    whole, sep, dec = m["int"], m["sep"], m["dec"]
    scale = SCALES.get(m["scale"] or "", 1.0)
    out = []
    if "," in whole:  # 17,255 or 1,234,567: thousands, or a French decimal when there is one comma
        out.append((float(whole.replace(",", "")), 0.5))
        if whole.count(",") == 1 and not sep:
            a, b = whole.split(",")
            out.append((float(f"{a}.{b}"), 0.5 * 10 ** -len(b)))
    else:
        out.append((float(whole), 0.5))
    if sep and dec:
        base = float(whole.replace(",", ""))
        out = [(float(f"{int(base)}.{dec}"), 0.5 * 10 ** -len(dec))]
        if sep == "," and len(dec) == 3 and "," not in whole:  # 17,255 written with a decimal comma or thousands
            out.append((float(whole + dec), 0.5))
    return [(v * scale, tol * scale) for v, tol in out]


def _numbers(text: str) -> list[tuple[str, list[tuple[float, float]]]]:
    text = THOUSANDS_SPACE.sub("", DATE.sub(" ", text or ""))
    return [(m.group(0).strip(), _candidates(m)) for m in NUMBER.finditer(text)]


def _ignored(cands: list[tuple[float, float]]) -> bool:
    v, tol = cands[0]
    is_whole = tol >= 0.5 and v == int(v)
    return is_whole and (0 <= v <= 12 or 1990 <= v <= 2035)


class Sources:
    """The numbers an answer may state, sorted for fast lookup."""

    def __init__(self, texts: list[str]):
        vals = set()
        for t in texts:
            # Prose ("17,255 households", "1.25 billion XAF") and CSV cells ("fcs_pb,64.24,3979"), read both ways.
            for _, cands in _numbers(t):
                vals.update(abs(v) for v, _ in cands)
            vals.update(abs(float(x)) for x in PLAIN.findall(t or ""))
        self.values = sorted(vals)

    def has(self, value: float, tol: float) -> bool:
        value = abs(value)
        i = bisect.bisect_left(self.values, value - tol - 1e-9)
        return i < len(self.values) and self.values[i] <= value + tol + 1e-9


def check(answer: str, sources: list[str]) -> dict:
    """{checked: numbers looked for, unverified: those not found (as written), ok: none unverified}."""
    src = Sources(sources)
    checked, unverified = 0, []
    for written, cands in _numbers(answer):
        if _ignored(cands):
            continue
        checked += 1
        if not any(src.has(v, tol) for v, tol in cands) and written not in unverified:
            unverified.append(written)
    return {"checked": checked, "unverified": unverified, "ok": not unverified}
