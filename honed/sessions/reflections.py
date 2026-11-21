"""Reflections in the session index, beside the sessions they judge.

A reflection is stored in the index itself (``store``; ``Sessions.add_reflection``): the index is where reflections
live, not a cache of them — deleting it loses them (``honed sessions query --json 'select:reflection'`` exports
them). The ``reflected`` lines of evolution workspaces' journals are a source too, read on every sync, so the
reflections a team shares through a workspace are found as well; a reflection stored here wins over a journal's copy.

    reflections          one row per reflection, whatever made it (handoff, in-session, attached)
    reflection_ratings   one row per rating item: what it cites and its rating on which scale
    reflection_coverage  per session and question (``asked``) and revision: how far its reflections reach
    event_tags           ``reflection:<id>`` on the events of an in-session reflection's own turns
    prompts              prompt texts by hash, so a hash can always be explained

An in-session reflection knows its marker (``honed-reflect:…``), not its session: syncing finds the events holding
the marker, binds the reflection to their session, tags the reflection's own turns, and records the segment it
judged — after the previous in-session reflection of that session (or its start) up to its own turns.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .index import SessionIndex
    from .models import SyncReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS reflection_sources(id TEXT PRIMARY KEY, kind TEXT NOT NULL, root TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompts(hash TEXT PRIMARY KEY, name TEXT, version TEXT, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reflections(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
 session_id TEXT, evidence_session TEXT, native_session TEXT, thread TEXT,
 mode TEXT NOT NULL, trigger TEXT,
 prompt_name TEXT, prompt_version TEXT, prompt_hash TEXT, schema_hash TEXT, asked TEXT,
 revision TEXT NOT NULL, marker TEXT,
 from_event TEXT, to_event TEXT, events INTEGER, events_digest TEXT,
 reflector_harness TEXT, model TEXT, cost REAL, at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reflections_session ON reflections(session_id, prompt_name, prompt_hash);
CREATE INDEX IF NOT EXISTS reflections_asked ON reflections(asked, revision);
CREATE INDEX IF NOT EXISTS reflections_marker ON reflections(marker);
CREATE TABLE IF NOT EXISTS reflection_ratings(
 reflection_id TEXT NOT NULL, item TEXT NOT NULL, ref TEXT, target TEXT,
 scale TEXT NOT NULL, rating TEXT, PRIMARY KEY(reflection_id, item, scale));
CREATE INDEX IF NOT EXISTS reflection_ratings_target ON reflection_ratings(target, rating);
CREATE TABLE IF NOT EXISTS reflection_coverage(
 session_id TEXT NOT NULL, asked TEXT NOT NULL, revision TEXT NOT NULL, prompt_hash TEXT,
 covered_to TEXT, last_event TEXT, tail INTEGER NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(session_id, asked, revision));
CREATE TABLE IF NOT EXISTS event_tags(event_id TEXT NOT NULL, tag TEXT NOT NULL, ref TEXT,
 PRIMARY KEY(event_id, tag));
"""

TAG = "reflection:"  # An in-session reflection's own turns: ``reflection:<id>``.
STORED = "reflections:stored"  # The source of reflections stored in the index itself.
MARKER = "honed-reflect:"
ROW_COLUMNS = (
    "id", "source_id", "path", "session_id", "evidence_session", "native_session", "thread", "mode", "trigger",
    "prompt_name", "prompt_version", "prompt_hash", "schema_hash", "asked", "revision", "marker", "from_event",
    "to_event", "events", "events_digest", "reflector_harness", "model", "cost", "at", "data",
)


def digest(ids: Iterable[str]) -> str:
    """The digest of events with these IDs, in order: ``honed.reflection.reflector.ids_digest``."""
    return hashlib.sha256(json.dumps(list(ids)).encode()).hexdigest()[:16]


def coarse_target(ref: str) -> str:
    """The resource a citation's file belongs to: ``skill:NAME``, ``context:FILE``, or the file's path."""
    path = ref.partition("#")[0]
    parts = path.split("/")
    if parts[0] == "skills" and len(parts) > 1:
        return f"skill:{parts[1]}"
    if parts[0] == "context" and len(parts) > 1:
        return f"context:{'/'.join(parts[1:])}"
    return path


