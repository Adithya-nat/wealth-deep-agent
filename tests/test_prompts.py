"""The prompts are now files, so they can have tests.

That is most of the argument for moving them out of Python. A string constant
with a typo'd placeholder produces an agent with a literal `${SPEND_DIR}` in its
instructions and no error anywhere; these tests make that a red build.
"""

from __future__ import annotations

import re

import pytest

from wealth_agent import prompts


def flat(text: str) -> str:
    """Collapse wrapping so an assertion matches a phrase broken across lines.

    The prompt files are hard-wrapped for review, so `"never compute"` may be
    `"never\ncompute"` on disk. Tests assert on meaning, not on line breaks.
    """
    return re.sub(r"\s+", " ", text).strip().lower()

#: Every prompt that configures an agent. `rubric` is criteria, not a prompt,
#: so it is exempt from the structural checks below.
AGENT_PROMPTS = [
    "supervisor",
    "supervisor_naive",
    "portfolio_analyst",
    "spend_analyst",
    "market_researcher",
    "allocation_strategist",
    "verifier",
    "rubric_grader",
]


def test_every_prompt_on_disk_loads() -> None:
    names = prompts.names()
    assert names, "no prompts found"
    for name in names:
        assert prompts.get(name).body.strip(), f"{name} is empty"


def test_a_missing_prompt_names_the_ones_that_exist() -> None:
    """The failure this replaces was a typo'd constant and a silent empty prompt."""
    with pytest.raises(FileNotFoundError, match="supervisor"):
        prompts.get("superviser")


@pytest.mark.parametrize("name", AGENT_PROMPTS)
def test_agent_prompts_declare_a_role(name: str) -> None:
    assert "<role>" in prompts.get(name).body


@pytest.mark.parametrize("name", AGENT_PROMPTS)
def test_agent_prompts_have_frontmatter(name: str) -> None:
    meta = prompts.get(name).meta
    assert meta.get("name"), f"{name} has no name in frontmatter"
    assert meta.get("description"), f"{name} has no description in frontmatter"


def test_no_placeholder_survives_rendering() -> None:
    """A `${VAR}` left in a rendered prompt is an instruction the model cannot follow."""
    rendered = {
        "portfolio_analyst": prompts.render("portfolio_analyst", PORTFOLIO_DIR="portfolio"),
        "spend_analyst": prompts.render("spend_analyst", SPEND_DIR="spend"),
        "market_researcher": prompts.render("market_researcher", SOURCES_DIR="sources"),
        "supervisor": prompts.render("supervisor"),
        "supervisor_naive": prompts.render("supervisor_naive"),
        "verifier": prompts.render("verifier"),
        "allocation_strategist": prompts.render("allocation_strategist"),
        "rubric_grader": prompts.render("rubric_grader"),
    }
    for name, text in rendered.items():
        leftover = re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", text)
        assert not leftover, f"{name} still contains {leftover}"


def test_grounding_prompts_forbid_self_computed_numbers() -> None:
    """The whole verification story rests on this instruction being present."""
    for name in ("supervisor", "portfolio_analyst", "spend_analyst"):
        body = flat(prompts.get(name).body)
        assert "never compute" in body, f"{name} does not forbid self-computed figures"


def test_the_naive_prompt_is_the_one_without_the_discipline() -> None:
    """The workshop's central comparison, asserted so it cannot quietly drift.

    If someone "improves" the naive prompt by adding grounding rules, the
    naive/baseline demo stops showing anything and nothing else would catch it.
    """
    careful = flat(prompts.get("supervisor").body)
    naive = flat(prompts.get("supervisor_naive").body)
    for discipline in ("never compute", "src_", "denominator"):
        assert discipline in careful, f"supervisor.md lost {discipline!r}"
        assert discipline not in naive, f"supervisor_naive.md gained {discipline!r}"


def test_the_researcher_treats_fetched_pages_as_untrusted() -> None:
    """Fetched web pages enter a context window. Nothing else says they are data."""
    body = prompts.get("market_researcher").body
    assert "<untrusted_content>" in body
    assert "ignore your previous instructions" in flat(body)
