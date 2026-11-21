"""Sessions: the one entry point for collecting, finding, and reading sessions.

`Sessions` registers source directories in a session index, detects the harness
and collector-batch layouts, synchronizes them, and resolves sessions by any
recorded identity. Reads go through read-only snapshots of the index, so a
`Session` keeps its events after later synchronizations or after closing.
Indexing never writes into a source directory.
"""

from __future__ import annotations

import builtins
import json
import os
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, Self, cast
from uuid import uuid4

from honed.harnesses import Harness

from .immutable import Record
from .index import SessionIndex
from .install import InstalledSession
from .models import SearchFilters, SessionMetadata, SourceConfig, SyncReport
from .session import Results, SearchHit, Session, query_events, read_only_index, search_filters

if TYPE_CHECKING:
    from .query import QueryMatch
    from .reflections import Partition
    from .threads import Thread

DEFAULT_INDEX = Path("~/.honed/learning.sqlite")
INDEX_VARIABLE = "HONED_INDEX"
LEGACY_INDEX = Path("~/.thearc/learning.sqlite")  # Where thearc, as honed was called before 0.4, kept it.
LEGACY_INDEX_VARIABLE = "THEARC_INDEX"


def default_index() -> Path:
    """The session index sessions and reflections are kept in: ``$HONED_INDEX``, else ``~/.honed/learning.sqlite``.

    Reflections live only there, so an index thearc made is kept in use: ``$THEARC_INDEX`` when ``$HONED_INDEX`` is
    unset, and ``~/.thearc/learning.sqlite`` while ``~/.honed/learning.sqlite`` does not exist.
    """
    for variable in (INDEX_VARIABLE, LEGACY_INDEX_VARIABLE):
        if os.environ.get(variable):
            return Path(os.environ[variable])
    if not DEFAULT_INDEX.expanduser().exists() and LEGACY_INDEX.expanduser().exists():
        return LEGACY_INDEX
    return DEFAULT_INDEX
_PROBE_FILES = 20
Moment = datetime | str | None


