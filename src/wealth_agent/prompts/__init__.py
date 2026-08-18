"""Prompts as files, not string constants.

Every system prompt in this repo lives in a `.md` file next to this one. That
is not tidiness; it changes what a prompt *is* in the development process:

* **It gets a diff.** The workshop's central comparison — a careful agent
  against a naive one — is `diff supervisor.md supervisor_naive.md`, two files
  that differ only in the discipline they impose. As Python constants that
  comparison was a branch in a build function and you had to take it on trust.
* **It gets reviewed by the person who owns the wording.** In a regulated
  domain the wording of "never state a figure no tool returned" is a compliance
  concern, and compliance reviewers do not read `subagents.py`.
* **It gets a version.** `judge/v1.md` through `v3.md` are the three judge
  prompts whose measured agreement is the point of Loop 0. Reading them side by
  side is the demo; reading three adjacent string literals is not.

The format mirrors `skills/` — YAML frontmatter, then the body — so the repo has
one convention for "instructions on disk" rather than two.

Substitution uses `${name}` rather than `str.format`'s braces, because these
prompts contain JSON examples and `{"action": "TRIM"}` should not have to be
escaped to appear in one.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Prompt:
    """One prompt file: its metadata and its body."""

    name: str
    meta: dict[str, Any]
    body: str

    def render(self, **variables: Any) -> str:
        """Substitute `${name}` placeholders.

        Unknown placeholders are left alone rather than raising: a prompt that
        mentions `${some_shell_var}` in an example should not break the build.
        """
        return Template(self.body).safe_substitute(**variables)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    _, _, rest = text.partition("---")
    front, sep, body = rest.partition("---")
    if not sep:  # an opening fence with no closing one is a malformed file
        return {}, text.strip()
    return yaml.safe_load(front) or {}, body.strip()


@lru_cache(maxsize=None)
def get(name: str) -> Prompt:
    """Load `prompts/<name>.md`.

    Args:
        name: Path relative to this directory, without the extension —
            `"supervisor"` or `"judge/v3"`.

    Raises:
        FileNotFoundError: With the list of prompts that do exist, because the
            failure mode this replaces is a typo in a string constant that
            silently produced an agent with no instructions.
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in PROMPTS_DIR.rglob("*.md")))
        msg = f"no prompt named {name!r}. Available: {available}"
        raise FileNotFoundError(msg)
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return Prompt(name=name, meta=meta, body=body)


def render(name: str, **variables: Any) -> str:
    """Load a prompt and substitute its variables in one call."""
    return get(name).render(**variables)


def names() -> list[str]:
    """Every prompt on disk, for tests and for `wealth doctor`."""
    return sorted(
        str(p.relative_to(PROMPTS_DIR).with_suffix("")) for p in PROMPTS_DIR.rglob("*.md")
    )


__all__ = ["PROMPTS_DIR", "Prompt", "get", "names", "render"]
