"""A markdown subset renderer, written rather than imported.

Two reasons this is not a dependency. First, the memo uses a small, known
subset — headings, paragraphs, tables, bullets, bold, rules — and a general
renderer brings a parser whose escaping behaviour I would have to verify
anyway. Second and more important: the report inserts `<span>` elements into the
memo at exact character offsets *before* rendering, so the renderer has to pass
inline HTML through untouched and in the right place. That is a property worth
owning rather than discovering.

Everything that is not an intentionally inserted span is escaped, so a memo that
happens to contain `<script>` renders as text.
"""

from __future__ import annotations

import html
import re

#: Spans this module inserts, which must survive escaping. Anything else
#: angle-bracketed in the memo is content and gets escaped.
_ALLOWED = re.compile(
    r"\x00(/?)(span|a)((?:\s[^\x00]*?)?)\x01"
)

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def protect(tag: str) -> str:
    """Wrap a trusted HTML tag in sentinels so escaping leaves it alone."""
    inner = tag[1:-1]
    closing = "/" if inner.startswith("/") else ""
    inner = inner.lstrip("/")
    name, _, attrs = inner.partition(" ")
    return f"\x00{closing}{name}{(' ' + attrs) if attrs else ''}\x01"


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = _ALLOWED.sub(lambda m: f"<{m.group(1)}{m.group(2)}{m.group(3)}>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(r'<a href="\2" rel="noopener noreferrer" target="_blank">\1</a>', text)
    return text


def _is_divider(row: str) -> bool:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def render(markdown: str, *, min_heading: int = 2) -> str:
    """Render the memo subset to HTML.

    Args:
        min_heading: The most prominent heading level the content may produce.
            The report passes 3, because the memo sits inside a section that
            already owns an `<h2>` — and a memo whose title is not its first
            line (some runs open with a cover sentence) would otherwise emit a
            second one. Clamping is more robust than lifting the title out,
            because it holds regardless of how the model laid the memo out.
    """
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            level = max(min_heading, min(len(heading.group(1)) + 1, 6))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        # table: a header row followed by a divider row
        if stripped.startswith("|") and i + 1 < len(lines) and _is_divider(lines[i + 1]):
            header = _cells(stripped)
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_cells(lines[i]))
                i += 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            out.append(
                f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{rows}</tbody></table></div>"
            )
            continue

        if re.match(r"[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines) and re.match(r"\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue

        if re.match(r"\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue

        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("|", "#", "-", "*")):
            para.append(lines[i].strip())
            i += 1
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
        else:
            out.append(f"<p>{_inline(stripped)}</p>")
            i += 1

    return "\n".join(out)


__all__ = ["protect", "render"]