def _first_record(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as stream:
            for line in islice(stream, 5):
                if line.strip():
                    value = json.loads(line)
                    return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
    return None


def detect_harness(root: str | Path) -> Harness:
    """Infer the harness from a few native files; raise if absent or ambiguous."""
    root = Path(root).expanduser().resolve()
    votes: set[Harness] = set()
    for path in islice(sorted(root.rglob("*.jsonl")), _PROBE_FILES):
        if path.name == "transcript.jsonl":
            votes.add("antigravity")
        elif path.name.startswith("rollout-"):
            votes.add("codex")
        elif (record := _first_record(path)) is None:
            continue
        elif isinstance(record.get("info"), dict) and isinstance(record.get("messages"), list):
            votes.add("opencode")
        elif record.get("type") in {"session_meta", "response_item", "event_msg", "turn_context"}:
            votes.add("codex")
        elif "sessionId" in record:
            votes.add("claude")
        elif record.get("type") == "session" or isinstance(record.get("message"), dict):
            votes.add("pi")
    if len(votes) != 1:
        found = ", ".join(sorted(votes)) or "no recognizable transcripts"
        raise ValueError(f"Cannot infer one harness for {root} ({found}); pass harness= explicitly")
    return votes.pop()


def _batch_runs(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Collector batches store runs/<id>/manifest.json beside the sessions/ transcripts."""
    for path in sorted((root / "runs").glob("*/manifest.json")):
        try:
            yield path.parent, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def _compact(value: str) -> str:
    return value.replace("-", "").lower()


def _moment(value: Moment) -> datetime | None:
    """A datetime or ISO string; naive values are UTC."""
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _names(value: str | Iterable[str] | None) -> builtins.list[str]:
    return [value] if isinstance(value, str) else list(value or [])


class Sessions:
    """Collect, list, and resolve sessions from many harnesses in one index.

        with Sessions() as sessions:
            sessions.add("~/.codex/sessions")
            sessions.add("logs/batch")          # a collector batch
            trajectory = sessions.get("14a6b5fe").trajectories[0]

    The first read after a synchronization copies the index into ``artifact_dir``;
    sessions and results keep reading that snapshot.
    """

    def __init__(self, index_path: str | Path | None = None, *, artifact_dir: str | Path | None = None):
        """``index_path`` defaults to ``default_index()``: ``$HONED_INDEX``, else ``~/.honed/learning.sqlite``."""
        self.path = Path(index_path if index_path is not None else default_index()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = (
            Path(artifact_dir).expanduser().resolve() if artifact_dir else self.path.with_suffix(".artifacts")
        )
        self.index = SessionIndex(self.path)
        self._snapshot: Path | None = None
        self._revision: tuple | None = None

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    # --- sources ------------------------------------------------------------------------------------------------

    def add(
        self,
        path: str | Path,
        *,
        harness: Harness | None = None,
        source_id: str | None = None,
        format: Literal["native", "dataclaw"] = "native",
        sync: bool = True,
    ) -> SourceConfig:
        """Register a transcript directory or collector batch, and synchronize it unless ``sync=False``."""
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        if (root / "runs").is_dir() and (root / "sessions").is_dir():
            harnesses = {manifest.get("harness", "codex") for _, manifest in _batch_runs(root)}
            if len(harnesses) > 1:
                raise ValueError(f"Collector batch mixes harnesses {sorted(harnesses)}; add its runs separately")
            if harness is None and harnesses:
                harness = harnesses.pop()
            root = root / "sessions"
        if harness is None:
            harness = "claude" if format == "dataclaw" else detect_harness(root)
        if root in self.path.parents:
            raise ValueError("Keep the index outside the source directory")
        source = SourceConfig(id=source_id or f"{harness}:{format}:{root}", harness=harness, root=root, format=format)
        known = next((s for s in self.sources() if s.root == source.root and s.format == source.format), None)
        if known is not None and source_id is None:
            if known.harness != source.harness:
                raise ValueError(f"{root} is already registered as {known.harness}")
            source = known
        else:
            self.index.register_source(source)
        if sync:
            self.sync(source_ids=[source.id])
        return source

    def add_harness_store(self, harness: Harness, project: str | Path, *, home: str | Path | None = None,
                          sync: bool = True) -> builtins.list[SourceConfig]:
        """Register where ``harness`` keeps the sessions run in ``project`` (see ``honed.sessions.stores``) and
        synchronize them; returns the sources. OpenCode's sessions are exported next to the index first, so call it
        again to pick up new ones. List them with ``list(cwd=project)``."""
        from .stores import export_opencode_sessions, store_folders

        if harness == "opencode":
            exports = self.artifact_dir / "opencode-exports" / Path(project).resolve().as_posix().strip("/")
            export_opencode_sessions(project, exports)
            folders = [exports]
        else:
            folders = store_folders(harness, project, home=home)
        return [self.add(folder, harness=harness, sync=sync) for folder in folders]

    def add_reflection(self, reflection: Any) -> str:
        """Keep a reflection (a ``Reflection`` or its ``to_dict()``) in this index, joined to the session it judged
        (see ``honed.sessions.reflections``); returns its ID. The index is where it lives: deleting the index loses
        it."""
        from . import reflections

        data = reflection.to_dict() if hasattr(reflection, "to_dict") else dict(reflection)
        reflections.store(self.index, data)
        reflections.link(self.index)
        return str(data["id"])

    def add_reflections(self, project: str | Path, *, workspace: str | Path | None = None,
                        sync: bool = True) -> builtins.list[str]:
        """Register the journals of ``project``'s evolution workspace (``workspace``, default ``.honed/workspace``
        when there is one) as a source of reflections, and synchronize; returns the source IDs."""
        root = Path(project).expanduser().resolve()
        folder = Path(workspace) if workspace is not None else root / ".honed/workspace"
        added = [self.index.register_reflections("journal", folder)] if (folder / ".git").exists() else []
        if sync:
            self.sync(source_ids=[])
        return added

    def ingest(self, source: SourceConfig) -> SyncReport:
        """Register an explicit source configuration and synchronize only it."""
        return self.index.ingest(source)

    def sources(self) -> builtins.list[SourceConfig]:
        return self.index.list_sources()

    def sync(self, *, source_ids: builtins.list[str] | None = None) -> SyncReport:
        return self.index.sync(source_ids=source_ids)

    # --- finding sessions ---------------------------------------------------------------------------------------

    def list(
        self,
        *,
        harness: str | Iterable[str] | None = None,
        source: str | Iterable[str] | None = None,
        since: Moment = None,
        until: Moment = None,
        cwd: str | Path | None = None,
        without_call: str | None = None,
    ) -> builtins.list[SessionMetadata]:
        """Session records, newest first. ``cwd`` keeps the sessions run in that folder or below it;
        ``without_call`` drops those with a tool call whose arguments contain that text (e.g. ``honed evolve``)."""
        filters = SearchFilters(
            harnesses=cast("list[Harness]", _names(harness)),
            source_ids=_names(source),
            started_after=_moment(since),
            started_before=_moment(until),
            cwd=str(Path(cwd).expanduser().resolve()) if cwd is not None else None,
            without_call=without_call,
        )
        return sorted(self.index.iter_sessions(filters), key=lambda s: (s.started_at or "", s.id), reverse=True)

    def resolve(self, reference: str | None = None) -> str:
        """Map an indexed ID, native ID, collector run ID, or unique prefix to an indexed ID; None means latest."""
        if reference is None:
            latest = self.list()
            if not latest:
                raise LookupError("No sessions are indexed; add a source first")
            return latest[0].id
        rows = self.index.connection.execute("SELECT id, native_id FROM sessions").fetchall()
        exact = [r["id"] for r in rows if reference in (r["id"], r["native_id"])]
        if len(exact) == 1:
            return exact[0]
        compact, native = _compact(reference), self._run_native_id(reference)
        matches = {
            r["id"]
            for r in rows
            if r["id"].startswith(reference)
            or _compact(r["native_id"]).startswith(compact)
            or (native and r["native_id"] == native)
        }
        if len(matches) != 1:
            problem = "matches no indexed session" if not matches else f"is ambiguous ({len(matches)} sessions)"
            raise LookupError(f"Session reference {reference!r} {problem}")
        return matches.pop()

    def get(self, reference: str | None = None) -> Session:
        """The immutable Session for any reference; None selects the most recent session."""
        return self._get(self._capture(), self.resolve(reference))

    def threads(self) -> tuple[Thread, ...]:
        """Return every indexed execution thread, across session boundaries.

        Each thread carries only direct ``parent_id`` and ``child_ids`` links.
        A linked child may belong to another indexed session.
        """
        from .threads import Thread

        rows = self.index.connection.execute("SELECT * FROM runs ORDER BY started_at,id").fetchall()
        runs = [self.index._run_from_row(row) for row in rows]
        children: dict[str, list[str]] = {}
        sessions_by_id: dict[str, Session] = {}
        for run in runs:
            if run.parent_run_id:
                children.setdefault(run.parent_run_id, []).append(run.id)
        result = []
        for run in runs:
            if run.session_id not in sessions_by_id:
                sessions_by_id[run.session_id] = self.get(run.session_id)
            events = tuple(self.index.iter_events(SearchFilters(run_ids=[run.id])))
            result.append(
                Thread(
                    run=run,
                    session=sessions_by_id[run.session_id],
                    events=events,
                    parent_id=run.parent_run_id,
                    child_ids=tuple(sorted(children.get(run.id, ()))),
                )
            )
        return tuple(result)

    def root_threads(self) -> tuple[Thread, ...]:
        """Return all indexed threads without a recorded parent link."""
        return tuple(thread for thread in self.threads() if thread.parent_id is None)

    def thread(self, reference: str) -> Thread:
        """Resolve one indexed execution thread by exact ID or unique prefix."""
        from .threads import Thread

        rows = self.index.connection.execute("SELECT * FROM runs").fetchall()
        matches = [row for row in rows if row["id"] == reference or row["id"].startswith(reference)]
        if len(matches) != 1:
            reason = "matches no thread" if not matches else f"is ambiguous ({len(matches)} threads)"
            raise LookupError(f"Thread reference {reference!r} {reason}")
        run = self.index._run_from_row(matches[0])
        children = self.index.connection.execute(
            "SELECT id FROM runs WHERE parent_run_id=? ORDER BY id", (run.id,)
        ).fetchall()
        events = tuple(self.index.iter_events(SearchFilters(run_ids=[run.id])))
        return Thread(
            run=run,
            session=self.get(run.session_id),
            events=events,
            parent_id=run.parent_run_id,
            child_ids=tuple(row["id"] for row in children),
        )

    def family(self, reference: str, *, exclude_root: bool = False):
        """Resolve a run/session reference to its linked thread family.

        The root thread is included by default. Child threads are included only
        when the source transcript records a parent-run relationship.
        """
        from .threads import build_thread_family

        return build_thread_family(self, reference, exclude_root=exclude_root)

    def search(
        self, text: str | None = None, *, mode: str = "literal", case_sensitive: bool | None = None, **filters: Any
    ) -> Results:
        """Events matching ``text`` and/or filters (``kind=``, ``status=``, ``tool_name=``, …), in recorded order."""
        selection = search_filters(filters)
        sensitive = (mode == "literal") if case_sensitive is None else case_sensitive

        def generate() -> Iterator[SearchHit]:
            path = self._capture()
            index = read_only_index(path)
            sessions: dict[str, Session] = {}
            try:
                for row in query_events(index, text, selection, mode, sensitive):
                    if row["session_id"] not in sessions:
                        sessions[row["session_id"]] = self._get(path, row["session_id"])
                    event = Record(index.get_event(row["id"]).model_dump(mode="json"))
                    ranges = (
                        SessionIndex._literal_ranges(event.text, text, sensitive) if text and mode == "literal" else []
                    )
                    yield SearchHit(event, sessions[row["session_id"]], tuple(ranges))
            finally:
                index.close()

        return Results(generate)

    def query(self, text: str, *, limit: int | None = None, offset: int = 0) -> builtins.list[QueryMatch]:
        """Events, threads or sessions matching a Sourcegraph-style query (see ``honed.sessions.query``); every
        match unless ``limit`` or the query's ``count:`` stops them. A result's ``history`` is its ``Session``.

            sessions.query("shell.startswith:git status:failure")
            sessions.query("shell:graphify harness:codex at:root select:thread")
        """
        from .query import execute

        path = self._capture()
        index = read_only_index(path)
        try:
            return execute(index, text, limit=limit, offset=offset, loader=lambda session: self._get(path, session))
        finally:
            index.close()

    def partition(self, histories: Sequence[Session | Thread], prompt: Any, schema: Any, *, revision: str,
                  tail_min: int = 1) -> Partition:
        """Split ``histories`` (sessions, or threads of them) by what the reflections kept here cover of them: those
        of ``revision``, made in the session or handed off, that ask the same question: ``prompt`` (the same text,
        or the same ``name@version``) with ``schema``.

        A history is covered when fewer than ``tail_min`` of its events come after the furthest event its
        reflections judged; partly covered when more do; not reflected on when no reflection reaches into it."""
        from .reflections import partition

        return partition(self, histories, prompt, schema, revision=revision, tail_min=tail_min)

    def count(self, text: str) -> int:
        """How many results a query (see ``query``) has, without loading them.

            sessions.count("select:reflection bound:no cwd:~/project")
        """
        from .query import count

        path = self._capture()
        index = read_only_index(path)
        try:
            return count(index, text)
        finally:
            index.close()

    def match(self, pattern: Any, **filters: Any) -> Results:
        """Behavioral pattern matches (see ``scanner``) in the sessions the filters select."""
        selection = search_filters(filters)

        def generate() -> Iterator[Any]:
            path = self._capture()
            index = read_only_index(path)
            try:
                for record in index.iter_candidate_sessions(selection):
                    yield from self._get(path, record.id).match(pattern)
            finally:
                index.close()

        return Results(generate)

    # --- collector batches and installation ---------------------------------------------------------------------

    def run(self, session: Session | str) -> tuple[Path, dict[str, Any]] | None:
        """The collector run directory and manifest that produced this session, if any."""
        session = session if isinstance(session, Session) else self.get(session)
        source = next((s for s in self.sources() if s.id == session.metadata.source_id), None)
        if source is None or source.root.name != "sessions":
            return None
        return next(
            (
                (directory, manifest)
                for directory, manifest in _batch_runs(source.root.parent)
                if manifest.get("thread_id") == session.metadata.native_id
            ),
            None,
        )

    def install_all(
        self,
        harness: str,
        *,
        references: Iterable[str] | None = None,
        source_harness: str | Iterable[str] | None = None,
        source: str | Iterable[str] | None = None,
        since: Moment = None,
        until: Moment = None,
        home: str | Path | None = None,
        overwrite: bool = False,
        strict: bool = True,
        keep_id: bool = False,
    ) -> builtins.list[tuple[Any, InstalledSession | Exception]]:
        """Install the given sessions, or every one matching the filters, into ``harness``.

        Returns ``(session metadata, InstalledSession or the exception)`` per session;
        one failure does not stop the others.
        """
        records = (
            [self.get(reference).metadata for reference in references]
            if references
            else self.list(harness=source_harness, source=source, since=since, until=until)
        )
        results: builtins.list[tuple[Any, InstalledSession | Exception]] = []
        for record in records:
            try:
                results.append(
                    (
                        record,
                        self.get(record.id).install(
                            harness, home=home, overwrite=overwrite, strict=strict, keep_id=keep_id
                        ),
                    )
                )
            except (ValueError, OSError) as exc:
                results.append((record, exc))
        return results

    def _run_native_id(self, run_id: str) -> str | None:
        for source in self.sources():
            manifest = source.root.parent / "runs" / run_id / "manifest.json"
            if source.root.name == "sessions" and manifest.is_file():
                return json.loads(manifest.read_text(encoding="utf-8")).get("thread_id")
        return None

    # --- snapshots ----------------------------------------------------------------------------------------------

    def _capture(self) -> Path:
        """A read-only copy of the index, refreshed when it changed (also by another connection)."""
        revision = (
            self.index.revision,
            self.index.connection.total_changes,
            self.index.connection.execute("PRAGMA data_version").fetchone()[0],
        )
        if self._snapshot is None or revision != self._revision:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.artifact_dir, suffix=".sqlite", delete=False) as stream:
                temporary = Path(stream.name)
            destination = sqlite3.connect(temporary)
            try:
                self.index.connection.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
            finally:
                destination.close()
            path = self.artifact_dir / f"{uuid4().hex}.sqlite"
            temporary.rename(path)
            self._snapshot, self._revision = path, revision
        return self._snapshot

    @staticmethod
    def _get(path: Path, session_id: str) -> Session:
        index = read_only_index(path)
        try:
            metadata = index.get_session(session_id)
        finally:
            index.close()
        return Session(Record(metadata.model_dump(mode="json")), f"{path.stem}:{session_id}", path)
