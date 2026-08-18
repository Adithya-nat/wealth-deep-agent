"""The report: one self-contained HTML file where every figure links to its evidence.

This is the screen the whole repo builds toward. "You cannot evaluate what you
did not record" is a sentence until you can click a number in a finished memo
and see the agent, the tool, and the raw output it came from — at which point it
is a property of the artifact.

Three decisions worth naming:

* **Tables are rendered from the recorded data, not written by the model.** The
  memo carries `{{table:holdings}}` and this module expands it from
  `portfolio/positions.json`. A table the model retypes costs output tokens on
  every revision and can be wrong; a placeholder can be neither.
* **Annotation happens on character offsets, before markdown rendering.**
  `verify.py` reports exactly which span it checked, so the report highlights
  that occurrence rather than re-finding the claim with a second regex and
  possibly landing on a different one.
* **The header says what the reader is looking at.** Someone handed this file
  cold — a client, a compliance reviewer — should not need the README to know
  what it is, what went into it, or how far to trust it.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wealth_agent.data.store import PORTFOLIO_DIR, SPEND_DIR, RunWorkspace
from wealth_agent.models import RunMeter
from wealth_agent.policy import Policy, load_policy
from wealth_agent.recommendations import Recommendation, RecommendationSet
from wealth_agent.reporting.markdown import protect, render as render_markdown
from wealth_agent.verify import Verdict, VerificationReport, verify_memo
from wealth_agent.voice import lint as voice_lint

REPORT_FILE = "report.html"

_VERDICT_LABEL = {
    Verdict.GROUNDED: "traces to a recorded tool result",
    Verdict.UNSUPPORTED: "appears in no tool result or fetched source",
    Verdict.FABRICATED: "cites a source that does not support it",
}


@dataclass
class ReportData:
    """Everything the report needs, gathered once."""

    workspace: RunWorkspace
    memo: str
    verification: VerificationReport
    policy: Policy
    meter: RunMeter | None = None
    recommendations: RecommendationSet | None = None
    mode: str = "verified"
    elapsed_seconds: float | None = None
    #: Agents that hit their call ceiling. Non-empty means this report describes
    #: an unfinished run and says so above everything else.
    truncated: list[str] = field(default_factory=list)

    @property
    def positions(self) -> list[dict[str, Any]]:
        path = self.workspace.root / PORTFOLIO_DIR / "positions.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    @property
    def transactions(self) -> list[dict[str, Any]]:
        path = self.workspace.root / SPEND_DIR / "transactions.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    @property
    def balances(self) -> dict[str, Any]:
        path = self.workspace.root / PORTFOLIO_DIR / "balances.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------


def split_title(memo: str) -> tuple[str, str]:
    """Lift the memo's own `# Heading` out of the body.

    Rendered inside the report it became a second `<h2>` directly beneath the
    section's own "The memo" heading — two titles stacked, saying nearly the
    same thing. It still carries the period the review covers, so it becomes a
    subtitle rather than being thrown away.
    """
    lines = memo.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:]).lstrip()
    return "", memo


def annotate(memo: str, report: VerificationReport) -> str:
    """Wrap every checked claim in a verdict-carrying span.

    Insertions run **back to front** so that each offset is still valid when it
    is used — patching forwards would shift every subsequent span by the length
    of the tags already inserted, and the highlights would drift further off
    with every claim on the page.
    """
    spans = sorted(
        (f for f in report.findings if f.start >= 0 and f.end > f.start),
        key=lambda f: f.start,
        reverse=True,
    )
    out = memo
    for finding in spans:
        if finding.grounded_by:
            evidence = f"{finding.grounded_by[0]} · {finding.grounded_by[1]}"
        elif finding.kind == "citation" and finding.verdict is Verdict.GROUNDED:
            # A citation's evidence is the page itself, not a tool call.
            evidence = "a source fetched during this run"
        elif finding.verdict is Verdict.GROUNDED:
            # Grounded, but the value is too common to pin to one tool without
            # risking a wrong pointer. See `verify._DISTINCTIVE_FLOOR`.
            evidence = "matches a recorded value; too common to attribute to one tool"
        else:
            evidence = _VERDICT_LABEL.get(finding.verdict, "")
        open_tag = protect(
            f'<span class="claim {finding.verdict}" '
            f'data-evidence="{html.escape(evidence, quote=True)}" '
            f'data-detail="{html.escape(finding.detail, quote=True)}" '
            f'tabindex="0">'
        )
        out = out[: finding.start] + open_tag + out[finding.start : finding.end] + protect("</span>") + out[finding.end :]
    return out


# --------------------------------------------------------------------------
# Tables rendered from recorded data
# --------------------------------------------------------------------------


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _table(headers: list[str], rows: list[list[str]], *, right_from: int = 1) -> str:
    head = "".join(
        f'<th{" class=num" if i >= right_from else ""}>{html.escape(h)}</th>'
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td{" class=num" if i >= right_from else ""}>{c}</td>' for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def build_tables(data: ReportData) -> dict[str, str]:
    """Every `{{table:...}}` the memo may reference, rendered from the ledger."""
    positions, balances = data.positions, data.balances
    total = balances.get("total_value") or sum(p["market_value"] for p in positions) or 1
    equity = balances.get("equity_value") or sum(p["market_value"] for p in positions) or 1
    tables: dict[str, str] = {}

    tables["holdings"] = _table(
        ["Ticker", "Name", "Qty", "Price", "Market value", "Unrealized P/L"],
        [
            [
                f"<strong>{html.escape(p['symbol'])}</strong>",
                html.escape(p.get("name", "")),
                f"{p['quantity']:g}",
                _money(p["last_price"]),
                _money(p["market_value"]),
                f'<span class="{"pos" if p["unrealized_pl"] >= 0 else "neg"}">'
                f'{"+" if p["unrealized_pl"] >= 0 else "−"}{_money(abs(p["unrealized_pl"]))}</span>',
            ]
            for p in sorted(positions, key=lambda p: -p["market_value"])
        ],
        right_from=2,
    )

    tables["concentration"] = _table(
        ["Ticker", "Market value", "% of total portfolio"],
        [
            [
                f"<strong>{html.escape(p['symbol'])}</strong>",
                _money(p["market_value"]),
                f"{100 * p['market_value'] / total:.2f}%"
                + (
                    ' <span class="flag">over cap</span>'
                    if 100 * p["market_value"] / total > data.policy.max_single_name
                    else ""
                ),
            ]
            for p in sorted(positions, key=lambda p: -p["market_value"])
        ],
    )

    by_sector: dict[str, float] = {}
    for p in positions:
        by_sector[p.get("sector", "Unknown")] = by_sector.get(p.get("sector", "Unknown"), 0.0) + p["market_value"]
    rows = []
    for sector in sorted(set(by_sector) | set(data.policy.sector_targets)):
        value = by_sector.get(sector, 0.0)
        current = 100 * value / equity
        target = data.policy.target_for(sector)
        drift = current - target
        breached = abs(drift) > data.policy.drift_band
        rows.append(
            [
                html.escape(sector),
                _money(value),
                f"{current:.2f}%",
                f"{target:.1f}%",
                f'<span class="{"neg" if breached else "muted"}">{drift:+.2f} pp</span>'
                + (' <span class="flag">breach</span>' if breached else ""),
            ]
        )
    rows.sort(key=lambda r: -abs(float(r[4].split(">")[1].split(" pp")[0])))
    tables["drift"] = _table(
        ["Sector", "Market value", "% of equity", "Target", "Drift"], rows
    )

    # Spending, from the same cached rows the spend analyst worked from.
    # Refunds and statement payments are excluded here exactly as the spend
    # tools exclude them, so this table and the memo's prose agree — a report
    # whose appendix contradicts its own body is worse than one with no
    # appendix.
    charges = [
        t
        for t in data.transactions
        if float(t.get("amount") or 0) > 0 and not t.get("is_payment")
    ]
    if charges:
        by_category: dict[str, list[float]] = {}
        for txn in charges:
            by_category.setdefault(txn.get("category", "Uncategorized"), []).append(
                float(txn["amount"])
            )
        spent = sum(sum(v) for v in by_category.values()) or 1
        tables["spend_category"] = _table(
            ["Category", "Total", "Charges", "% of spend"],
            [
                [
                    html.escape(category),
                    _money(sum(amounts)),
                    str(len(amounts)),
                    f"{100 * sum(amounts) / spent:.2f}%",
                ]
                for category, amounts in sorted(
                    by_category.items(), key=lambda kv: -sum(kv[1])
                )
            ],
        )

        by_merchant: dict[str, list[float]] = {}
        for txn in charges:
            by_merchant.setdefault(txn.get("merchant", "Unknown"), []).append(
                float(txn["amount"])
            )
        top = sorted(by_merchant.items(), key=lambda kv: -sum(kv[1]))[:10]
        tables["spend_merchant"] = _table(
            ["Merchant", "Total", "Charges"],
            [[html.escape(m), _money(sum(a)), str(len(a))] for m, a in top],
        )

    return tables


#: Marks where a table goes while the markdown around it is rendered.
#: Deliberately not HTML: the renderer escapes everything it did not insert
#: itself, so anything angle-bracketed here would come out as visible source.
_SLOT = "\x02table:{index}\x03"


def expand_tables(memo: str, tables: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Swap each `{{table:name}}` for a sentinel, and return the markup to
    substitute back in after rendering.

    The obvious implementation — paste the table HTML straight into the
    markdown — is the one that shipped, and it printed several hundred lines of
    raw `<div class="scroll"><table>...` into the middle of a client-facing
    report. The renderer escapes every tag it did not insert itself, which is
    the behaviour you want for a memo written by a model and exactly wrong for
    markup you generated yourself.

    Substituting after rendering also fixes a second, quieter defect: a table
    pasted inline became a `<div>` nested inside a `<p>`, which is invalid HTML
    and lets a browser close the paragraph wherever it likes.

    An unknown placeholder becomes a visible note rather than disappearing —
    a gap in a financial document has to announce itself.
    """
    import re

    slots: dict[str, str] = {}

    def take(match: re.Match[str]) -> str:
        name = match.group(1)
        markup = tables.get(name)
        if markup is None:
            return f"*(no table named `{name}` was recorded for this run)*"
        key = _SLOT.format(index=len(slots))
        slots[key] = markup
        # Blank lines on both sides so the renderer always treats the sentinel
        # as its own block, whatever the model wrote around it. Splicing
        # `</p>...<p>` around an inline table instead left dangling paragraph
        # tags before headings — correct-looking output, invalid HTML.
        return f"\n\n{key}\n\n"

    return re.sub(r"\{\{table:([a-z_]+)\}\}", take, memo), slots


