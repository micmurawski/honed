# The /evolve recipe: the last week's sessions → take the reflections made on them already → reflect on the rest →
# curate. Edit freely.
"""How a round of `thearc evolve` proposes a new version.

`thearc evolve` runs `propose(evolution)` and turns what it returns into the proposal. The round is a flow — find the
sessions, partition them by what earlier reflections cover, source those reflections, reflect on the rest, curate —
so `evolution.track` can show it live at .thearc/cache/progress.html, with the round's log (`evolution.log`) in a tab.
The prompts are the Markdown files in prompts/ next to this script.

Reflections are made two ways: during a session, by its own agent (the /reflect skill, `mode: in-session`), and here,
by a reflector handed the session (`mode: handoff`). Both are kept in the session index, beside the sessions they
judge (`thearc sessions query 'select:reflection'`), so a round reflects only on what no reflection covers yet.
"""

from collections import Counter
from datetime import timedelta
from pathlib import Path

from thearc.curation import Curator
from thearc.evolve import FAILURE_MODES, SCHEMA, USEFULNESS, Evolution
from thearc.flow import BatchNode, Flow, Node
from thearc.reflection import Prompt, ReflectionError, Reflector
from thearc.resources import MetaAgent
from thearc.sessions.analysis.aeg import focus, session_failures

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


class FindSessions(Node):
    """The harness's sessions run in this project in the last WINDOW; enough of them to learn from?"""

    def prep(self, shared):
        return shared["evolution"]

    def exec(self, evolution):
        return evolution.sessions(harness=HARNESS, since=WINDOW)  # Never one that ran /evolve.

    def post(self, shared, prep_res, sessions):
        evolution = shared["evolution"]
        evolution.log.info(f"{len(sessions)} {HARNESS} session(s) in the last {WINDOW.days} days")
        for session in sessions:
            evolution.log.info(f"  {session.id}  {session.metadata.started_at}  {session.metadata.title or ''}")
        if len(sessions) < MIN_SESSIONS:
            evolution.nothing(f"{len(sessions)} session(s) in the last {WINDOW.days} days; {MIN_SESSIONS} needed")
            return "too few"
        shared["sessions"] = sessions
        return "enough"


class TooFew(Node):
    """Nothing to propose yet."""


class Stop(Node):
    """Estimated only, or over the budget: nothing reflected on."""


class Partition(Node):
    """The sessions by what reflections made already cover: covered, partly covered, not reflected on."""

    def prep(self, shared):
        return shared["evolution"], shared["sessions"]

    def exec(self, prep_res):
        evolution, sessions = prep_res
        if not SOURCE:
            return evolution.partition([], REFLECT, SCHEMA), [history(s) for s in sessions]
        partition = evolution.partition([history(s) for s in sessions], REFLECT, SCHEMA, match=SOURCE_MATCH,
                                        modes=SOURCE_MODES, tail_min=TAIL_MIN_EVENTS)
        tails = list(partition.partial) if TAILS == "reflect" else []
        return partition, [part.history for part in partition.unreflected] + [part.history for part in tails]

    def post(self, shared, prep_res, result):
        evolution = shared["evolution"]
        partition, to_reflect = result
        evolution.log.info(f"{partition} (by {REFLECT.label}, matched by {SOURCE_MATCH})" if SOURCE
                           else "taking no reflections made already: reflecting on every session")
        for label, parts in (("covered", partition.covered), ("partly covered", partition.partial)):
            for part in parts:
                made = ", ".join(sorted(f"{n} {mode}" for mode, n in Counter(r.mode for r in part.reflections).items()))
                tail = f"; {part.tail} event(s) after them" if part.tail else ""
                evolution.log.info(f"  {part.session.id}: {label} by {len(part.reflections)} reflection(s) "
                                   f"({made}){tail}")
        tails = {part.session.id: part for part in partition.partial} if TAILS == "reflect" else {}
        if FOCUS or SELECT == "findings":  # Rules, no model calls: only where a reflector will look.
            found = session_failures(to_reflect)
            shared["focus"] = {sid: focus(*found[sid]) for sid in found if found[sid][0]}  # Only with findings.
            for sid, (findings, _) in found.items():
                if findings:
                    evolution.log.info(f"  {sid}: {', '.join(sorted({f.mode for f in findings}))}")
            if SELECT == "findings":
                to_reflect = [h for h in to_reflect if found.get(_sid(h), ([], None))[0]]
                evolution.log.info(f"{len(to_reflect)} session(s) with failure findings to reflect on")
        # Size, earlier costs, and what is reused: of what each reflector will be handed.
        estimate = evolution.estimate(to_reflect, RUNTIME, prompt=REFLECT, sourced=len(partition.covered))
        if evolution.estimate_only:
            evolution.nothing(f"estimate only: {estimate}; {partition}")
            return "stop"
        if BUDGET is not None and estimate.cost is not None and estimate.cost > BUDGET:
            evolution.nothing(f"estimated at ${estimate.cost:.2f}, over the budget of ${BUDGET:.2f}")
            return "stop"
        shared["partition"], shared["to_reflect"], shared["tails"] = partition, to_reflect, tails
        shared["candidate"] = evolution.candidate()  # A worktree of the installed release.
        return "go"


