"""Getting the finished report in front of a person.

The browser is the primary channel and email is opt-in, which is the opposite of
how this usually gets built. The reason is demo discipline: opening a local file
cannot fail because someone's SMTP credentials expired or a corporate relay is
slow, and the one moment you cannot afford a "hmm, it should have arrived" is
while a room is watching.

So email never fails the run. If it does not go out, the report is still on disk
and still open on screen, and the failure is reported rather than raised.
"""

from __future__ import annotations

import os
import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Delivery:
    """What actually happened, so the caller can tell the user honestly."""

    path: Path
    opened: bool = False
    emailed_to: str | None = None
    email_error: str | None = None


def open_in_browser(path: Path) -> bool:
    """Open a local file in the default browser. Never raises."""
    try:
        return webbrowser.open(path.resolve().as_uri())
    except Exception:  # noqa: BLE001 — a headless box is not a failed run
        return False


def send_email(path: Path, to: str, *, subject: str | None = None) -> str | None:
    """Email the report as HTML. Returns an error string, or None on success.

    Uses Resend because it needs one API key and no infrastructure. The
    alternative — SMTP with an app password — works too and is worse to explain
    on stage.
    """
    key = os.getenv("RESEND_API_KEY")
    if not key:
        return "RESEND_API_KEY is not set, so no email was sent."
    try:
        import resend
    except ImportError:
        return "the `resend` package is not installed (`uv sync`), so no email was sent."

    try:
        resend.api_key = key
        resend.Emails.send(
            {
                "from": os.getenv("REPORT_FROM", "Wealth Agent <onboarding@resend.dev>"),
                "to": [to],
                "subject": subject or "Your wealth review",
                "html": path.read_text(encoding="utf-8"),
            }
        )
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        return f"{type(exc).__name__}: {exc}"
    return None


def deliver(path: Path, *, open_browser: bool = True, email_to: str | None = None) -> Delivery:
    """Open and optionally email a finished report."""
    result = Delivery(path=path)
    if open_browser:
        result.opened = open_in_browser(path)
    if email_to:
        error = send_email(path, email_to)
        if error:
            result.email_error = error
        else:
            result.emailed_to = email_to
    return result


__all__ = ["Delivery", "deliver", "open_in_browser", "send_email"]