#: A paragraph containing nothing but sentinels and whitespace.
#:
#: Models write consecutive placeholders on consecutive lines, which markdown
#: reads as one paragraph — so `<p>SLOT0 SLOT1 SLOT2</p>` is the common case,
#: not `<p>SLOT0</p>`. Handling only the single-slot form left three `<div>`s
#: inside a `<p>`, which is invalid and lets the browser close the paragraph
#: early.
_SENTINEL_PARAGRAPH = re.compile(r"<p>((?:\s*\x02table:\d+\x03\s*)+)</p>")


def restore_tables(html_text: str, slots: dict[str, str]) -> str:
    """Put the real tables back as block-level siblings.

    Unwraps the paragraph the renderer put them in first, so a table is never
    nested inside a `<p>`.
    """

    def unwrap(match: re.Match[str]) -> str:
        return match.group(1).strip()

    html_text = _SENTINEL_PARAGRAPH.sub(unwrap, html_text)

    for key, markup in slots.items():
        html_text = html_text.replace(key, markup)
    return re.sub(r"<p>\s*</p>", "", html_text)


# --------------------------------------------------------------------------
# Pieces of the page
# --------------------------------------------------------------------------


def _allocation_bars(data: ReportData) -> str:
    """Current versus target weight, as inline SVG. No chart library."""
    positions = data.positions
    equity = sum(p["market_value"] for p in positions) or 1
    by_sector: dict[str, float] = {}
    for p in positions:
        by_sector[p.get("sector", "Unknown")] = by_sector.get(p.get("sector", "Unknown"), 0.0) + p["market_value"]

    sectors = sorted(
        set(by_sector) | set(data.policy.sector_targets),
        key=lambda s: -max(100 * by_sector.get(s, 0.0) / equity, data.policy.target_for(s)),
    )
    scale = max(
        [100 * v / equity for v in by_sector.values()] + list(data.policy.sector_targets.values())
    ) or 1

    rows = []
    for sector in sectors:
        current = 100 * by_sector.get(sector, 0.0) / equity
        target = data.policy.target_for(sector)
        breached = abs(current - target) > data.policy.drift_band
        rows.append(
            f'<div class="bar-row">'
            f'<div class="bar-label">{html.escape(sector)}</div>'
            f'<div class="bar-track">'
            f'<div class="bar-fill{" breach" if breached else ""}" style="width:{100 * current / scale:.1f}%"></div>'
            f'<div class="bar-target" style="left:{100 * target / scale:.1f}%" '
            f'title="target {target:.1f}%"></div>'
            f"</div>"
            f'<div class="bar-value">{current:.1f}%<span class="muted"> / {target:.0f}%</span></div>'
            f"</div>"
        )
    return (
        '<div class="bars">' + "".join(rows) + "</div>"
        '<p class="legend"><span class="swatch"></span> current weight &nbsp;'
        '<span class="tick"></span> policy target &nbsp;'
        '<span class="swatch breach"></span> outside the drift band</p>'
    )