class Source(Node):
    """The reflections made already on covered and partly covered sessions, into the candidate: no model call."""

    def prep(self, shared):
        return shared["evolution"], shared["candidate"], shared["partition"]

    def exec(self, prep_res):
        evolution, candidate, partition = prep_res
        return evolution.source(candidate, partition.sourced)

    def post(self, shared, prep_res, recorded):
        evolution, _, partition = prep_res
        modes = Counter(reflection.mode for reflection in partition.sourced)
        evolution.log.info(f"sourced {recorded} reflection(s) made already "
                           f"({', '.join(f'{n} {mode}' for mode, n in sorted(modes.items())) or 'none'})")


class Reflect(BatchNode):
    """Each session no reflection covers yet (or the tail of one partly covered), reviewed against the installed
    configuration: which lines helped, which hurt."""

    def prep(self, shared):
        self.evolution, self.candidate = shared["evolution"], shared["candidate"]
        self.focus = shared.get("focus", {}) if FOCUS else {}
        self.tails = shared.get("tails", {})
        return shared["to_reflect"]

    def exec(self, handed):
        sid = _sid(handed)
        if self.evolution.reflected(handed, self.candidate):  # A stopped round kept its reflections.
            self.evolution.log.info(f"{sid}: reflected on in an earlier, stopped run")
            return
        if (reused := self.evolution.recall(handed, self.candidate, REFLECT)) is not None:
            self.evolution.log.info(f"{sid}: reused {reused.id} from an earlier round (no model call)")
            return
        tail = self.tails.get(sid)
        part = f" (root thread {handed.id[:12]}, without its subagents)" if REFLECT_ON == "root" and handed.id != sid \
            else ""
        hints = [hint for hint in (self.focus.get(sid), _tail_focus(tail)) if hint]
        self.evolution.log.info(f"{sid}{part}: reflecting with {RUNTIME}"
                                + (f" on its last {tail.tail} event(s)" if tail else "") + "…")
        try:
            reflection = Reflector(RUNTIME, timeout=TIMEOUT).handoff(
                REFLECT, handed, self.candidate, SCHEMA, provenance=self.evolution.provenance(_session(handed)),
                focus="\n\n".join(hints) or None, judged_from=tail.tail_from if tail else None)
        except ReflectionError as error:  # One failed reflection costs its session, not the round.
            self.evolution.log.warning(f"{sid}: no reflection ({error}); going on without it")
            return
        self.evolution.keep(reflection)  # For later rounds and queries, whatever this round proposes.
        self.candidate.add_reflection(reflection)
        rated = Counter(item.rating for item in reflection)
        self.evolution.log.info(f"{sid}: {len(reflection)} rating(s) "
                                f"({', '.join(f'{value} {label}' for label, value in rated.items()) or 'none'})")


class Curate(Node):
    """The configuration changed where the reflections show it hurt, and kept where they show it helped."""

    def prep(self, shared):
        return shared["evolution"], shared["candidate"]

    def exec(self, prep_res):
        evolution, candidate = prep_res
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
        return candidate.reload()

    def post(self, shared, prep_res, proposal):
        if proposal is not None:
            shared["proposal"] = proposal


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


def propose(evolution: Evolution) -> MetaAgent | None:
    find, too_few, stop, partition, source, reflect, curate = (
        FindSessions(metadata={"harness": HARNESS, "window": f"{WINDOW.days} days", "min": MIN_SESSIONS}),
        TooFew(),
        Stop(),
        Partition(metadata={"prompt": REFLECT.label, "match": SOURCE_MATCH, "budget": BUDGET}),
        Source(metadata={"modes": ", ".join(SOURCE_MODES)}),
        Reflect(metadata={"runtime": RUNTIME, "prompt": "prompts/reflect.md"}),
        Curate(metadata={"runtime": RUNTIME, "prompt": "prompts/curate.md"}),
    )
    find - "too few" >> too_few
    find - "enough" >> partition
    partition - "stop" >> stop
    partition - "go" >> source >> reflect >> curate
    shared = evolution.track(Flow(start=find), {"evolution": evolution})
    return shared.get("proposal")
