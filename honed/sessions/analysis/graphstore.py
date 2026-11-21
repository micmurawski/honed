"""Graph artifacts: every graph `honed sessions graph <kind> --export DIR` builds, in one model, in ``DIR``.

A directory is an artifact. It holds ``graphs.sqlite`` (every graph exported into it, whatever its kind),
``manifest.json`` (one entry per kind: what it was built from and how) and each kind's reports in ``DIR/<kind>/``.
Exporting a kind again replaces that kind's graphs; other kinds stay, so one directory can be queried across them.

The model is a property graph whose every element leads back to events:

* **graph**       one graph: ``aeg``, ``weighted``, or one ``workgraph`` / ``decision-trace`` per session or thread;
* **node, edge**  with a kind, a label (an AEG state, a weighted activity, a WorkGraph event kind) and properties;
* **walk**        one case's ordered path through the nodes (a thread, a branch, an AEG case), with its session;
* **occurrence**  where a node (or edge) happened: walk, step, time and event IDs; aggregate nodes have many;
* **annotation**  a finding, critical step or signal at a walk step, with its evidence.

Labels and text are redacted as the reports are; raw events stay in the session index, found by their IDs.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = 1
STORE, MANIFEST = "graphs.sqlite", "manifest.json"
KINDS = ("workgraph", "decision-trace", "weighted", "aeg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS graphs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT, props TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes(graph_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT, label TEXT, text TEXT,
  props TEXT NOT NULL, PRIMARY KEY(graph_id, id));
CREATE TABLE IF NOT EXISTS edges(graph_id TEXT NOT NULL, id TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
  label TEXT, props TEXT NOT NULL, PRIMARY KEY(graph_id, id));
CREATE TABLE IF NOT EXISTS walks(graph_id TEXT NOT NULL, id TEXT NOT NULL, session_id TEXT, thread_id TEXT,
  harness TEXT, title TEXT, cwd TEXT, started_at TEXT, outcome TEXT, steps TEXT NOT NULL, props TEXT NOT NULL,
  PRIMARY KEY(graph_id, id));
CREATE TABLE IF NOT EXISTS occurrences(graph_id TEXT NOT NULL, element TEXT NOT NULL, element_id TEXT NOT NULL,
  walk_id TEXT, step INTEGER, at TEXT, event_ids TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS occurrences_element ON occurrences(graph_id, element, element_id);
CREATE TABLE IF NOT EXISTS annotations(graph_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT,
  text TEXT, walk_id TEXT, step INTEGER, event_ids TEXT NOT NULL, props TEXT NOT NULL);
"""
_TABLES = ("graphs", "nodes", "edges", "walks", "occurrences", "annotations")


@dataclass
class GraphData:
    """One graph in the common model, as rows (dicts) ready to store."""

    id: str
    kind: str
    title: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    walks: list[dict[str, Any]] = field(default_factory=list)
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _walk(walk_id: str, history: Any, steps: list[str], **extra: Any) -> dict[str, Any]:
    metadata = history.metadata if history is not None else None
    return {
        "id": walk_id,
        "session_id": extra.pop("session_id", None) or getattr(history, "session_id", None)
        or getattr(history, "id", None),
        "thread_id": extra.pop("thread_id", None),
        "harness": extra.pop("harness", None) or (metadata.harness if metadata else None),
        "title": extra.pop("title", None) or (metadata.title if metadata else None),
        "cwd": extra.pop("cwd", None) or (metadata.cwd if metadata else None),
        "started_at": extra.pop("started_at", None) or (metadata.started_at if metadata else None),
        "outcome": extra.pop("outcome", None),
        "steps": steps,
        "props": extra,
    }


# --- adapters: each kind into the model --------------------------------------------------------------------------