def _recommendation_cards(recs: RecommendationSet | None) -> str:
    if recs is None or not recs.actions:
        return ""
    cards = []
    for rec in recs.actions:
        amount = "—" if rec.action == "HOLD" else _money(rec.dollars)
        conflict = (
            f'<p class="conflict"><strong>Policy conflict.</strong> {html.escape(rec.policy_conflict)}</p>'
            if rec.policy_conflict
            else ""
        )
        cards.append(
            f'<article class="rec {rec.action.lower()}">'
            f'<header><span class="verb">{rec.action}</span>'
            f'<span class="sym">{html.escape(rec.symbol or "—")}</span>'
            f'<span class="amt">{amount}</span></header>'
            f'<p class="why">{html.escape(rec.rationale)}</p>'
            f'<footer><code>{rec.reason_code}</code></footer>{conflict}'
            f"</article>"
        )
    unaddressed = (
        '<div class="unaddressed"><h3>Deliberately not acted on</h3><ul>'
        + "".join(f"<li>{html.escape(u)}</li>" for u in recs.unaddressed)
        + "</ul></div>"
        if recs.unaddressed
        else ""
    )
    return (
        f'<section id="actions"><h2>Recommended actions</h2>'
        f'<p class="lede">{html.escape(recs.summary)}</p>'
        f'<div class="recs">{"".join(cards)}</div>{unaddressed}</section>'
    )


