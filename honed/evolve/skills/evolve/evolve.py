# The /evolve recipe: the last week's sessions → take the reflections made on them already → reflect on the rest →
# curate. Edit freely.
"""How a round of `honed evolve` proposes a new version.

`honed evolve` runs `propose(evolution)` and turns what it returns into the proposal. The round is a flow — find the
sessions, partition them by what earlier reflections cover, source those reflections, reflect on the rest, curate —
so `evolution.track` can show it live at .honed/cache/progress.html, with the round's log (`evolution.log`) in a tab.
The prompts are the Markdown files in prompts/ next to this script.

Reflections are made two ways: during a session, by its own agent (the /reflect skill, `mode: in-session`), and here,
by a reflector handed the session (`mode: handoff`). Both are kept in the session index, beside the sessions they
judge (`honed sessions query 'select:reflection'`), so a round reflects only on what no reflection covers yet.
"""

from collections import Counter
from datetime import timedelta
from pathlib import Path

from honed.curation import Curator
from honed.evolve import FAILURE_MODES, SCHEMA, USEFULNESS, Evolution, Partition
from honed.flow import Flow
from honed.flow.decorators import node
from honed.reflection import Prompt, ReflectionError, Reflector
from honed.resources import AgentConfig
from honed.sessions import Session, Thread
from honed.sessions.analysis.aeg import focus, session_failures

PROMPTS = Path(__file__).parent / "prompts"
REFLECT, CURATE = (Prompt.load(PROMPTS / f"{name}.md") for name in ("reflect", "curate"))
HARNESS = "claude"  # Whose sessions are read: the harness this project evolves (set at install).
RUNTIME = "claude"  # Runs every reflection and the curation.
WINDOW = timedelta(days=7)
MIN_SESSIONS = 3
BUDGET: float | None = None  # In USD: stop before reflecting when the round is estimated to cost more (e.g. 2.0).
TIMEOUT = 900  # Seconds an agent's turn may take before it counts as stalled and fails.
# What of a session the reflector is handed: "session" (all of it, subagent threads appended to the main one as if
# the same agent went on) or "root" (only the root thread, the main agent's own run; subagents show as their calls).
REFLECT_ON = "session"
# Failure modes found by rules in each session (premature_done, no_verification, step_repetition, …; no model calls):
# FOCUS hands them to its reflector as hints of where to look. SELECT "findings" reflects only on sessions where the
# rules found one, spending the reflection budget where there is evidence; "all" reflects on every session.
FOCUS = True
SELECT = "all"
# Reflections made already — in the sessions themselves (/reflect) or by earlier rounds — are taken, not made again.
SOURCE = True  # False: reflect on every session, whatever covers it.
SOURCE_MATCH = "version"  # Which earlier prompts ask the same question: "hash" (same text), "version", "name".
SOURCE_MODES = ("in-session", "handoff")
TAILS = "reflect"  # A partly covered session: "reflect" on what came after its reflections, or "ignore" it.
TAIL_MIN_EVENTS = 20  # A shorter tail is not worth a reflection: the session counts as covered.


def history(session):
    """What of ``session`` the reflector is handed (REFLECT_ON). A session with one thread, or without a single root
    thread (a Codex subagent's, whose parent is another session), is handed whole."""
    if REFLECT_ON == "root" and len(session.threads) > 1 and len(session.root_threads) == 1:
        return session.root_thread
    return session


# Each step of the round is a node: a function whose parameters are read from the round's shared state by name, and
# whose returned dict is merged back into it ("return state, action" picks the next step). validate=False passes the
# round's live objects (the evolution, sessions, the candidate worktree) as they are.