def from_aeg(view: Any) -> list[GraphData]:
    """The AEG as one graph: states and transitions, a walk per case, findings and critical steps."""
    summary = view.summary()
    graph = GraphData("aeg", "aeg", f"AEG ({view.abstraction})", {
        key: summary[key] for key in ("abstraction", "cases", "sessions", "outcomes", "tokens", "cost")})
    for name, node in view.graph.nodes.items():
        data = node.to_dict()
        kind = "start" if name == "START" else "end" if name.startswith("END") else "state"
        graph.nodes.append({"id": name, "kind": kind, "label": name, "text": " | ".join(data.pop("samples")[:3]),
                            "props": {k: v for k, v in data.items() if k != "name"}})  # fmt: skip
    for (source, target), edge in view.graph.edges.items():
        props = {k: v for k, v in edge.to_dict().items() if k not in {"source", "target"}}
        graph.edges.append({"id": f"{source} -> {target}", "source": source, "target": target,
                            "label": f"{source} -> {target}", "props": props})  # fmt: skip
    position: dict[int, tuple[str, int]] = {}  # id(action) → (case, step)
    for sequence in view.sequences:
        case = sequence.case
        names = sequence.names
        graph.walks.append(_walk(case.case_id, None, names, session_id=case.session_id, thread_id=case.thread_id,
                                 harness=case.harness, title=case.title, cwd=case.cwd, started_at=case.started_at,
                                 outcome=sequence.outcome, root=case.root, tokens=case.tokens,
                                 cost=round(case.cost, 6), model=case.model, branch=case.branch_id,
                                 abandoned_branch=case.abandoned_branch))  # fmt: skip
        states = [None, *sequence.states, None]
        for step, name in enumerate(names):
            state = states[step]
            ids = [i for action in state.actions for i in action.event_ids] if state is not None else []
            at = state.start if state is not None else None
            graph.occurrences.append({"element": "node", "element_id": name, "walk_id": case.case_id, "step": step,
                                      "at": at, "event_ids": ids})  # fmt: skip
            if step + 1 < len(names):
                graph.occurrences.append({"element": "edge", "element_id": f"{name} -> {names[step + 1]}",
                                          "walk_id": case.case_id, "step": step, "at": at, "event_ids": []})
            if state is not None:
                for action in state.actions:
                    position[id(action)] = (case.case_id, step)
    report = view.failures
    if report is not None:
        actions = {s.case.case_id: s.actions for s in view.sequences}

        def where(case_id: str, index: int) -> tuple[str, int | None]:
            listed = actions.get(case_id, [])
            if 0 <= index < len(listed):
                return position.get(id(listed[index]), (case_id, None))
            return case_id, None

        for number, finding in enumerate(report.findings):
            at_walk, at_step = where(finding.case_id, finding.action_index)
            graph.annotations.append({"id": f"finding:{number}", "kind": "finding", "label": finding.mode,
                                      "text": finding.evidence, "walk_id": at_walk, "step": at_step,
                                      "event_ids": list(finding.event_ids),
                                      "props": {"source": finding.source, "confidence": finding.confidence,
                                                "action_index": finding.action_index, "at": finding.at}})
        for critical in report.critical_steps:
            at_walk, at_step = where(critical.case_id, critical.action_index)
            graph.annotations.append({"id": f"critical:{critical.case_id}", "kind": "critical",
                                      "label": critical.category, "text": f"{critical.label} {critical.sample}",
                                      "walk_id": at_walk, "step": at_step, "event_ids": list(critical.event_ids),
                                      "props": {"action_index": critical.action_index, "at": critical.at,
                                                "state_before": critical.state_before}})  # fmt: skip
    return [graph]


def from_weighted(view: Any, histories: Sequence[Any]) -> list[GraphData]:
    """The weighted view as one graph: activities and adjacent pairs, a walk per case and run stream."""
    graph = GraphData("weighted", "weighted", "Weighted patterns", {
        "case_scope": view.case_scope, "cases": view.case_count, "pattern_lengths": list(view.pattern_lengths)})
    for activity in view.activities:
        graph.nodes.append({"id": activity.key, "kind": "activity", "label": activity.key, "text": "",
                            "props": {"occurrences": activity.occurrences, "cases": activity.cases}})  # fmt: skip
    for transition in view.transitions:
        key = f"{transition.source} -> {transition.target}"
        graph.edges.append({"id": key, "source": transition.source, "target": transition.target, "label": key,
                            "props": {"occurrences": transition.occurrences, "cases": transition.cases}})  # fmt: skip
    sessions = {}
    for history in histories:
        session = getattr(history, "session", history)
        sessions[session.id] = session
    for number, walk in enumerate(view.walks):
        walk_id = f"{walk.case_id}/{walk.run_id[:12]}" if walk.run_id else f"{walk.case_id}/{number}"
        labels = [activity for activity, _, _ in walk.steps]
        graph.walks.append(_walk(walk_id, sessions.get(walk.session_id), labels, session_id=walk.session_id,
                                 thread_id=walk.run_id, case=walk.case_id, boundary=walk.boundary))  # fmt: skip
        for step, (activity, event_id, at) in enumerate(walk.steps):
            graph.occurrences.append({"element": "node", "element_id": activity, "walk_id": walk_id, "step": step,
                                      "at": at, "event_ids": [event_id]})  # fmt: skip
            if step + 1 < len(walk.steps):
                graph.occurrences.append({"element": "edge", "element_id": f"{activity} -> {labels[step + 1]}",
                                          "walk_id": walk_id, "step": step, "at": at, "event_ids": [event_id]})
    return [graph]