def _truncation_banner(data: ReportData) -> str:
    """Say it before anything else, or do not say it at all.

    A note about incompleteness at the bottom of a financial memo is a note
    nobody reads. This sits above the title.
    """
    if not data.truncated:
        return ""
    agents = ", ".join(html.escape(a) for a in data.truncated)
    return (
        '<div class="truncated" role="alert">'
        "<strong>This review is incomplete.</strong> "
        f"{agents} reached the maximum number of model calls allowed for one run and "
        "was stopped before finishing. Sections may be missing or cut off, and the "
        "grounding score below describes only the part that was written. "
        "<em>Do not treat this as a finished review.</em>"
        "</div>"
    )


def _evidence_footer(data: ReportData) -> str:
    report = data.verification
    unsupported = sum(1 for f in report.failures if f.verdict is Verdict.UNSUPPORTED)
    needs_human = len(report.failures)
    meter_rows = ""
    if data.meter and data.meter.by_agent:
        rows = [
            [
                html.escape(agent),
                html.escape(usage["model"]),
                str(usage["calls"]),
                f"{usage['input_tokens']:,}",
                f"{usage['cache_read']:,}",
                f"{usage['output_tokens']:,}",
                f"${usage['cost_usd']:.3f}",
            ]
            for agent, usage in data.meter.to_json()["by_agent"].items()
        ]
        meter_rows = _table(
            ["Agent", "Model", "Calls", "Input", "From cache", "Output", "Cost"],
            rows,
            right_from=2,
        )
    return f"""
<section id="evidence">
  <h2>How far to trust this</h2>
  <div class="stats">
    <div class="stat"><b>{report.score:.1%}</b><span>of checkable claims grounded</span></div>
    <div class="stat"><b>{report.checked_figures}</b><span>figures checked</span></div>
    <div class="stat"><b>{report.checked_citations}</b><span>citations checked</span></div>
    <div class="stat{' warn' if needs_human else ''}"><b>{needs_human}</b><span>need a human</span></div>
  </div>
  <p class="review-surface">
    Every figure above was checked automatically against the tool output it came from.
    <strong>{report.checked_figures + report.checked_citations} claims were verified by machine;
    {needs_human} could not be and need a person.</strong>
    That ratio is the point of the exercise — reviewing {needs_human} flagged claims is a
    different job from re-deriving {report.checked_figures + report.checked_citations}.
    {'' if not unsupported else f'<br><em>Unsupported</em> means nothing recorded backs the figure. It may still be true; nobody can currently tell.'}
  </p>
  {meter_rows}
  {_voice_note(data)}
</section>
"""


