"""Step 2: state abstraction, the agent's phase (and last outcome) instead of the raw label, repeats collapsed.

Abstractions, from coarse to fine:

* ``phase``           EXPLORE, PLAN, EDIT, TEST, DEBUG, VERIFY, SETUP, DELEGATE, ASK_USER, USER_TURN, DONE, …
* ``phase+outcome``   the phase plus what matters about it: TEST:fail, VERIFY:ok, EDIT:fail, USER_TURN:CORRECTION
* ``tail:K``          the last K phase+outcome states together (a k-tail): exact, and cluttered
* ``label``           the action labels themselves (EXPLORE:test, TEST:fail, USER:APPROVE, …)

DEBUG is derived from a window of the last three steps: exploring or running something right after a failure.
Coarser abstractions merge more paths (and can show some that never happened); finer ones are exact but cluttered.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .labels import Action

_PHASES = {
    "EXPLORE": "EXPLORE", "WEB": "EXPLORE", "SKILL": "EXPLORE", "EDIT": "EDIT", "TEST": "TEST", "BUILD": "VERIFY",
    "RUN": "VERIFY", "GIT": "VERIFY", "INSTALL": "SETUP", "SHELL": "OTHER", "TOOL": "OTHER", "DELEGATE": "DELEGATE",
    "PLAN": "PLAN", "ASK_USER": "ASK_USER", "USER": "USER_TURN", "FINAL_SUMMARY": "DONE", "COMPACT": "COMPACT",
    "API_ERROR": "ERROR",
}  # fmt: skip
_SKIPPED = {"EXPLAIN", "THINK", "MODEL"}  # Narration and bookkeeping: they do not change what the agent is doing.
_ALWAYS_OUTCOME = {"TEST", "VERIFY", "SETUP", "OTHER"}
ABSTRACTIONS = ("phase", "phase+outcome", "label")
_TAIL = re.compile(r"tail:(\d+)")


def check_abstraction(name: str) -> str:
    if name in ABSTRACTIONS or ((match := _TAIL.fullmatch(name)) and 1 <= int(match.group(1)) <= 5):
        return name
    raise ValueError(f"Unknown abstraction {name!r}; use phase, phase+outcome, label or tail:K (K = 1..5)")


@dataclass
class State:
    """A run of identical abstract states, collapsed: ``repeat`` steps of it in a row."""

    name: str
    repeat: int = 0
    actions: list[Action] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(action.tokens for action in self.actions)

    @property
    def cost(self) -> float:
        return sum(action.cost for action in self.actions)

    @property
    def start(self) -> str | None:
        return next((action.start for action in self.actions if action.start), None)

    @property
    def end(self) -> str | None:
        return next((action.end for action in reversed(self.actions) if action.end), None)

    @property
    def failed(self) -> bool:
        return any(action.outcome == "fail" for action in self.actions)


def phase(action: Action, window: Sequence[Action]) -> str | None:
    """The phase of ``action`` given the steps before it; None for narration that does not change it."""
    if action.family in _SKIPPED:
        return None
    base = _PHASES.get(action.family, "OTHER")
    if base in {"EXPLORE", "VERIFY", "OTHER", "SETUP"} and any(step.outcome == "fail" for step in window[-3:]):
        return "DEBUG"
    return base


def _with_outcome(name: str, action: Action) -> str:
    if name == "USER_TURN":
        return f"USER_TURN:{action.label.split(':', 1)[1]}"
    if action.outcome is None:
        return name
    if name in _ALWAYS_OUTCOME:
        return f"{name}:{action.outcome}"
    return f"{name}:fail" if action.outcome == "fail" else name


def abstract(actions: Sequence[Action], abstraction: str = "phase+outcome") -> list[State]:
    """The collapsed state sequence of a case's actions under ``abstraction``."""
    check_abstraction(abstraction)
    named: list[tuple[str, Action]] = []
    window: list[Action] = []
    detailed: list[str] = []
    for action in actions:
        if abstraction == "label":
            if action.family not in {"THINK", "MODEL"}:
                named.append((action.label, action))
            continue
        current = phase(action, window)
        if current is None:
            continue
        window.append(action)
        fine = _with_outcome(current, action)
        detailed.append(fine)
        if abstraction == "phase":
            named.append((current, action))
        elif abstraction == "phase+outcome":
            named.append((fine, action))
        else:
            k = int(abstraction.split(":")[1])
            named.append((" > ".join(detailed[-k:]), action))
    states: list[State] = []
    for name, action in named:
        if states and states[-1].name == name:
            states[-1].repeat += 1
            states[-1].actions.append(action)
        else:
            states.append(State(name, 1, [action]))
    return states


def coarsen(name: str) -> str:
    """The ``phase`` state a finer state belongs to (for checking a pattern under a coarser abstraction)."""
    last = name.split(" > ")[-1]
    head = last.split(":")[0]
    return _PHASES.get(head, head) if head not in _PHASES.values() else head
