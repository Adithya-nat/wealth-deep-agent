"""A deterministic lint for machine-sounding prose.

The repo's argument is that you should not pay a model to check what code can
check. That applies to writing quality too: "did the memo say 'it is worth
noting that'" has exactly one right answer, so it gets a regex rather than a
judge.

This is a *report*, not a gate. Prose style is a distribution, not an invariant
— a memo with two flagged phrases is not broken, and failing a run over word
choice would be exactly the over-assertion this repo warns about elsewhere. The
findings appear in the report so a human can decide.

Patterns adapted from the `humanizer` skill vendored at `skills/humanizer/`
(MIT, v2.11.1), itself based on Wikipedia's *Signs of AI writing*. The skill
teaches the agent while it writes; this checks the result afterwards. Only a
subset is mechanizable — "forced groups of three" and "shallow -ing phrases"
need judgement, so they stay in the skill and out of here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: (label, pattern, suggestion). Deliberately narrow: each one is a phrase that
#: is nearly always padding in a financial memo, not a judgement call about
#: style. False positives here cost credibility, so borderline cases are out.
PATTERNS: list[tuple[str, str, str]] = [
    # Word lists follow humanizer §1, §4 and §7. Trimmed to the ones that can
    # plausibly appear in a financial memo — "nestled" and "must-visit" are in
    # the source skill and would only ever be noise here.
    ("inflated", r"\b(pivotal|crucial|testament to|landmark|underscores?|noteworthy|key turning point|indelible|marking a|setting the stage for)\b", "state the figure instead"),
    ("sales", r"\b(seamless|robust|powerful|compelling|impressive|vibrant|renowned|groundbreaking)\b", "describe what it does"),
    ("stock-word", r"\b(delve|garner|tapestry|showcase[sd]?|interplay|foster(s|ing)?|enhance[sd]?\s+its)\b", "use a plainer word"),
    ("deeper-truth", r"\b(the real question is|at its core|what really matters|the deeper issue|the heart of the matter)\b", "state the point"),
    ("vague-source", r"\b(experts?\s+(believe|say|suggest)|analysts?\s+expect|industry\s+reports?\s+suggest|observers?\s+(note|cite)|it\s+is\s+widely\s+(believed|held))\b", "name and cite the source, or cut it"),
    ("filler", r"\b(in order to|due to the fact that|at this point in time|has the ability to|it is worth noting that|it should be noted that)\b", "say it directly"),
    ("stacked-hedge", r"\b(may\s+potentially|could\s+possibly|might\s+potentially|potentially\s+may)\b", "one hedge is enough"),
    ("announcing", r"\b(let's (dive|look|explore)|here's what you need to know|the key takeaway is)\b", "make the point"),
    ("generic-ending", r"\b(well[- ]positioned for|poised for (continued )?growth|remains? well positioned)\b", "end on the last concrete fact"),
    ("not-just", r"\bnot just [^.,;]{2,40}, but\b", "write the claim"),
    ("serves-as", r"\b(serves as|stands as|boasts)\b", "use is or has"),
]

#: An em or en dash standing in for a minus sign.
#:
#: The lookbehind is the whole rule: a dash *preceded* by a digit is a range —
#: `10–12%`, `$4,456–$4,803` — and flagging those was the lint's first false
#: positive. A dash that signs a number has something other than a digit before
#: it. This is the same distinction `data.store` makes when it normalizes
#: typographic minus signs, and the two must agree or the lint will complain
#: about figures the checker reads correctly.
_DASH_AS_MINUS = re.compile(r"(?<![\d%])[–—](?=\s*\$?\d)")

#: Any em or en dash. Humanizer §14 is unusually absolute about this: "the final
#: rewrite must not contain em dashes or en dashes, unless the writer's sample
#: uses them." There is no writer sample here, so the rule applies as written.
#:
#: Kept separate from `_DASH_AS_MINUS` because the two are different problems.
#: That one is arithmetic: the checker may read the figure as negative. This one
#: is only style, and is reported as such.
#:
#: A dash between two digits is excluded. `10–12%` and `$4,456–$4,803` are
#: ordinary numeric ranges, §14's examples are all prose dashes, and flagging
#: every range is how a lint earns the reputation that gets it switched off.
_ANY_DASH = re.compile(r"(?<![\d$])[–—]|[–—](?![\s$]*\d)")

#: Lines that are structure rather than prose.
_SKIP = re.compile(r"^\s*(\||#{1,6}\s|```|[-*_]{3,}\s*$)")


@dataclass
class VoiceFinding:
    """One phrase worth reconsidering."""

    label: str
    line: int
    excerpt: str
    suggestion: str


def lint(memo: str) -> list[VoiceFinding]:
    """Every machine-sounding phrase in a memo, with a suggestion.

    Headings and tables are skipped: a table cell reading "Energy" is not prose
    and a heading is not a sentence.
    """
    findings: list[VoiceFinding] = []
    for lineno, line in enumerate(memo.splitlines(), start=1):
        if _SKIP.match(line):
            continue
        for label, pattern, suggestion in PATTERNS:
            for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                findings.append(
                    VoiceFinding(
                        label=label,
                        line=lineno,
                        excerpt=match.group(0),
                        suggestion=suggestion,
                    )
                )
        signed = {m.start() for m in _DASH_AS_MINUS.finditer(line)}
        for match in _ANY_DASH.finditer(line):
            if match.start() in signed:
                continue  # reported below, as the more serious arithmetic case
            findings.append(
                VoiceFinding(
                    label="em-dash",
                    line=lineno,
                    excerpt=line[max(0, match.start() - 20) : match.end() + 20].strip(),
                    suggestion="use a comma, colon, or full stop (humanizer §14)",
                )
            )
        for match in _DASH_AS_MINUS.finditer(line):
            findings.append(
                VoiceFinding(
                    label="dash-as-minus",
                    line=lineno,
                    excerpt=line[max(0, match.start() - 12) : match.end() + 10].strip(),
                    suggestion=(
                        "a dash before a figure reads as a minus sign: use a comma or "
                        "colon for punctuation, or a hyphen if you mean negative"
                    ),
                )
            )
    return findings


def summarize(findings: list[VoiceFinding]) -> str:
    """One line per distinct pattern, for the CLI."""
    if not findings:
        return "no machine-sounding phrases found"
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.label] = counts.get(finding.label, 0) + 1
    parts = ", ".join(f"{label} ×{n}" for label, n in sorted(counts.items()))
    return f"{len(findings)} phrase(s) worth rewording: {parts}"


__all__ = ["PATTERNS", "VoiceFinding", "lint", "summarize"]