def _voice_note(data: ReportData) -> str:
    """A quiet note on prose, not a verdict.

    Style is a distribution, not an invariant. Flagging it beside the grounding
    numbers without scoring it is the honest treatment — and it keeps the
    distinction the rest of the repo makes: assert on invariants, report on
    distributions.
    """
    findings = voice_lint(data.memo)
    if not findings:
        return ""
    items = "".join(
        f"<li><code>{html.escape(f.excerpt)}</code> — {html.escape(f.suggestion)} "
        f'<span class="muted">(line {f.line})</span></li>'
        for f in findings[:8]
    )
    more = (
        f"<li class=\"muted\">and {len(findings) - 8} more</li>" if len(findings) > 8 else ""
    )
    return (
        '<details class="voice"><summary>'
        f"{len(findings)} phrase(s) read as machine-written</summary>"
        "<p class=\"muted\">Style, not grounding. Nothing here affects whether a claim is "
        "supported; it affects whether the memo gets read.</p>"
        f"<ul>{items}{more}</ul></details>"
    )


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def render_report(data: ReportData) -> str:
    """Build the complete HTML document."""
    balances = data.balances
    total = balances.get("total_value")
    as_of = balances.get("as_of", "")
    tables = build_tables(data)
    annotated = annotate(data.memo, data.verification)
    memo_title, memo_body = split_title(annotated)
    body_md, slots = expand_tables(memo_body, tables)
    body = restore_tables(render_markdown(body_md, min_heading=3), slots)
    subtitle = f'<p class="lede">{memo_title}</p>' if memo_title else ""

    duration = (
        f"{data.elapsed_seconds / 60:.1f} min" if data.elapsed_seconds else "—"
    )
    cost = f"${data.meter.cost():.2f}" if data.meter and data.meter.by_agent else "—"
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<title>Wealth Review</title>
<style>{_CSS}</style>
<main>
{_truncation_banner(data)}
<header id="top">
  <p class="kicker">Wealth review · {html.escape(data.policy.name)} policy</p>
  <h1>What you should do with your money, and why</h1>
  <p class="explainer">
    This is an automated review of one brokerage account. It was built from three
    inputs — <strong>positions and balances</strong> from the brokerage,
    <strong>six months of card transactions</strong>, and the
    <strong>{html.escape(data.policy.name)}</strong> investment policy that says what this
    portfolio is supposed to look like. Everything below is derived from those three
    things and from market sources the agent read and stored.
    <br><br>
    <strong>Every figure on this page is clickable.</strong> Click one to see which tool
    produced it and which agent was holding it at the time. Figures that could not be
    traced back to a recorded observation are marked, rather than quietly left in.
  </p>
  <div class="meta">
    <div><b>{_money(total) if total else "—"}</b><span>total portfolio{f" · as of {html.escape(as_of)}" if as_of else ""}</span></div>
    <div><b>{duration}</b><span>to produce</span></div>
    <div><b>{cost}</b><span>in model cost</span></div>
    <div><b>{data.verification.score:.1%}</b><span>grounded</span></div>
  </div>
</header>

{_recommendation_cards(data.recommendations)}

<section id="allocation">
  <h2>Where the portfolio sits against policy</h2>
  {_allocation_bars(data)}
</section>

<section id="memo">
  <h2>The memo</h2>
  {subtitle}
  {body}
</section>

{_evidence_footer(data)}

<footer>
  <p>Run <code>{html.escape(data.workspace.run_id)}</code> · mode <code>{html.escape(data.mode)}</code>
  · generated {generated}</p>
  <p class="disclaimer">Generated by an AI agent for demonstration. Not financial advice.
  The underlying account data is synthetic.</p>