@node(validate=False, metadata={"harness": HARNESS, "window": f"{WINDOW.days} days", "min": MIN_SESSIONS})
def find_sessions(evolution: Evolution):
    """The harness's sessions run in this project in the last WINDOW; enough of them to learn from?"""
    sessions = evolution.sessions(harness=HARNESS, since=WINDOW)  # Never one that ran /evolve.
    evolution.log.info(f"{len(sessions)} {HARNESS} session(s) in the last {WINDOW.days} days")
    for session in sessions:
        evolution.log.info(f"  {session.id}  {session.metadata.started_at}  {session.metadata.title or ''}")
    if len(sessions) < MIN_SESSIONS:
        evolution.nothing(f"{len(sessions)} session(s) in the last {WINDOW.days} days; {MIN_SESSIONS} needed")
        return None, "too few"
    return {"sessions": sessions}, "enough"


@node(validate=False)
def too_few():
    """Nothing to propose yet."""


@node(validate=False)
def stop():
    """Estimated only, or over the budget: nothing reflected on."""


@node(validate=False, metadata={"prompt": REFLECT.label, "match": SOURCE_MATCH, "budget": BUDGET})
def partition(evolution: Evolution, sessions: list):
    """The sessions by what reflections made already cover: covered, partly covered, not reflected on."""
    if not SOURCE:
        parts, to_reflect = evolution.partition([], REFLECT, SCHEMA), [history(s) for s in sessions]
    else:
        parts = evolution.partition([history(s) for s in sessions], REFLECT, SCHEMA, match=SOURCE_MATCH,
                                    modes=SOURCE_MODES, tail_min=TAIL_MIN_EVENTS)
        tails = list(parts.partial) if TAILS == "reflect" else []
        to_reflect = [part.history for part in parts.unreflected] + [part.history for part in tails]
    evolution.log.info(f"{parts} (by {REFLECT.label}, matched by {SOURCE_MATCH})" if SOURCE
                       else "taking no reflections made already: reflecting on every session")
    for label, covered in (("covered", parts.covered), ("partly covered", parts.partial)):
        for part in covered:
            made = ", ".join(sorted(f"{n} {mode}" for mode, n in Counter(r.mode for r in part.reflections).items()))
            tail = f"; {part.tail} event(s) after them" if part.tail else ""
            evolution.log.info(f"  {part.session.id}: {label} by {len(part.reflections)} reflection(s) ({made}){tail}")
    hints = {}
    if FOCUS or SELECT == "findings":  # Rules, no model calls: only where a reflector will look.
        found = session_failures(to_reflect)
        hints = {sid: focus(*found[sid]) for sid in found if found[sid][0]}  # Only with findings.
        for sid, (findings, _) in found.items():
            if findings:
                evolution.log.info(f"  {sid}: {', '.join(sorted({f.mode for f in findings}))}")
        if SELECT == "findings":
            to_reflect = [h for h in to_reflect if found.get(_sid(h), ([], None))[0]]
            evolution.log.info(f"{len(to_reflect)} session(s) with failure findings to reflect on")
    # Size, earlier costs, and what is reused: of what each reflector will be handed.
    estimate = evolution.estimate(to_reflect, RUNTIME, prompt=REFLECT, sourced=len(parts.covered))
    if evolution.estimate_only:
        evolution.nothing(f"estimate only: {estimate}; {parts}")
        return None, "stop"
    if BUDGET is not None and estimate.cost is not None and estimate.cost > BUDGET:
        evolution.nothing(f"estimated at ${estimate.cost:.2f}, over the budget of ${BUDGET:.2f}")
        return None, "stop"
    return {
        "partition": parts,
        "to_reflect": to_reflect,
        "tails": {part.session.id: part for part in parts.partial} if TAILS == "reflect" else {},
        "focus": hints if FOCUS else {},
        "candidate": evolution.candidate(),  # A worktree of the installed release.
    }, "go"


@node(validate=False, metadata={"modes": ", ".join(SOURCE_MODES)})
def source(evolution: Evolution, candidate: AgentConfig, partition: Partition):
    """The reflections made already on covered and partly covered sessions, into the candidate: no model call."""
    recorded = evolution.source(candidate, partition.sourced)
    modes = Counter(reflection.mode for reflection in partition.sourced)
    evolution.log.info(f"sourced {recorded} reflection(s) made already "
                       f"({', '.join(f'{n} {mode}' for mode, n in sorted(modes.items())) or 'none'})")


