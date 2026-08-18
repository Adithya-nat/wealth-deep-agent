"""The investment policy: the target the whole recommendation layer measures against.

Keeping this in a JSON file rather than in code is the point. "What should this
portfolio look like?" is a question for the person whose money it is, not for
the engineer who wrote the agent — and in an advised relationship it is a
document that gets signed. Putting it on disk means you can open it in front of
someone, change a number, re-run, and watch the recommendations move.

It also gives every recommendation an appealable reason. "Trim NVDA by $2,410"
is an instruction you either trust or do not. "Information Technology is 34.71%
of equity against a 25% target with a 5-point band" is a claim you can disagree
with by pointing at the target — which is a much better argument to be having.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from wealth_agent.config import REPO_ROOT

POLICIES_DIR = REPO_ROOT / "policies"

DEFAULT_POLICY = "balanced-growth"


@dataclass(frozen=True)
class Policy:
    """A parsed, validated investment policy."""

    name: str
    description: str
    asset_class_targets: dict[str, float]
    sector_targets: dict[str, float]
    drift_band: float
    max_single_name: float
    cash_reserve_months: float
    min_trade_usd: float
    preferred_instruments: dict[str, str]
    notes: list[str]

    def target_for(self, sector: str) -> float:
        """Target weight for a sector, as a percentage of equity.

        Unknown sectors target zero rather than raising: a holding in a sector
        the policy never contemplated is a real thing that happens, and the
        honest reading is "the policy allocates nothing here", which surfaces
        as drift rather than as a crash mid-run.
        """
        return self.sector_targets.get(sector, 0.0)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "asset_class_targets_percent_of_total": self.asset_class_targets,
            "sector_targets_percent_of_equity": self.sector_targets,
            "drift_band_percentage_points": self.drift_band,
            "max_single_name_percent_of_total": self.max_single_name,
            "cash_reserve_months": self.cash_reserve_months,
            "min_trade_usd": self.min_trade_usd,
            "preferred_instruments": self.preferred_instruments,
            "notes": self.notes,
        }


class PolicyError(ValueError):
    """A policy file that would produce nonsense recommendations."""


@lru_cache(maxsize=None)
def load_policy(name: str = DEFAULT_POLICY) -> Policy:
    """Load and validate `policies/<name>.json`.

    Validation is not ceremony here. Every dollar amount the agent recommends is
    derived from these numbers, so a policy whose sector targets sum to 140%
    produces a rebalancing plan that is arithmetically confident and completely
    wrong. Better to refuse to start.
    """
    path = POLICIES_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in POLICIES_DIR.glob("*.json"))) or "none"
        msg = f"no policy named {name!r}. Available: {available}"
        raise PolicyError(msg)

    raw = json.loads(path.read_text(encoding="utf-8"))
    sectors = raw.get("sector_targets_percent_of_equity", {})
    classes = raw.get("asset_class_targets_percent_of_total", {})

    for label, weights in (("sector", sectors), ("asset class", classes)):
        if not weights:
            msg = f"{path.name} defines no {label} targets"
            raise PolicyError(msg)
        total = sum(weights.values())
        if abs(total - 100.0) > 0.01:
            msg = f"{path.name}: {label} targets sum to {total:.2f}%, expected 100%"
            raise PolicyError(msg)
        negative = [k for k, v in weights.items() if v < 0]
        if negative:
            msg = f"{path.name}: negative {label} target for {negative}"
            raise PolicyError(msg)

    band = float(raw.get("drift_band_percentage_points", 5.0))
    if band <= 0:
        msg = f"{path.name}: drift band must be positive, got {band}"
        raise PolicyError(msg)

    return Policy(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        asset_class_targets=dict(classes),
        sector_targets=dict(sectors),
        drift_band=band,
        max_single_name=float(raw.get("max_single_name_percent_of_total", 10.0)),
        cash_reserve_months=float(raw.get("cash_reserve_months", 4.0)),
        min_trade_usd=float(raw.get("min_trade_usd", 1000.0)),
        preferred_instruments=dict(raw.get("preferred_instruments", {})),
        notes=list(raw.get("notes", [])),
    )


__all__ = ["DEFAULT_POLICY", "POLICIES_DIR", "Policy", "PolicyError", "load_policy"]