_SIGNAL = re.compile(r"^signal:([^:]+):")


def from_session_graph(kind: str, history: Any, graph: Any) -> GraphData:
    """A WorkGraph or DecisionTrace of one session or thread: its nodes and edges, a walk per run stream (the
    chain of ``next`` edges), and its signals as annotations."""
    session_id = getattr(history, "session_id", None) or history.id
    thread = None if session_id == history.id else history.id
    data = GraphData(f"{kind}:{history.id}", kind, history.metadata.title or history.metadata.native_id or history.id,
                     {"session_id": session_id, "thread_id": thread})  # fmt: skip
    run_of = {event.id: event.run_id for event in history.selected_events}
    for node in graph.nodes:
        label = node.kind + (f":{node.status}" if node.status else "")
        data.nodes.append({"id": node.id, "kind": node.kind, "label": label, "text": node.summary,
                           "props": {"timestamp": node.timestamp, "status": node.status}})  # fmt: skip
    for edge in graph.edges:
        data.edges.append({"id": f"{edge.source} -{edge.relation}-> {edge.target}", "source": edge.source,
                           "target": edge.target, "label": edge.relation, "props": {}})  # fmt: skip
    # The order within a run stream: WorkGraph links it with "next", DecisionTrace with "then".
    following = {edge.source: edge.target for edge in graph.edges if edge.relation in {"next", "then"}}
    preceded = set(following.values())
    by_id = {node.id: node for node in graph.nodes}
    place: dict[str, tuple[str, int]] = {}
    chains = [node.id for node in graph.nodes if node.kind != "signal" and node.id not in preceded]
    for number, start in enumerate(chains):
        steps, current = [], start
        while current is not None and current not in steps:
            steps.append(current)
            current = following.get(current)
        first = by_id[steps[0]]
        run_id = next((run_of.get(i) for i in first.event_ids if i in run_of), None) or thread
        walk_id = f"{history.id}/{number}" if len(chains) > 1 else history.id
        data.walks.append(_walk(walk_id, history, steps,
                                session_id=session_id, thread_id=run_id))  # fmt: skip
        for step, node_id in enumerate(steps):
            node = by_id[node_id]
            place[node_id] = (walk_id, step)
            data.occurrences.append({"element": "node", "element_id": node_id, "walk_id": walk_id, "step": step,
                                     "at": node.timestamp, "event_ids": list(node.event_ids)})  # fmt: skip
    for node in graph.nodes:
        if node.kind != "signal":
            continue
        signal_walk, signal_step = next(((w, i) for n in graph.nodes if n.kind != "signal" and n.id in place and
                                         set(n.event_ids) & set(node.event_ids) for w, i in [place[n.id]]),
                                        (None, None))  # fmt: skip
        data.occurrences.append({"element": "node", "element_id": node.id, "walk_id": signal_walk, "step": signal_step,
                                 "at": node.timestamp, "event_ids": list(node.event_ids)})  # fmt: skip
        match = _SIGNAL.match(node.id)
        data.annotations.append({"id": node.id, "kind": "signal", "label": match.group(1) if match else "signal",
                                 "text": node.summary, "walk_id": signal_walk, "step": signal_step,
                                 "event_ids": list(node.event_ids), "props": {}})  # fmt: skip
    return data


# --- the artifact directory --------------------------------------------------------------------------------------