</footer>
</main>
<div id="pop" role="status" aria-live="polite"></div>
<script>{_JS}</script>
"""


def write_report(data: ReportData, path: Path | None = None) -> Path:
    """Render and write the report, returning where it landed."""
    target = path or (data.workspace.root / REPORT_FILE)
    target.write_text(render_report(data), encoding="utf-8")
    return target


def recommendations_from_ledger(workspace: RunWorkspace) -> RecommendationSet | None:
    """Rebuild the recommendation cards from the recorded `rebalance_plan` result.

    The strategist returns a typed `RecommendationSet`, but it does so *to the
    supervisor* — the structured response is consumed inside the delegation and
    is not something the report can reach afterwards.

    Reading the plan back out of the ledger is not a workaround for that; it is
    the better source. The cards then show exactly what the deterministic tool
    computed, with the model's argument for each one sitting in the memo
    alongside. If the two ever disagree, the disagreement is visible on the same
    page rather than hidden behind a model's paraphrase of its own tool call.
    """
    plans = [e for e in workspace.ledger.entries() if e.name == "rebalance_plan"]
    if not plans:
        return None
    try:
        plan = json.loads(plans[-1].content)
    except (json.JSONDecodeError, TypeError):
        return None

    actions = []
    for action in plan.get("actions", []):
        detail = action.get("detail", "")
        if action.get("policy_conflict"):
            pass  # carried separately below so the report can style it
        actions.append(
            Recommendation(
                action=action.get("action", "HOLD"),
                symbol=action.get("symbol"),
                dollars=float(action.get("dollars") or 0.0),
                reason_code=action.get("reason_code", "BELOW_MIN_TRADE"),
                rationale=detail,
                policy_conflict=action.get("policy_conflict"),
            )
        )
    if not actions:
        return None

    residual = plan.get("residual") or {}
    unaddressed = []
    if residual.get("left_uninvested"):
        unaddressed.append(
            f"${residual['left_uninvested']:,.2f} of trim proceeds has no "
            "policy-mandated destination — a discretionary decision the policy "
            "does not make for you."
        )
    unaddressed.extend(plan.get("caveats", []))

    return RecommendationSet(
        summary=(
            f"{len([a for a in actions if a.action != 'HOLD'])} trades bring every "
            f"breached sector back inside its band under the {plan.get('policy', '')} "
            "policy. Amounts are computed, not estimated."
        ),
        actions=actions,
        unaddressed=unaddressed,
    )


def build_report_data(
    workspace: RunWorkspace,
    *,
    mode: str = "verified",
    meter: RunMeter | None = None,
    recommendations: RecommendationSet | None = None,
    elapsed_seconds: float | None = None,
    policy: Policy | None = None,
    truncated: list[str] | None = None,
) -> ReportData:
    """Gather everything the report needs from a finished run."""
    memo = workspace.read_memo()
    if recommendations is None:
        recommendations = recommendations_from_ledger(workspace)
    return ReportData(
        workspace=workspace,
        memo=memo,
        verification=verify_memo(memo, workspace),
        policy=policy or load_policy(),
        meter=meter,
        recommendations=recommendations,
        mode=mode,
        elapsed_seconds=elapsed_seconds,
        truncated=list(truncated or []),
    )


_CSS = """
:root{
  --paper:#F4F5F2; --surface:#FFFFFF; --surface-2:#EAECE7; --ink:#14181C;
  --ink-soft:#414B52; --ink-faint:#6E7A80; --rule:#D6DBD5; --rule-firm:#AEB6AE;
  --accent:#1F6F66; --accent-soft:#DCE9E6;
  --ok:#2F6A46; --ok-bg:#E3EFE7; --warn:#8A5D1E; --warn-bg:#F4EADA;
  --bad:#973B33; --bad-bg:#F3E2E0;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --paper:#0E1114; --surface:#161B1F; --surface-2:#1E262A; --ink:#E5EAE7;
  --ink-soft:#AFBAB8; --ink-faint:#7E8B8A; --rule:#2A343A; --rule-firm:#3C4850;
  --accent:#6BBDB0; --accent-soft:#1B2E2C;
  --ok:#7FC69A; --ok-bg:#172A1F; --warn:#D9AC6A; --warn-bg:#2C2416;
  --bad:#E08A80; --bad-bg:#2E1B19;
}}
:root[data-theme="dark"]{
  --paper:#0E1114; --surface:#161B1F; --surface-2:#1E262A; --ink:#E5EAE7;
  --ink-soft:#AFBAB8; --ink-faint:#7E8B8A; --rule:#2A343A; --rule-firm:#3C4850;
  --accent:#6BBDB0; --accent-soft:#1B2E2C;
  --ok:#7FC69A; --ok-bg:#172A1F; --warn:#D9AC6A; --warn-bg:#2C2416;
  --bad:#E08A80; --bad-bg:#2E1B19;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
main{max-width:60rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}
h1{font-size:clamp(1.75rem,4vw,2.5rem);line-height:1.15;letter-spacing:-.02em;margin:.2em 0 .5em}
h2{font-size:1.35rem;letter-spacing:-.01em;margin:2.5rem 0 .75rem;padding-bottom:.4rem;
  border-bottom:1px solid var(--rule)}
h3{font-size:1.05rem;margin:1.6rem 0 .5rem}
h4{font-size:.95rem;margin:1.2rem 0 .4rem;color:var(--ink-soft)}
p{margin:.7em 0}
a{color:var(--accent)}
code{font-family:var(--mono);font-size:.87em;background:var(--surface-2);
  padding:.1em .35em;border-radius:4px}
.kicker{font-family:var(--mono);font-size:.78rem;text-transform:uppercase;
  letter-spacing:.09em;color:var(--accent);margin:0}
.explainer{color:var(--ink-soft);max-width:44rem;font-size:1.02rem}
.lede{font-size:1.06rem;color:var(--ink-soft);max-width:44rem}
.muted{color:var(--ink-faint)}
hr{border:0;border-top:1px solid var(--rule);margin:2rem 0}

.meta{display:flex;flex-wrap:wrap;gap:1.5rem;margin:1.75rem 0 0;padding:1.1rem 1.25rem;
  background:var(--surface);border:1px solid var(--rule);border-radius:12px}
.meta div{display:flex;flex-direction:column}
.meta b{font-size:1.3rem;letter-spacing:-.01em}
.meta span{font-size:.78rem;color:var(--ink-faint)}

.recs{display:grid;gap:.85rem;grid-template-columns:repeat(auto-fit,minmax(17rem,1fr));margin:1.25rem 0}
.rec{background:var(--surface);border:1px solid var(--rule);border-left:3px solid var(--rule-firm);
  border-radius:10px;padding:1rem 1.1rem}
.rec.trim{border-left-color:var(--warn)} .rec.buy{border-left-color:var(--ok)}
.rec.hold{border-left-color:var(--ink-faint)}
.rec header{display:flex;align-items:baseline;gap:.55rem;flex-wrap:wrap}
.verb{font-family:var(--mono);font-size:.72rem;letter-spacing:.08em;padding:.15rem .45rem;
  border-radius:4px;background:var(--surface-2);color:var(--ink-soft)}
.rec.trim .verb{background:var(--warn-bg);color:var(--warn)}
.rec.buy .verb{background:var(--ok-bg);color:var(--ok)}
.sym{font-weight:650;font-size:1.05rem}
.amt{margin-left:auto;font-family:var(--mono);font-size:1rem}
.why{font-size:.92rem;color:var(--ink-soft);margin:.5rem 0 .4rem}
.rec footer{font-size:.75rem;color:var(--ink-faint)}
.conflict{font-size:.85rem;background:var(--warn-bg);color:var(--warn);
  padding:.5rem .6rem;border-radius:6px;margin:.6rem 0 0}
.unaddressed{margin-top:1rem;font-size:.9rem;color:var(--ink-soft)}

.bars{display:flex;flex-direction:column;gap:.5rem;margin:1.25rem 0}
.bar-row{display:grid;grid-template-columns:minmax(6rem,11rem) 1fr minmax(5.5rem,auto);
  gap:.75rem;align-items:center;font-size:.87rem}
.bar-label{color:var(--ink-soft);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{position:relative;height:1.4rem;background:var(--surface-2);border-radius:5px}
.bar-fill{height:100%;background:var(--accent);border-radius:5px;opacity:.85}
.bar-fill.breach{background:var(--warn)}
.bar-target{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink);opacity:.65}
.bar-value{font-family:var(--mono);font-size:.8rem;text-align:right}
.legend{font-size:.78rem;color:var(--ink-faint);display:flex;align-items:center;gap:.35rem;flex-wrap:wrap}
.swatch{display:inline-block;width:.75rem;height:.75rem;border-radius:3px;background:var(--accent)}
.swatch.breach{background:var(--warn)}
.tick{display:inline-block;width:2px;height:.85rem;background:var(--ink);vertical-align:-2px}

.claim{border-bottom:1.5px solid transparent;cursor:pointer;border-radius:2px;padding:0 1px}
.claim.grounded{border-bottom-color:color-mix(in srgb,var(--ok) 45%,transparent)}
.claim.grounded:hover,.claim.grounded:focus{background:var(--ok-bg);outline:none}
.claim.unsupported{background:var(--warn-bg);border-bottom-color:var(--warn)}
.claim.fabricated{background:var(--bad-bg);border-bottom-color:var(--bad);font-weight:600}
.claim:focus-visible{outline:2px solid var(--accent);outline-offset:1px}

.scroll{overflow-x:auto;margin:1rem 0;border:1px solid var(--rule);border-radius:10px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:.87rem}
th,td{padding:.5rem .7rem;text-align:left;border-bottom:1px solid var(--rule);white-space:nowrap}
th{background:var(--surface-2);font-weight:600;font-size:.78rem;letter-spacing:.02em;
  text-transform:uppercase;color:var(--ink-soft)}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-family:var(--mono);font-size:.83rem}