# --- sources ------------------------------------------------------------------------------------------------------


def register(index: SessionIndex, kind: str, root: str | Path) -> str:
    """Register a reflection source: ``journal`` (a workspace repository, whose branches' ``journal.jsonl`` hold
    ``reflected`` lines). Returns its ID."""
    if kind != "journal":
        raise ValueError(f"unknown reflection source kind {kind!r}")
    root = Path(root).expanduser().resolve()
    source_id = f"reflections:{kind}:{root}"
    with index.connection:
        index.connection.execute("INSERT OR REPLACE INTO reflection_sources VALUES (?,?,?)",
                                 (source_id, kind, str(root)))
    return source_id


def store(index: SessionIndex, data: dict[str, Any]) -> None:
    """Keep a reflection (``Reflection.to_dict()``) in the index; one with the same ID is replaced."""
    with index.connection:
        _forget(index.connection, "id=?", (data["id"],))
        _insert(index.connection, STORED, "", dict(data))
        index.connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")


def register_prompt(index: SessionIndex, text: str, name: str | None, version: str | None, hashed: str) -> None:
    with index.connection:
        index.connection.execute("INSERT OR REPLACE INTO prompts VALUES (?,?,?,?)", (hashed, name, version, text))


def sources(index: SessionIndex) -> list[tuple[str, str, Path]]:
    return [(row[0], row[1], Path(row[2]))
            for row in index.connection.execute("SELECT id, kind, root FROM reflection_sources ORDER BY id")]


def sync(index: SessionIndex, report: SyncReport) -> int:
    """Read every registered reflection source; returns how many reflections were (re)read."""
    changed = 0
    for source_id, kind, root in sources(index):
        try:
            changed += _sync_source(index, source_id, _journals(root), report)
        except OSError as error:
            report.warnings.append(f"Reflections unavailable from {root}: {error}")
    for name, version, count in index.connection.execute(
            "SELECT prompt_name, prompt_version, COUNT(DISTINCT prompt_hash) FROM reflections "
            "WHERE prompt_name IS NOT NULL AND prompt_version IS NOT NULL GROUP BY prompt_name, prompt_version "
            "HAVING COUNT(DISTINCT prompt_hash) > 1"):
        report.warnings.append(f"Prompt {name}@{version} was asked with {count} different texts: its version was not "
                               "bumped when it changed; match reflections by prompt hash")
    return changed


def _journals(root: Path) -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
    """(``git:<blob>``, blob, [reflection]) for each distinct ``journal.jsonl`` on the workspace's branches, with each
    reflection's ratings from the journal's Rated entries (they carry the targets the ratings resolved to)."""
    from honed.resources.journal import Rated, load_journal_records
    from honed.resources.workspace import JOURNAL_FILE

    if not (root / ".git").exists():
        return
    heads = _git(root, "for-each-ref", "--format=%(objectname)", "refs/heads").split()
    seen: set[str] = set()
    for head in heads:
        try:
            blob = _git(root, "rev-parse", f"{head}:{JOURNAL_FILE}").strip()
        except subprocess.CalledProcessError:
            continue
        if blob in seen:
            continue
        seen.add(blob)
        entries, reflected = load_journal_records(_git(root, "cat-file", "blob", blob))
        targets: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(lambda: defaultdict(set))
        for entry in entries:
            if isinstance(entry, Rated) and entry.extra.get("reflection") and entry.item_id:
                item = entry.item_id.partition("#")[2]
                targets[entry.extra["reflection"]][(item, entry.scale)].add(str(entry.target))
        found = []
        for kept in reflected:
            data = kept.model_dump(mode="json")
            data["_targets"] = {f"{item}\x00{scale}": sorted(names)
                                for (item, scale), names in targets.get(kept.id, {}).items()}
            found.append(data)
        yield f"git:{blob}", blob, found


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _sync_source(index: SessionIndex, source_id: str, read: Iterator[tuple[str, str, list[dict[str, Any]]]],
                 report: SyncReport) -> int:
    connection = index.connection
    known = {row[0]: row[1] for row in connection.execute(
        "SELECT path, digest FROM artifacts WHERE source_id=?", (source_id,))}
    seen: set[str] = set()
    changed = 0
    with connection:
        for path, hashed, reflections in read:
            seen.add(path)
            if known.get(path) == hashed:
                continue
            _forget(connection, "source_id=? AND path=?", (source_id, path))
            for data in reflections:
                if connection.execute("SELECT 1 FROM reflections WHERE id=?", (data["id"],)).fetchone():
                    continue  # Stored here, or in another journal, already.
                _insert(connection, source_id, path, data)
                changed += 1
            connection.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?,?,1)",
                               (source_id, path, 0, 0, 0, hashed, "{}"))
        for path in set(known) - seen:  # A deleted file, or a journal no branch holds any more.
            _forget(connection, "source_id=? AND path=?", (source_id, path))
            connection.execute("DELETE FROM artifacts WHERE source_id=? AND path=?", (source_id, path))
            changed += 1
        if changed:
            connection.execute("UPDATE settings SET value=value+1 WHERE key='revision'")
    report.artifacts += len(seen)
    return changed


