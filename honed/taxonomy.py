"""The taxonomy of failure modes: what an agent's session can go wrong by.

It merges MAST (Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*, 2025: 14 modes in three categories) with
AgentRx (critical-step localisation and its failure categories) for single coding agents. One table serves the session
analysis (``honed.sessions.analysis.aeg``: its rules and judge find these modes in sessions) and the rating scale
reflections use (``honed.evolve.FAILURE_MODES``: which mode cited configuration lines contributed to), so they never
drift apart. It depends on nothing else in honed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    """A failure mode of the taxonomy."""

    id: str
    category: str  # specification, execution, verification, tooling, system
    source: str  # Where it comes from: MAST 3.1, AgentRx, …
    definition: str
    detected_by: str  # rules, judge, or rules+judge


TAXONOMY: tuple[Mode, ...] = (
    # Specification and planning (MAST category 1).
    Mode("disobey_task_spec", "specification", "MAST 1.1",
         "The agent ignored a stated constraint or requirement of the task.", "judge"),
    Mode("step_repetition", "specification", "MAST 1.3",
         "The same call with the same arguments and the same outcome, three or more times, with no edit between.",
         "rules+judge"),
    Mode("history_loss", "specification", "MAST 1.4",
         "After a context compaction the agent re-read what it had already read, having lost it.", "rules+judge"),
    Mode("unaware_of_termination", "specification", "MAST 1.5",
         "The agent did not recognise when the task was finished, or stopped before it was.", "judge"),
    # Execution and alignment (MAST category 2, AgentRx).
    Mode("failed_to_clarify", "execution", "MAST 2.2",
         "A short, ambiguous request acted on without exploring or asking, then corrected by the user.", "rules+judge"),
    Mode("task_derailment", "execution", "MAST 2.3",
         "The agent drifted from the task to other work.", "judge"),
    Mode("ignored_input", "execution", "MAST 2.4",
         "The agent ignored what the user or a tool result told it.", "judge"),
    Mode("delegation_failed", "execution", "MAST 2.5",
         "A subagent failed or was abandoned while the parent went on to claim success.", "rules+judge"),
    Mode("reasoning_action_mismatch", "execution", "MAST 2.6",
         "What the agent said it would do differs from what it did.", "judge"),
    Mode("plan_adherence", "execution", "AgentRx",
         "The agent departed from its own plan, or the user's, without saying why.", "judge"),
    Mode("invented_information", "execution", "AgentRx",
         "The agent stated facts (paths, APIs, results) that nothing in the session supports.", "judge"),
    # Verification and termination (MAST category 3).
    Mode("premature_done", "verification", "MAST 3.1",
         "The agent finished while the last check after its last edit was failing.", "rules+judge"),
    Mode("claim_contradicted", "verification", "AgentRx",
         "The final answer claims success that the last check contradicts.", "rules+judge"),
    Mode("no_verification", "verification", "MAST 3.2",
         "The agent edited code and finished without running any check after its last edit.", "rules+judge"),
    Mode("incorrect_verification", "verification", "MAST 3.3",
         "A check passed, yet the user corrected the result before any further edit.", "rules+judge"),
    # Tools and environment (AgentRx).
    Mode("invalid_invocation", "tooling", "AgentRx",
         "A tool was called with invalid arguments or does not exist.", "rules+judge"),
    Mode("guardrail_block", "tooling", "AgentRx",
         "A permission, approval or sandbox rule blocked the step.", "rules+judge"),
    Mode("system_failure", "system", "AgentRx",
         "The model API or the environment failed: errors, timeouts, rate limits.", "rules+judge"),
)  # fmt: skip
MODES = {mode.id: mode for mode in TAXONOMY}