.pos{color:var(--ok)} .neg{color:var(--bad)}
.flag{font-family:var(--mono);font-size:.68rem;background:var(--warn-bg);color:var(--warn);
  padding:.1rem .3rem;border-radius:3px;margin-left:.25rem}

.stats{display:flex;flex-wrap:wrap;gap:1rem;margin:1.1rem 0}
.stat{flex:1 1 8rem;background:var(--surface);border:1px solid var(--rule);border-radius:10px;
  padding:.85rem 1rem;display:flex;flex-direction:column}
.stat b{font-size:1.5rem;letter-spacing:-.02em}
.stat span{font-size:.76rem;color:var(--ink-faint)}
.stat.warn{border-color:var(--warn);background:var(--warn-bg)}
.truncated{background:var(--bad-bg);color:var(--bad);border:1px solid var(--bad);
  border-radius:10px;padding:1rem 1.25rem;margin:0 0 1.5rem;font-size:.95rem}
.voice{margin:1.25rem 0 0;font-size:.88rem;color:var(--ink-soft)}
.voice summary{cursor:pointer;color:var(--accent);font-size:.85rem}
.voice ul{margin:.5rem 0 0;padding-left:1.2rem}
.voice li{margin:.2rem 0}
.review-surface{font-size:.94rem;color:var(--ink-soft);max-width:46rem}