def _forget(connection: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> None:
    ids = [row[0] for row in connection.execute(f"SELECT id FROM reflections WHERE {where}", params)]
    for identifier in ids:
        connection.execute("DELETE FROM reflection_ratings WHERE reflection_id=?", (identifier,))
        connection.execute("DELETE FROM reflections WHERE id=?", (identifier,))


def _insert(connection: sqlite3.Connection, source_id: str, path: str, data: dict[str, Any]) -> None:
    from honed.reflection.reflector import reflection_mode

    evidence = data.get("evidence") or {}
    prompt = data.get("prompt") or {}
    reflector = data.get("reflector") or {}
    usage = reflector.get("usage") or {}
    targets = data.pop("_targets", {})
    row = {
        "id": data["id"], "source_id": source_id, "path": path, "session_id": None,
        "evidence_session": evidence.get("session"), "native_session": evidence.get("native_session"),
        "thread": evidence.get("thread"), "mode": reflection_mode(data), "trigger": data.get("trigger"),
        "prompt_name": prompt.get("name"), "prompt_version": prompt.get("version"), "prompt_hash": prompt.get("hash"),
        "schema_hash": data.get("schema"), "asked": evidence.get("asked"), "revision": data.get("revision") or "",
        "marker": evidence.get("marker"), "from_event": evidence.get("from_event"),
        "to_event": evidence.get("to_event"), "events": evidence.get("events"), "events_digest": evidence.get("digest"),
        "reflector_harness": reflector.get("harness"), "model": reflector.get("model"), "cost": usage.get("cost"),
        "at": data.get("at") or "", "data": json.dumps(data, ensure_ascii=False),
    }
    connection.execute(f"INSERT OR REPLACE INTO reflections ({','.join(ROW_COLUMNS)}) "
                       f"VALUES ({','.join('?' for _ in ROW_COLUMNS)})", [row[name] for name in ROW_COLUMNS])
    if prompt.get("hash") and prompt.get("text"):
        connection.execute("INSERT OR IGNORE INTO prompts VALUES (?,?,?,?)",
                           (prompt["hash"], prompt.get("name"), prompt.get("version"), prompt["text"]))
    for item in data.get("items") or []:
        names = targets.get(f"{item.get('path', '')}\x00{item.get('scale', '')}") or [coarse_target(item["ref"])]
        for name in names:
            connection.execute("INSERT OR REPLACE INTO reflection_ratings VALUES (?,?,?,?,?,?)",
                               (data["id"], f"{item.get('path', '')}\x00{name}", item.get("ref"), name,
                                item.get("scale") or "", None if item.get("rating") is None else str(item["rating"])))


# --- joining reflections to sessions ------------------------------------------------------------------------------


def link(index: SessionIndex) -> None:
    """After events and sessions are current: bind reflections to their sessions (by the IDs their evidence names, or
    by their markers), tag in-session reflections' own turns, fill in segments (in the stored record too), and
    rebuild coverage."""
    connection = index.connection
    with connection:
        connection.execute(
            "UPDATE reflections SET session_id=COALESCE("
            "(SELECT s.id FROM sessions s WHERE s.id=reflections.evidence_session), "
            "(SELECT s.id FROM sessions s WHERE s.native_id=reflections.native_session LIMIT 1))")
        resolved = _resolve_markers(connection)
        _record(connection, resolved)
        _fill_legacy_segments(connection)
        refresh_coverage(connection)


def ordered_events(connection: sqlite3.Connection, session_id: str) -> list[sqlite3.Row]:
    """A session's events in the index's stream order, with their call IDs and kinds."""
    return connection.execute(
        "SELECT id, run_id, kind, call_id, text FROM events WHERE session_id=? "
        "ORDER BY source_id, path, byte_offset, subrecord, id", (session_id,)).fetchall()


def tagged(connection: sqlite3.Connection, session_id: str | None = None) -> set[str]:
    """IDs of events that are an in-session reflection's own turns (of one session, or all)."""
    try:
        if session_id is None:
            rows = connection.execute("SELECT event_id FROM event_tags WHERE tag LIKE 'reflection:%'")
        else:
            rows = connection.execute(
                "SELECT t.event_id FROM event_tags t JOIN events e ON e.id=t.event_id "
                "WHERE e.session_id=? AND t.tag LIKE 'reflection:%'", (session_id,))
        return {row[0] for row in rows}
    except sqlite3.OperationalError:  # An index (snapshot) from before reflections were indexed.
        return set()


def _resolve_markers(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Find every in-session reflection's marker; returns what each resolved to (for its file)."""
    connection.execute("DELETE FROM event_tags WHERE tag LIKE 'reflection:%'")
    marked = connection.execute("SELECT id, marker FROM reflections WHERE marker IS NOT NULL").fetchall()
    turns: dict[str, list[tuple[int, int, str]]] = defaultdict(list)  # session -> [(start, end, reflection)]
    positions: dict[str, list[sqlite3.Row]] = {}
    for reflection_id, marker in marked:
        phrase = '"' + marker.replace('"', "") + '"'
        hits = connection.execute(
            "SELECT e.session_id, e.id FROM events e WHERE e.id IN "
            "(SELECT event_id FROM event_text WHERE event_text MATCH ?) AND instr(e.text, ?) > 0 "
            "AND e.kind IN ('tool_call', 'tool_result')", (phrase, marker)).fetchall()
        if not hits:
            continue  # Not in any indexed transcript yet: tried again on the next sync.
        sessions = defaultdict(set)
        for session_id, event_id in hits:
            sessions[session_id].add(event_id)
        session_id = max(sessions, key=lambda s: len(sessions[s]))
        if session_id not in positions:
            positions[session_id] = ordered_events(connection, session_id)
        events = positions[session_id]
        where = [i for i, event in enumerate(events) if event["id"] in sessions[session_id]]
        start, end = _with_calls(events, where[0], where[-1])
        turns[session_id].append((start, end, reflection_id))
    resolved: dict[str, dict[str, Any]] = {}
    for session_id, found in turns.items():
        events = positions[session_id]
        found.sort()
        for start, end, reflection_id in found:
            connection.executemany("INSERT OR IGNORE INTO event_tags VALUES (?,?,?)",
                                   [(events[i]["id"], TAG + reflection_id, None) for i in range(start, end + 1)])
        mine = {i for start, end, _ in found for i in range(start, end + 1)}
        previous_end = -1
        for start, end, reflection_id in found:
            judged = [events[i]["id"] for i in range(previous_end + 1, start) if i not in mine]
            previous_end = end
            segment = {"session": session_id, "from_event": judged[0] if judged else None,
                       "to_event": judged[-1] if judged else None, "events": len(judged), "digest": digest(judged)}
            native = connection.execute("SELECT native_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            segment["native_session"] = native[0] if native else None
            connection.execute(
                "UPDATE reflections SET session_id=?, evidence_session=?, native_session=?, from_event=?, to_event=?, "
                "events=?, events_digest=? WHERE id=?",
                (session_id, session_id, segment["native_session"], segment["from_event"], segment["to_event"],
                 segment["events"], segment["digest"], reflection_id))
            resolved[reflection_id] = segment
    return resolved


def _with_calls(events: list[sqlite3.Row], start: int, end: int) -> tuple[int, int]:
    """Widen ``start..end`` to whole tool calls: the call of a first result, the result of a last call."""
    first, last = events[start], events[end]
    if first["kind"] == "tool_result" and first["call_id"]:
        start = next((i for i in range(start, -1, -1) if events[i]["kind"] == "tool_call"
                      and events[i]["call_id"] == first["call_id"]), start)
    if last["kind"] == "tool_call" and last["call_id"]:
        end = next((i for i in range(end, len(events)) if events[i]["kind"] == "tool_result"
                    and events[i]["call_id"] == last["call_id"]), end)
    return start, end


def _fill_legacy_segments(connection: sqlite3.Connection) -> None:
    """Handoff reflections recorded before segments: they judged the first ``events`` events of their session."""
    rows = connection.execute("SELECT id, session_id, events FROM reflections WHERE session_id IS NOT NULL "
                              "AND marker IS NULL AND to_event IS NULL AND events > 0").fetchall()
    for reflection_id, session_id, count in rows:
        skipped = tagged(connection, session_id)
        ids = [event["id"] for event in ordered_events(connection, session_id) if event["id"] not in skipped]
        if ids:
            judged = ids[:count]
            connection.execute("UPDATE reflections SET from_event=?, to_event=? WHERE id=?",
                               (judged[0], judged[-1], reflection_id))


def refresh_coverage(connection: sqlite3.Connection) -> None:
    """Per session, question (``asked``) and revision: the furthest event its reflections judged, against the last
    event of the session (or of the thread its reflections judged); ``tail`` events are left after it."""
    connection.execute("DELETE FROM reflection_coverage")
    groups = connection.execute(
        "SELECT session_id, asked, revision, MAX(prompt_hash), GROUP_CONCAT(COALESCE(to_event, ''), char(31)), "
        "GROUP_CONCAT(COALESCE(thread, ''), char(31)) FROM reflections "
        "WHERE session_id IS NOT NULL AND asked IS NOT NULL GROUP BY session_id, asked, revision").fetchall()
    cache: dict[str, tuple[list[sqlite3.Row], set[str]]] = {}
    for session_id, asked, revision, prompt, ends, threads in groups:
        if session_id not in cache:
            cache[session_id] = (ordered_events(connection, session_id), tagged(connection, session_id))
        events, skipped = cache[session_id]
        thread_ids = set(threads.split("\x1f"))
        domain = [event["id"] for event in events if event["id"] not in skipped
                  and ("" in thread_ids or len(thread_ids) != 1 or event["run_id"] in thread_ids)]
        place = {identifier: i for i, identifier in enumerate(domain)}
        covered = max((place[end] for end in ends.split("\x1f") if end in place), default=None)
        tail = len(domain) - (covered + 1 if covered is not None else 0)
        connection.execute("INSERT INTO reflection_coverage VALUES (?,?,?,?,?,?,?,?)",
                           (session_id, asked, revision, prompt,
                            domain[covered] if covered is not None else None, domain[-1] if domain else None,
                            tail, "full" if tail == 0 else "partial"))


def _record(connection: sqlite3.Connection, resolved: dict[str, dict[str, Any]]) -> None:
    """What markers resolved to, in the stored reflections' own records (their ``evidence``)."""
    for reflection_id, segment in resolved.items():
        row = connection.execute("SELECT data FROM reflections WHERE id=? AND source_id=?",
                                 (reflection_id, STORED)).fetchone()
        if row is None:
            continue
        data = json.loads(row[0])
        evidence = data.get("evidence") or {}
        if any(evidence.get(key) != value for key, value in segment.items()):
            data["evidence"] = {**evidence, **segment}
            connection.execute("UPDATE reflections SET data=? WHERE id=?",
                               (json.dumps(data, ensure_ascii=False), reflection_id))
