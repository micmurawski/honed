"""What a round will cost, worked out before it runs: from the size of its sessions and what earlier runs cost.

The evidence is each session as the reflector reads it (exported to its harness, about four characters a token). The
cost comes from this workspace's journal: the reflections that recorded their cost and the size of their evidence give a
cost per evidence token (or, without sizes, per reflection), and the curations give a cost per curation. A first round
has no history, so it gets the tokens alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from thearc.reflection.reflector import estimate_tokens
from thearc.resources import MetaAgent
from thearc.sessions import Session, Thread
from thearc.sessions.export import export_events


@dataclass(frozen=True)
class Estimate:
    sessions: int  # To reflect on.
    tokens: int  # Of evidence: the sessions as the reflector reads them.
    cost: float | None  # USD for the reflections and one curation; None without a cost history.
    basis: str  # Where the cost came from.
    reused: int = 0  # Sessions reflected on in an earlier round: their reflections are reused, at no cost.

    def __str__(self) -> str:
        reused = f" (and {self.reused} reflected on before, reused)" if self.reused else ""
        sized = f"{self.sessions} session(s){reused}, ~{_tokens(self.tokens)} tokens of evidence"
        return f"{sized}; about ${self.cost:.2f} ({self.basis})" if self.cost is not None else f"{sized}; {self.basis}"

    def to_dict(self) -> dict[str, object]:
        return {"sessions": self.sessions, "reused": self.reused, "tokens": self.tokens, "cost": self.cost,
                "basis": self.basis}


def estimate(sessions: Sequence[Session | Thread], runtime: str, agent: MetaAgent, *, curations: int = 1,
             reused: int = 0) -> Estimate:
    """The cost of reflecting on ``sessions`` with ``runtime`` and curating ``curations`` times, from ``agent``'s
    journal; ``reused`` more sessions are counted, at no cost."""
    sizes = [estimate_tokens(export_events(session.selected_events, runtime, strict=False).text)
             for session in sessions]
    journal = agent.journal
    costed = [(kept.evidence.get("tokens"), cost) for kept in journal.reflections
              if (cost := (kept.reflector.get("usage") or {}).get("cost")) is not None]
    sized = [(tokens, cost) for tokens, cost in costed if tokens]
    curated: dict[str, float] = {}
    for entry in journal.entries:
        extra = entry.extra
        if extra.get("curation") and (extra.get("usage") or {}).get("cost") is not None:
            curated[extra["curation"]] = extra["usage"]["cost"]
    curation = fmean(curated.values()) if curated else 0.0
    if sized:
        rate = sum(cost for _, cost in sized) / sum(tokens for tokens, _ in sized)
        reflections, basis = rate * sum(sizes), f"from {len(sized)} earlier reflection(s)"
    elif costed:
        reflections, basis = fmean(cost for _, cost in costed) * len(sizes), f"from {len(costed)} earlier reflection(s)"
    else:
        return Estimate(len(sizes), sum(sizes), None, "no cost history yet: later rounds are estimated from this one",
                        reused)
    basis += f" and {len(curated)} curation(s)" if curated else ", curation not included"
    return Estimate(len(sizes), sum(sizes), reflections + curation * curations, basis, reused)


def _tokens(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}k" if n >= 1000 else str(n)