footer{margin-top:3rem;padding-top:1.25rem;border-top:1px solid var(--rule);
  font-size:.8rem;color:var(--ink-faint)}
.disclaimer{font-style:italic}

#pop{position:fixed;left:50%;bottom:1.25rem;transform:translateX(-50%) translateY(140%);
  max-width:min(38rem,92vw);background:var(--surface);color:var(--ink);
  border:1px solid var(--rule-firm);border-radius:10px;padding:.7rem 1rem;font-size:.85rem;
  box-shadow:0 8px 28px rgba(0,0,0,.18);transition:transform .16s ease;z-index:10}
#pop.show{transform:translateX(-50%) translateY(0)}
#pop .ev{font-family:var(--mono);font-size:.8rem;color:var(--accent)}
@media (max-width:640px){
  .bar-row{grid-template-columns:1fr;gap:.2rem}
  .bar-value{text-align:left}
  .meta{gap:1rem}
}
"""

_JS = """
(function(){
  var pop=document.getElementById('pop'),timer;
  function show(el){
    var ev=el.getAttribute('data-evidence')||'', d=el.getAttribute('data-detail')||'';
    pop.innerHTML='<div>'+d.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>'+
                  (ev?'<div class="ev">recorded by '+ev.replace(/</g,'&lt;')+'</div>':'');
    pop.classList.add('show');
    clearTimeout(timer); timer=setTimeout(function(){pop.classList.remove('show');},5000);
  }
  document.addEventListener('click',function(e){
    var el=e.target.closest('.claim');
    if(el){show(el);} else if(!e.target.closest('#pop')){pop.classList.remove('show');}
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Enter'&&e.target.classList&&e.target.classList.contains('claim')){show(e.target);}
    if(e.key==='Escape'){pop.classList.remove('show');}
  });
})();
"""

__all__ = [
    "REPORT_FILE",
    "ReportData",
    "annotate",
    "build_report_data",
    "build_tables",
    "recommendations_from_ledger",
    "expand_tables",
    "restore_tables",
    "render_report",
    "split_title",
    "write_report",
]