@contextmanager
def _locked(folder: Path) -> Iterator[None]:
    with (folder / ".graphs.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _inputs(histories: Sequence[Any]) -> dict[str, Any]:
    sessions: dict[str, int] = {}
    threads: list[str] = []
    for history in histories:
        session = getattr(history, "session", history)
        sessions[session.id] = session.metadata.event_count
        if session is not history:
            threads.append(history.id)
    digest = hashlib.sha256(_json([sorted(sessions.items()), sorted(threads)]).encode()).hexdigest()[:16]
    return {"sessions": sessions, "threads": threads, "digest": digest}


def save(folder: str | Path, kind: str, graphs: Iterable[GraphData], *, histories: Sequence[Any] = (),
         parameters: dict[str, Any] | None = None, index: str | Path | None = None) -> Path:  # fmt: skip
    """Store ``graphs`` (all of one ``kind``) in the artifact at ``folder``, replacing that kind's earlier graphs,
    and record the export in the manifest. Returns the store's path."""
    if kind not in KINDS:
        raise ValueError(f"unknown graph kind {kind!r}")
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / STORE
    graphs = list(graphs)
    with _locked(folder):
        connection = sqlite3.connect(path)
        try:
            connection.executescript(SCHEMA)
            with connection:
                old = [row[0] for row in connection.execute("SELECT id FROM graphs WHERE kind=?", (kind,))]
                for table in _TABLES:
                    column = "id" if table == "graphs" else "graph_id"
                    connection.executemany(f"DELETE FROM {table} WHERE {column}=?", [(g,) for g in old])
                for graph in graphs:
                    connection.execute("INSERT INTO graphs VALUES (?,?,?,?)",
                                       (graph.id, graph.kind, graph.title, _json(graph.props)))  # fmt: skip
                    connection.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", [
                        (graph.id, n["id"], n["kind"], n["label"], n["text"], _json(n["props"])) for n in graph.nodes])
                    connection.executemany("INSERT OR IGNORE INTO edges VALUES (?,?,?,?,?,?)", [
                        (graph.id, e["id"], e["source"], e["target"], e["label"], _json(e["props"]))
                        for e in graph.edges])  # fmt: skip
                    connection.executemany("INSERT INTO walks VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                        (graph.id, w["id"], w["session_id"], w["thread_id"], w["harness"], w["title"], w["cwd"],
                         w["started_at"], w["outcome"], _json(w["steps"]), _json(w["props"])) for w in graph.walks])
                    connection.executemany("INSERT INTO occurrences VALUES (?,?,?,?,?,?,?)", [
                        (graph.id, o["element"], o["element_id"], o["walk_id"], o["step"], o["at"],
                         _json(o["event_ids"])) for o in graph.occurrences])  # fmt: skip
                    connection.executemany("INSERT INTO annotations VALUES (?,?,?,?,?,?,?,?,?)", [
                        (graph.id, a["id"], a["kind"], a["label"], a["text"], a["walk_id"], a["step"],
                         _json(a["event_ids"]), _json(a["props"])) for a in graph.annotations])  # fmt: skip
        finally:
            connection.close()
        manifest = read_manifest(folder)
        manifest["version"] = VERSION
        manifest.setdefault("exports", {})[kind] = {
            "kind": kind,
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "builder_version": VERSION,
            "parameters": parameters or {},
            "index": str(index) if index is not None else None,
            "graphs": len(graphs),
            "input": _inputs(histories),
        }
        (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _index_page(folder, manifest)
    return path


def read_manifest(folder: Path) -> dict[str, Any]:
    path = folder / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": VERSION, "exports": {}}


def _index_page(folder: Path, manifest: dict[str, Any]) -> None:
    rows = "".join(
        f"<tr><td><a href='{kind}/index.html'>{kind}</a></td><td>{entry['graphs']}</td>"
        f"<td>{len(entry['input']['sessions'])}</td><td>{entry['built_at']}</td></tr>"
        for kind, entry in sorted(manifest.get("exports", {}).items())
    )
    (folder / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Graphs</title><style>body{font:15px system-ui;margin:2rem}"
        "td,th{padding:.4rem .8rem;border-bottom:1px solid #d9e0e8;text-align:left}</style><h1>Graphs</h1>"
        f"<table><tr><th>Kind</th><th>Graphs</th><th>Sessions</th><th>Built</th></tr>{rows}</table>"
        f"<p>Query them: <code>honed sessions graph query {folder} '…'</code></p>\n", encoding="utf-8")


def discover(paths: Iterable[str | Path]) -> list[Path]:
    """The artifact directories at or below ``paths`` (each holding ``graphs.sqlite``)."""
    found: list[Path] = []
    for path in paths:
        root = Path(path).expanduser().resolve()
        if (root / STORE).exists():
            found.append(root)
        elif root.is_dir():
            found += sorted(store.parent for store in root.rglob(STORE))
    return list(dict.fromkeys(found))


def stale(folder: Path, index: Path | None) -> list[str]:
    """Exports in ``folder`` whose input sessions changed in the index since (``kind: n session(s) changed``)."""
    if index is None or not Path(index).expanduser().exists():
        return []
    manifest = read_manifest(folder)
    try:
        connection = sqlite3.connect(f"file:{Path(index).expanduser()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    notes = []
    try:
        for kind, entry in manifest.get("exports", {}).items():
            sessions = entry["input"]["sessions"]
            changed = 0
            for start in range(0, len(sessions), 500):
                chunk = list(sessions)[start : start + 500]
                rows = dict(connection.execute(
                    f"SELECT id, event_count FROM sessions WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk).fetchall())  # fmt: skip
                changed += sum(rows.get(session) != sessions[session] for session in chunk)
            if changed:
                notes.append(f"{kind}: {changed} session(s) changed or gone since {entry['built_at']}")
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return notes
