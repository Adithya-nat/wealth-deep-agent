"""Tests for the prose lint.

Two properties matter and they pull against each other. It has to catch the
phrases that make a memo read as machine-written, and it has to stay quiet on
ordinary financial prose — a lint that cries wolf on `10–12%` gets switched off,
and then it catches nothing at all.
"""

from __future__ import annotations

import pytest

from wealth_agent.voice import lint, summarize


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("It is worth noting that spending rose.", "filler"),
        ("Experts believe the fund will outperform.", "vague-source"),
        ("The portfolio serves as a testament to growth.", "serves-as"),
        ("This may potentially affect returns.", "stacked-hedge"),
        ("The account remains well positioned for growth.", "generic-ending"),
        ("It is not just a trim, but a rebalance.", "not-just"),
        ("Let's dive into the numbers.", "announcing"),
        ("A robust and seamless allocation.", "sales"),
    ],
)
def test_machine_phrases_are_caught(text: str, label: str) -> None:
    assert label in {f.label for f in lint(text)}


@pytest.mark.parametrize(
    "text",
    [
        "Trim AAPL by $11,759.48. Information Technology is 34.71% of equity.",
        "Revenue growth of 10-12% year over year.",
        "Monthly spend held between $4,456.99 and $4,803.03.",
        "UNH is down -$1,621.90, or -22.61% of its cost basis.",
        "No tool in this run returns tax lots, so the tax cost is not estimated.",
    ],
)
def test_ordinary_financial_prose_is_not_flagged(text: str) -> None:
    """False positives are how a lint gets ignored."""
    assert lint(text) == []


@pytest.mark.parametrize("text", ["revenue growth of 10–12% year over year", "held $4,456.99–$4,803.03"])
def test_a_dash_between_digits_is_a_range_not_a_minus(text: str) -> None:
    """The lint's first false positive. A dash preceded by a digit is a range."""
    assert not [f for f in lint(text) if f.label == "dash-as-minus"]


@pytest.mark.parametrize("text", ["UNH is down —$1,621.90", "a loss of (–22.61%)"])
def test_a_dash_signing_a_figure_is_flagged(text: str) -> None:
    assert [f for f in lint(text) if f.label == "dash-as-minus"]


def test_tables_and_headings_are_not_prose() -> None:
    """A table cell reading 'Energy' is not a sentence."""
    assert lint("| Sector | robust |\n## Recommended Actions\n---") == []


def test_uncertainty_language_survives() -> None:
    """The rule against hedging is about padding, not about honesty.

    A memo that stops naming its limits to sound more confident has been made
    worse, and the voice skill says so explicitly.
    """
    honest = (
        "No tool in this run returns tax lots. The August figure covers a partial "
        "month and is not comparable. This could not be verified against a primary source."
    )
    assert lint(honest) == []


def test_summary_counts_by_pattern() -> None:
    findings = lint("It is worth noting that experts believe this is robust.")
    assert "3 phrase(s)" in summarize(findings)
    assert summarize([]) == "no machine-sounding phrases found"


# --------------------------------------------------------------------------
# The voice guidance is a deep-agent skill, not a longer prompt
# --------------------------------------------------------------------------


def test_memo_voice_is_a_progressive_disclosure_skill() -> None:
    """It loads the same way the other three do.

    Skills carry their frontmatter into every turn and their body only when the
    agent opens them. The voice rules are ~90 lines the agent needs once, while
    writing — exactly the shape skills exist for, and exactly the wrong thing
    to paste into a system prompt that is re-sent on every one of forty calls.
    """
    import yaml

    from wealth_agent.config import REPO_ROOT

    path = REPO_ROOT / "skills" / "memo-voice" / "SKILL.md"
    assert path.exists()
    front = path.read_text(encoding="utf-8").split("---")[1]
    meta = yaml.safe_load(front)
    assert meta["name"] == "memo-voice"
    assert "before writing or revising the memo" in meta["description"]


def test_the_supervisor_is_told_to_read_it() -> None:
    """A skill nothing points at is a skill nothing opens."""
    import re

    from wealth_agent import prompts

    body = re.sub(r"\s+", " ", prompts.get("supervisor").body)
    assert "/skills/memo-voice/SKILL.md" in body
    assert "format wins where they conflict" in body.lower()


def test_the_naive_agent_gets_no_skills_including_this_one() -> None:
    """The workshop's comparison depends on the naive agent lacking the
    discipline, and skills are where the discipline lives."""
    import inspect

    from wealth_agent.agents import supervisor

    source = inspect.getsource(supervisor.build_wealth_agent)
    assert 'skills=None if mode == "naive" else ["/skills/"]' in source


# --------------------------------------------------------------------------
# The vendored skill
# --------------------------------------------------------------------------


def test_the_humanizer_skill_is_vendored_with_its_licence() -> None:
    """It is MIT-licensed third-party work. Ship the attribution with it."""
    import yaml

    from wealth_agent.config import REPO_ROOT

    path = REPO_ROOT / "skills" / "humanizer" / "SKILL.md"
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    meta = yaml.safe_load(raw.split("---")[1])
    assert meta["license"] == "MIT"
    assert meta["metadata"]["version"], "pin the version so drift is visible"
    assert "Signs of AI writing" in raw, "the upstream source must stay credited"


def test_memo_voice_defers_to_humanizer_rather_than_restating_it() -> None:
    """A paraphrase of someone else's 456-line skill goes stale silently.

    memo-voice carries only what changes because this is a financial memo.
    """
    from wealth_agent.config import REPO_ROOT

    body = (REPO_ROOT / "skills" / "memo-voice" / "SKILL.md").read_text(encoding="utf-8")
    assert "/skills/humanizer/SKILL.md" in body
    assert "Grounding beats prose" in body
    assert len(body.splitlines()) < 120, "the adapter should stay thin"


def test_no_pattern_fires_twice_on_the_same_word() -> None:
    """Duplicate hits inflate the count and make the report look worse than it is."""
    for text in ("The portfolio boasts robust growth.", "It serves as a testament to growth."):
        labels = [f"{f.label}:{f.excerpt.lower()}" for f in lint(text)]
        assert len(labels) == len(set(labels)), f"duplicate finding in {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "IT sits at 34.71% of equity — the largest breach",
        "the policy — announced today — applies",
    ],
)
def test_prose_em_dashes_are_flagged(text: str) -> None:
    """Humanizer §14 is absolute: no em or en dashes without a writer sample."""
    assert "em-dash" in {f.label for f in lint(text)}


@pytest.mark.parametrize(
    "text",
    ["a range of 10–12% held", "spend of $4,456.99–$4,803.03 monthly"],
)
def test_numeric_ranges_are_not_flagged_as_dashes(text: str) -> None:
    """§14's examples are prose dashes. Flagging every range is how a lint gets
    switched off, and then it catches nothing at all."""
    assert not [f for f in lint(text) if f.label in {"em-dash", "dash-as-minus"}]