@node(batch=True, items_key="to_reflect", item="handed", results_key="reflected", validate=False,
      metadata={"runtime": RUNTIME, "prompt": "prompts/reflect.md"})
def reflect(handed: Session | Thread, evolution: Evolution, candidate: AgentConfig, focus: dict, tails: dict):
    """Each session no reflection covers yet (or the tail of one partly covered), reviewed against the installed
    configuration: which lines helped, which hurt."""
    sid = _sid(handed)
    if evolution.reflected(handed, candidate):  # A stopped round kept its reflections.
        evolution.log.info(f"{sid}: reflected on in an earlier, stopped run")
        return
    if (reused := evolution.recall(handed, candidate, REFLECT)) is not None:
        evolution.log.info(f"{sid}: reused {reused.id} from an earlier round (no model call)")
        return
    tail = tails.get(sid)
    part = f" (root thread {handed.id[:12]}, without its subagents)" if REFLECT_ON == "root" and handed.id != sid \
        else ""
    hints = [hint for hint in (focus.get(sid), _tail_focus(tail)) if hint]
    evolution.log.info(f"{sid}{part}: reflecting with {RUNTIME}"
                       + (f" on its last {tail.tail} event(s)" if tail else "") + "…")
    try:
        reflection = Reflector(RUNTIME, timeout=TIMEOUT).handoff(
            REFLECT, handed, candidate, SCHEMA, provenance=evolution.provenance(_session(handed)),
            focus="\n\n".join(hints) or None, judged_from=tail.tail_from if tail else None)
    except ReflectionError as error:  # One failed reflection costs its session, not the round.
        evolution.log.warning(f"{sid}: no reflection ({error}); going on without it")
        return
    evolution.keep(reflection)  # For later rounds and queries, whatever this round proposes.
    candidate.add_reflection(reflection)
    rated = Counter(item.rating for item in reflection)
    evolution.log.info(f"{sid}: {len(reflection)} rating(s) "
                       f"({', '.join(f'{value} {label}' for label, value in rated.items()) or 'none'})")


@node(validate=False, metadata={"runtime": RUNTIME, "prompt": "prompts/curate.md"})
def curate(evolution: Evolution, candidate: AgentConfig):
    """The configuration changed where the reflections show it hurt, and kept where they show it helped."""
    if not evolution.curated(candidate):
        reflections = evolution.reflections(candidate)
        if not reflections:
            evolution.nothing("no reflection succeeded")
            return None
        modes = Counter(reflection.mode for reflection in reflections)
        evolution.log.info(f"curating with {RUNTIME} from {len(reflections)} reflection(s) "
                           f"({', '.join(f'{n} {mode}' for mode, n in sorted(modes.items()))})…")
        curation = Curator(RUNTIME, timeout=TIMEOUT).curate(CURATE.text, reflections, candidate,
                                                             scales=[USEFULNESS, FAILURE_MODES])
        for commit in curation.commits:
            evolution.log.info(f"  {commit.sha[:10]} {commit.message.splitlines()[0]}")
        evolution.log.info(f"curator: {curation.summary}")
    return {"proposal": candidate.reload()}


def _session(handed):
    return handed if not hasattr(handed, "session_id") or handed.session_id == handed.id else handed.session


def _sid(handed):
    return _session(handed).id


def _tail_focus(tail):
    """What a reflector on a partly covered session should judge: what came after the reflections made already."""
    if tail is None:
        return None
    return (f"Earlier reflections already judged this session up to its last {tail.tail} event(s). Judge only what "
            "happened in those last events; earlier ones are context.")


# The round: which step follows which, by the action a step returns.
find_sessions - "too few" >> too_few
find_sessions - "enough" >> partition
partition - "stop" >> stop
partition - "go" >> source >> reflect >> curate


def propose(evolution: Evolution) -> AgentConfig | None:
    return evolution.track(Flow(start=find_sessions), {"evolution": evolution}).get("proposal")
