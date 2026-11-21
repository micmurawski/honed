"""Run-thread families and per-thread analysis over indexed sessions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .index import SessionIndex
from .models import Event, Run, SearchFilters, SessionMetadata
from .session import EventSequence, Session

if TYPE_CHECKING:
    from .sessions import Sessions


@dataclass(frozen=True)
class Thread(EventSequence):
    """One independently ordered execution stream (stored as a normalized run)."""

    run: Run
    session: Session
    events: tuple[Event, ...]
    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.run.id

    @property
    def session_id(self) -> str:
        return self.run.session_id

    @property
    def metadata(self) -> SessionMetadata:
        return SessionMetadata.model_validate(self.session.metadata.model_dump())

    @property
    def selected_events(self) -> tuple[Event, ...]:
        return self.events

# Retain the first public name while the thread vocabulary settles.
AgentThread = Thread


@dataclass(frozen=True)
class ThreadMatch:
    """A behavioral match together with the thread that produced it."""

    thread: Thread
    match: Any


class ThreadFamily:
    """A root run and every indexed descendant run linked by ``parent_run_id``.

    The root is included by default. Run streams remain distinct: family analyses
    never invent a single chronological order across parent and child threads.
    """

    def __init__(
        self,
        all_threads: Iterable[Thread],
        *,
        exclude_root: bool = False,
        linkage_warnings: Iterable[str] = (),
    ) -> None:
        self.all_threads = tuple(all_threads)
        self.exclude_root = exclude_root
        self.linkage_warnings = tuple(linkage_warnings)

    @property
    def root(self) -> Thread:
        """Derive the family root from direct parent links."""
        member_ids = {thread.id for thread in self.all_threads}
        roots = [thread for thread in self.all_threads if thread.parent_id not in member_ids]
        if len(roots) != 1:
            raise ValueError(f"Expected one family root from parent links, found {len(roots)}")
        return roots[0]

    @property
    def threads(self) -> tuple[Thread, ...]:
        if self.exclude_root:
            return tuple(thread for thread in self.all_threads if thread.id != self.root.id)
        return self.all_threads

    def select(self, *, exclude_root: bool | None = None) -> tuple[Thread, ...]:
        """Select family threads, including the root unless explicitly excluded."""
        exclude_root = self.exclude_root if exclude_root is None else exclude_root
        return tuple(thread for thread in self.all_threads if not (exclude_root and thread.id == self.root.id))

    def match(self, pattern: Any, *, exclude_root: bool | None = None) -> tuple[ThreadMatch, ...]:
        """Run a behavioral pattern separately on each selected thread's ordered events."""
        threads = self.threads if exclude_root is None else self.select(exclude_root=exclude_root)
        matches = []
        for thread in threads:
            matches.extend(ThreadMatch(thread, match) for match in pattern.find_matches(list(thread.events)))
        return tuple(matches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_thread_id": self.root.id,
            "exclude_root": self.exclude_root,
            "linkage_warnings": list(self.linkage_warnings),
            "threads": [
                {
                    "thread_id": thread.id,
                    "session_id": thread.session_id,
                    "native_session_id": thread.metadata.native_id,
                    "harness": thread.metadata.harness,
                    "parent_thread_id": thread.parent_id,
                    "event_count": len(thread.events),
                }
                for thread in self.threads
            ],
        }


def build_thread_family(
    store: Sessions,
    reference: str,
    *,
    exclude_root: bool = False,
) -> ThreadFamily:
    """Resolve a session/run reference to its root run and linked descendants."""
    index: SessionIndex = store.index
    all_runs = index.connection.execute("SELECT * FROM runs").fetchall()
    runs = {row["id"]: index._run_from_row(row) for row in all_runs}

    try:
        session_id = store.resolve(reference)
    except LookupError:
        session_matches = []
    else:
        session_matches = [run for run in runs.values() if run.session_id == session_id]

    if session_matches:
        roots = [run for run in session_matches if run.parent_run_id is None]
        if len(roots) == 1:
            selected = roots[0]
        elif len(session_matches) == 1:
            selected = session_matches[0]
        else:
            raise ValueError(
                f"Session {reference!r} has {len(roots)} root runs; select a run ID to choose a thread family"
            )
    else:
        matches = [run for run in runs.values() if run.id == reference or run.id.startswith(reference)]
        if len(matches) != 1:
            reason = "matches no indexed thread or session" if not matches else f"is ambiguous ({len(matches)} runs)"
            raise LookupError(f"Thread reference {reference!r} {reason}")
        selected = matches[0]

    seen: set[str] = set()
    while selected.parent_run_id in runs:
        if selected.id in seen:
            raise ValueError("Cycle detected in indexed run-parent links")
        seen.add(selected.id)
        selected = runs[selected.parent_run_id]
    root_run = selected
    missing_parent = root_run.parent_run_id is not None and root_run.parent_run_id not in runs

    children: dict[str, list[Run]] = {}
    for run in runs.values():
        if run.parent_run_id:
            children.setdefault(run.parent_run_id, []).append(run)
    family_runs: list[Run] = []
    pending = [root_run]
    visited: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current.id in visited:
            continue
        visited.add(current.id)
        family_runs.append(current)
        pending.extend(sorted(children.get(current.id, ()), key=lambda run: run.id))

    threads = []
    sessions_by_id = {}
    child_ids = {run.id: tuple(sorted(child.id for child in children.get(run.id, ()))) for run in family_runs}
    for run in family_runs:
        if run.session_id not in sessions_by_id:
            sessions_by_id[run.session_id] = store.get(run.session_id)
        session = sessions_by_id[run.session_id]
        events = tuple(index.iter_events(SearchFilters(run_ids=[run.id])))
        threads.append(Thread(run, session, events, parent_id=run.parent_run_id, child_ids=child_ids[run.id]))
    root = next(thread for thread in threads if thread.id == root_run.id)
    warnings = []
    if root.metadata.harness == "antigravity":
        warnings.append(
            "Antigravity subagent transcripts are separate sessions; the current adapter has no reliable parent link."
        )
    if root.metadata.harness == "pi":
        warnings.append(
            "Pi core parentId links describe branches; extension-created subagent relationships are not standardized."
        )
    if missing_parent:
        warnings.append("The selected thread refers to a parent run that is not present in this index.")
    return ThreadFamily(threads, exclude_root=exclude_root, linkage_warnings=warnings)
