"""Tests for the report.

The one that matters is `test_annotation_lands_on_the_span_that_was_checked`.
Highlighting the wrong occurrence of a number would be worse than highlighting
nothing: it would confidently point a reviewer at a claim that was never the one
in question.
"""

from __future__ import annotations

import re

import pytest

from wealth_agent.config import ARTIFACTS_DIR
from wealth_agent.data.store import RunWorkspace
from wealth_agent.reporting.markdown import render as render_markdown
from wealth_agent.reporting.render import (
    annotate,
    build_report_data,
    build_tables,
    expand_tables,
    render_report,
    restore_tables,
)
from wealth_agent.verify import verify_memo


@pytest.fixture
def data():
    return build_report_data(RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs"))


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_markdown_escapes_content_it_did_not_insert() -> None:
    out = render_markdown("A <script>alert(1)</script> line")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_tables_get_a_horizontal_scroll_container() -> None:
    """A wide table must scroll inside itself, not push the page sideways."""
    out = render_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
    assert 'class="scroll"' in out
    assert "<table>" in out


# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------


def test_annotation_lands_on_the_span_that_was_checked() -> None:
    """Offsets, not a second regex.

    The memo says `$139,557.05` several times. The verifier checked specific
    occurrences; the report must highlight those and not merely the first match.
    """
    memo = "Cash is $18,420.55 and total is $139,557.05, of which $139,557.05 is counted."
    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    report = verify_memo(memo, ws)
    out = annotate(memo, report)
    assert out.count("\x00span") == len(
        [f for f in report.findings if f.start >= 0]
    ), "one span per checked claim"
    # every original figure survives intact inside its wrapper
    for raw in ("$18,420.55", "$139,557.05"):
        assert raw in out


def test_annotation_marks_verdicts_distinctly(data) -> None:
    out = annotate(data.memo, data.verification)
    assert "claim grounded" in out
    if data.verification.failures:
        assert "claim unsupported" in out or "claim fabricated" in out


def test_a_grounded_figure_names_the_tool_that_produced_it(data) -> None:
    """This is what makes 'you cannot evaluate what you did not record' visible."""
    out = annotate(data.memo, data.verification)
    assert re.search(r'data-evidence="[a-z-]+ · \w+"', out), "no agent · tool attribution found"


# --------------------------------------------------------------------------
# Tables from recorded data
# --------------------------------------------------------------------------


def test_tables_are_built_from_recorded_positions_not_from_the_memo(data) -> None:
    tables = build_tables(data)
    assert {"holdings", "concentration", "drift"} <= set(tables)
    for symbol in ("VOO", "AAPL", "MSFT"):
        assert symbol in tables["holdings"]


def test_a_placeholder_is_replaced_with_the_real_table(data) -> None:
    md, slots = expand_tables("Before\n\n{{table:holdings}}\n\nAfter", build_tables(data))
    assert "{{table:holdings}}" not in md
    out = restore_tables(render_markdown(md), slots)
    assert "<table>" in out
    assert "VOO" in out


def test_table_markup_is_not_escaped_into_visible_source(data) -> None:
    """The bug this replaces printed several hundred lines of raw
    `<div class="scroll"><table>` into the middle of a client-facing report.

    The renderer escapes every tag it did not insert itself — correct for a
    memo written by a model, exactly wrong for markup we generated.
    """
    md, slots = expand_tables("{{table:holdings}}", build_tables(data))
    out = restore_tables(render_markdown(md), slots)
    assert "&lt;table&gt;" not in out
    assert '&lt;div class="scroll"' not in out
    assert '<div class="scroll">' in out


def test_a_table_is_a_block_sibling_not_nested_in_a_paragraph(data) -> None:
    """`<div>` inside `<p>` is invalid and browsers close the paragraph early."""
    import re

    md, slots = expand_tables("Text\n\n{{table:drift}}\n\nMore", build_tables(data))
    out = restore_tables(render_markdown(md), slots)
    assert not re.search(r"<p>\s*<div", out)


def test_the_spend_tables_the_prompt_promises_actually_exist(data) -> None:
    """The supervisor prompt tells the model these placeholders work.

    One of them did not, and the memo got a "no table named spend_category"
    note where a table belonged.
    """
    tables = build_tables(data)
    for name in ("holdings", "concentration", "drift", "spend_category", "spend_merchant"):
        assert name in tables, f"{name} is referenced in prompts but never built"


def test_an_unknown_placeholder_is_visible_rather_than_silently_dropped(data) -> None:
    """A gap in a client-facing document must announce itself."""
    md, slots = expand_tables("{{table:nonexistent}}", build_tables(data))
    assert "{{table:" not in md
    assert "nonexistent" in md
    assert not slots


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def test_the_report_is_self_contained(data) -> None:
    """A strict-CSP viewer and an offline demo both require this."""
    html = render_report(data)
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html
    assert "cdn." not in html


def test_the_report_supports_both_colour_schemes(data) -> None:
    html = render_report(data)
    assert "prefers-color-scheme:dark" in html
    assert '[data-theme="dark"]' in html


def test_the_report_says_what_the_reader_is_looking_at(data) -> None:
    """Kaushik's note: a person opening this cold should not need the README."""
    html = render_report(data)
    for phrase in ("brokerage", "card transactions", "investment policy"):
        assert phrase in html, f"the explainer never mentions {phrase}"


def test_the_report_states_the_review_surface(data) -> None:
    """N checked by machine, M need a human — the customer value, computed."""
    html = render_report(data)
    assert "need a human" in html
    assert "verified by machine" in html


# --------------------------------------------------------------------------
# Recommendation cards
# --------------------------------------------------------------------------


def test_recommendations_are_rebuilt_from_the_recorded_plan() -> None:
    """The cards must show what the tool computed, not what a model recalled.

    The strategist's structured output is consumed inside the delegation and is
    unreachable afterwards. Reading `rebalance_plan` back out of the ledger is
    the better source anyway: if the memo's prose ever disagrees with the
    computed amounts, both are on the same page.
    """
    from wealth_agent.reporting.render import recommendations_from_ledger

    ws = RunWorkspace(run_id="verified", base=ARTIFACTS_DIR / "runs")
    recs = recommendations_from_ledger(ws)
    if recs is None:
        pytest.skip("this frozen run predates the allocation tools")
    assert recs.actions
    for action in recs.trades:
        assert action.dollars > 0
        assert action.reason_code


def test_a_run_without_a_plan_yields_no_cards() -> None:
    """A run that never reached the strategist must not fabricate a card."""
    from wealth_agent.reporting.render import recommendations_from_ledger

    ws = RunWorkspace(run_id="naive", base=ARTIFACTS_DIR / "runs")
    assert recommendations_from_ledger(ws) is None


def test_a_policy_conflict_reaches_the_card(tmp_path) -> None:
    """Two policy rules disagreeing is the most interesting thing on the page.

    It must survive from the tool, through the ledger, onto the report.
    """
    import json

    from wealth_agent.data.store import RunWorkspace as WS
    from wealth_agent.reporting.render import _recommendation_cards, recommendations_from_ledger

    ws = WS(run_id="synthetic", base=tmp_path)
    ws.ledger.record(
        kind="tool_result",
        name="rebalance_plan",
        agent="allocation-strategist",
        args={},
        content=json.dumps(
            {
                "policy": "balanced-growth",
                "actions": [
                    {
                        "action": "TRIM",
                        "symbol": "VOO",
                        "dollars": 18651.05,
                        "reason_code": "SINGLE_NAME_OVER_CAP",
                        "detail": "VOO is 23.36% of total against a 10% cap",
                        "policy_conflict": "Broad Market ETF is below its target weight",
                    }
                ],
                "residual": {"left_uninvested": 17560.96},
                "caveats": ["No tax-lot data."],
            }
        ),
    )
    recs = recommendations_from_ledger(ws)
    assert recs is not None
    assert recs.actions[0].policy_conflict
    html = _recommendation_cards(recs)
    assert "Policy conflict" in html
    assert "18,651.05" in html


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------


def test_a_common_number_is_grounded_but_not_attributed() -> None:
    """A wrong pointer is worse than no pointer.

    The report credited Apple's "8% revenue growth" to
    `spend-analyst · load_spend_data`, because an unrelated 8 happened to be
    recorded first. The verdict was right and the evidence link was nonsense.
    """
    from wealth_agent.verify import _is_distinctive

    assert not _is_distinctive(8)
    assert not _is_distinctive(25)
    assert _is_distinctive(34.71)
    assert _is_distinctive(139_557.05)


def test_distinctive_figures_still_name_their_tool(data) -> None:
    out = annotate(data.memo, data.verification)
    assert "portfolio-analyst" in out or "allocation-strategist" in out


def test_an_unattributed_claim_says_why(data) -> None:
    out = annotate(data.memo, data.verification)
    if "too common to attribute" in out:
        assert "matches a recorded value" in out


def test_the_memo_title_becomes_a_subtitle_not_a_second_heading() -> None:
    """Rendered inside the report it stacked two `<h2>`s saying the same thing."""
    from wealth_agent.reporting.render import split_title

    title, body = split_title("# Wealth Review — June to August\n\n## Recommended actions\n\nText")
    assert title == "Wealth Review — June to August"
    assert body.startswith("## Recommended actions")


def test_a_memo_without_a_title_is_left_alone(data) -> None:
    from wealth_agent.reporting.render import split_title

    title, body = split_title("## Recommended actions\n\nText")
    assert title == ""
    assert body.startswith("## Recommended actions")


def test_the_memo_section_has_exactly_one_heading(data) -> None:
    import re

    html = render_report(data)
    section = html[html.index('<section id="memo">') : html.index('<section id="evidence">')]
    assert len(re.findall(r"<h2>", section)) == 1


# --------------------------------------------------------------------------
# Table placement
#
# `<div>` inside `<p>` is invalid HTML and browsers close the paragraph early,
# which reflows everything after it. The model decides where placeholders go,
# so every arrangement it might choose has to land correctly.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "memo"),
    [
        ("consecutive lines", "Text\n\n{{table:a}}\n{{table:b}}\n{{table:c}}\n\nMore"),
        ("blank-line separated", "{{table:a}}\n\n{{table:b}}"),
        ("inline with prose", "See {{table:a}} above."),
        ("alone", "{{table:a}}"),
    ],
)
def test_a_table_never_ends_up_inside_a_paragraph(label: str, memo: str) -> None:
    import re

    tables = {k: f'<div class="scroll">{k.upper()}</div>' for k in ("a", "b", "c")}
    body, slots = expand_tables(memo, tables)
    out = restore_tables(render_markdown(body, min_heading=3), slots)
    assert not re.search(r"<p>(?:(?!</p>).)*<div", out, re.S), f"{label} nested a table"
    assert not re.search(r"<p>\s*</p>", out), f"{label} left an empty paragraph"
    for key in slots:
        assert key not in out, "a sentinel survived into the output"


def test_the_rendered_report_has_balanced_tags(data) -> None:
    """A stray tag reflows everything after it, which is how the table bug
    presented before its cause was found."""
    import re

    html = render_report(data)
    for tag in ("p", "div", "table", "tbody", "thead", "tr", "section", "article", "ul", "span"):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", html))
        closes = html.count(f"</{tag}>")
        assert opens == closes, f"<{tag}>: {opens} open, {closes} closed"
