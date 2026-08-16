"""Runtime configuration.

Everything that varies between "running on my laptop against a real brokerage
account" and "running on stage in front of an audience" is funnelled through
this module, so there is exactly one place to check before you share a screen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Scrubbed fixtures that ship with the repo. Safe to commit, safe to present.
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

#: Raw, unredacted responses captured from live MCP calls. Gitignored. Nothing
#: in here is ever presented or committed; `wealth capture scrub` promotes a
#: redacted copy into ARTIFACTS_DIR.
CAPTURES_DIR = REPO_ROOT / "captures"

#: OAuth tokens and dynamically-registered client credentials. Outside the repo
#: on purpose — a gitignore typo should not be able to leak a refresh token.
AUTH_DIR = Path.home() / ".wealth-agent"

TRADING_MCP_URL = os.getenv(
    "ROBINHOOD_TRADING_MCP_URL", "https://agent.robinhood.com/mcp/trading"
)
BANKING_MCP_URL = os.getenv(
    "ROBINHOOD_BANKING_MCP_URL", "https://agent.robinhood.com/mcp/banking"
)


def _flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings.

    Attributes:
        demo_mode: Replay scrubbed fixtures instead of calling live MCP
            servers. Defaults to ``True`` — you have to opt *in* to touching a
            real account, never out of it.
        allow_write_tools: Expose order-placement and card-purchase tools to
            the model at all. Independent of the human-in-the-loop gate: even
            when this is ``True``, those tools still interrupt for approval.
            Two switches because "the model can see it" and "it happens without
            me" are different risks.
    """

    demo_mode: bool = True
    allow_write_tools: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            demo_mode=_flag("DEMO_MODE", default=True),
            allow_write_tools=_flag("ALLOW_WRITE_TOOLS", default=False),
        )

    @property
    def is_live(self) -> bool:
        return not self.demo_mode


SETTINGS = Settings.from_env()


def require_env(name: str) -> str:
    """Read a required environment variable, failing with a usable message."""
    value = os.getenv(name)
    if not value:
        msg = (
            f"{name} is not set. Copy .env.example to .env and fill it in, "
            f"or export {name} in your shell."
        )
        raise RuntimeError(msg)
    return value
