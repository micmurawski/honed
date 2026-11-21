"""Querying graph artifacts (``graphstore``) with the semantics of session queries.

A query is a list of terms joined by ``AND`` (or adjacency), ``OR`` and ``NOT``/``-``, with parentheses;
``select:`` sets what it returns, ``count:`` how many, ``case:yes`` makes text matching case-sensitive.

    honed sessions graph query artifacts/claude-30d 'path:"EDIT -> TEST:fail ->> DONE" outcome:failed select:walk'

Every field belongs to a level of the model: graph, node, edge, walk, annotation or event. A term holds for a
result when some element of its level *related to that result* satisfies it: a node is related to the walks it
occurs in, a walk to its nodes and annotations, a session to its walks, an event to the occurrences that cite it,
and so on. So ``label:EDIT label:TEST:fail select:walk`` finds walks with both states, and a negated term needs
none. Prefix a field with a graph kind to look only in graphs of that kind: ``aeg.label:DEBUG``,
``workgraph.signal:StubbornToolLoop`` (joined through shared events with ``select:event``, ``thread`` or
``session``).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ..query import QuerySyntaxError, _moment, _read_value
from .graphstore import KINDS, STORE

Unit = Literal["graph", "node", "edge", "walk", "occurrence", "annotation", "session", "thread", "event"]
UNITS: tuple[str, ...] = ("graph", "node", "edge", "walk", "occurrence", "annotation", "session", "thread", "event")
_UNIT_ALIASES = {u + "s": u for u in UNITS} | {"finding": "annotation", "findings": "annotation", "case": "walk",
                                               "cases": "walk", "state": "node", "states": "node"}  # fmt: skip
FAILED = {"failed", "abandoned", "user_aborted", "error", "branch_abandoned"}

# name → (level, help). The level decides which related elements a term is tested against.
FIELDS: dict[str, tuple[str, str]] = {
    "graph": ("graph", "the graph's kind: aeg, weighted, workgraph, decision-trace"),
    "artifact": ("graph", "the artifact directory's name"),
    "reaches": ("graph", '"A ~> B": some node matching A leads to one matching B along edges'),
    "label": ("node", "a node's label: an AEG state, a weighted activity, a WorkGraph event kind(:status)"),
    "kind": ("node", "a node's kind: state, start, end, activity, message, tool_call, signal, …"),
    "text": ("node", "a node's text: AEG samples, WorkGraph summaries"),
    "content": ("node", "a bare word: a node's label or text"),
    "edge": ("edge", '"A -> B": an edge from a node matching A to one matching B'),
    "relation": ("edge", "an edge's label (WorkGraph: next, returned, parent, evidence_for)"),
    "path": ("walk", '"A -> B ->> C": states in order (-> next, ->> later; * any, A|B, !A, PREFIX*, ^, $)'),
    "loop": ("walk", '"A B": the cycle A → B → A … repeated (iterations>=N; default 2)'),
    "outcome": ("walk", "completed, abandoned, user_aborted, error, branch_abandoned; failed: any but completed"),
    "case": ("walk", "a walk's ID (prefix)"),
    "session": ("walk", "a session ID (prefix)"),
    "thread": ("walk", "a thread (run) ID (prefix)"),
    "harness": ("walk", "claude, codex, pi, opencode, antigravity"),
    "cwd": ("walk", "the session's working directory (~ expands)"),
    "title": ("walk", "the session's title"),
    "after": ("walk", "started at or after: ISO date/time, or 30d, 12h, 2w ago"),
    "before": ("walk", "started before: ISO date/time, or 30d, 12h, 2w ago"),
    "finding": ("annotation", "a failure mode found at a step (aeg): premature_done, no_verification, …"),
    "critical": ("annotation", "a critical step's category (aeg): check_failed, guardrail, …"),
    "signal": ("annotation", "a WorkGraph signal: StubbornToolLoop, FrustrationSpike"),
    "source": ("annotation", "who found a finding: rules or judge"),
    "confidence": ("annotation", "a finding's confidence: high, medium, low"),
    "event": ("event", "an event ID (prefix)"),
}
MODIFIERS = {"select", "count", "case"}
_OPERATORS = {"contains", "is", "startswith", "matches"}
_LEVELS = ("graph", "node", "edge", "walk", "annotation")
_EXACT = {"harness", "outcome", "source", "confidence", "graph"}
_PREFIX = {"case", "session", "thread", "event"}


@dataclass(frozen=True)
class Term:
    """``[kind.]field[.operator]:value``, or a numeric comparison ``[kind.][level.]property OP number``."""

    field: str
    level: str
    value: str
    operator: str = "contains"
    graph_kind: str | None = None
    compare: str | None = None  # >, >=, <, <=, =, != for numeric terms
    position: int = 0


@dataclass(frozen=True)
class And:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Not:
    item: Node


Node = Term | And | Or | Not


@dataclass(frozen=True)
class GraphQuery:
    text: str
    node: Node | None
    select: str = "node"
    count: int | None = None
    case_sensitive: bool = False
    iterations: tuple[str, float] | None = None  # A comparison applied to every loop: term.


# --- parsing -----------------------------------------------------------------------------------------------------

_NAME = r"(?:[A-Za-z_][\w-]*\.)*[A-Za-z_]\w*"
_FILTER = re.compile(rf"(-?)({_NAME}):")
_NUMERIC = re.compile(rf"(-?)({_NAME})(>=|<=|!=|>|<|=)(-?\d+(?:\.\d+)?)(?=\s|\)|$)")


def _split_name(name: str) -> tuple[str | None, list[str]]:
    parts = name.lower().split(".")
    if len(parts) > 1 and parts[0] in KINDS:
        return parts[0], parts[1:]
    if name.lower().startswith("decision-trace."):
        return "decision-trace", name.lower().split(".")[1:]
    return None, parts


@dataclass
class _Token:
    kind: str  # pattern, filter, numeric, lparen, rparen, and, or, not
    position: int
    value: str = ""
    name: str = ""
    negated: bool = False
    compare: str = ""


def _tokens(text: str) -> list[_Token]:
    tokens, position = [], 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        if char in "()":
            tokens.append(_Token("lparen" if char == "(" else "rparen", position))
            position += 1
            continue
        if match := _NUMERIC.match(text, position):
            tokens.append(_Token("numeric", position, match.group(4), match.group(2), bool(match.group(1)),
                                 match.group(3)))  # fmt: skip
            position = match.end()
            continue
        match = _FILTER.match(text, position)
        if match:
            _, parts = _split_name(match.group(2))
            if parts and (parts[0] in FIELDS or parts[0] in MODIFIERS):
                value, _, end = _read_value(text, match.end(), True, True)
                tokens.append(_Token("filter", position, value, match.group(2), bool(match.group(1))))
                position = end
                continue
        value, quoted, end = _read_value(text, position, True, True)
        if not quoted and value.lower() in {"and", "or", "not"}:
            tokens.append(_Token(value.lower(), position))
        else:
            negated = not quoted and value.startswith("-") and len(value) > 1
            tokens.append(_Token("pattern", position, value[1:] if negated else value, negated=negated))
        position = end
    return tokens


def parse(text: str) -> GraphQuery:
    """Parse a graph query; raises ``QuerySyntaxError`` pointing at the problem."""
    settings: dict[str, Any] = {}
    tokens: list[_Token] = []
    iterations = None
    for token in _tokens(text):
        if token.kind == "filter" and token.name.lower() in MODIFIERS:
            name, value = token.name.lower(), token.value.lower()
            if name in settings:
                raise QuerySyntaxError(f"{name}: given more than once", text, token.position)
            if name == "select":
                unit = _UNIT_ALIASES.get(value, value)
                if unit not in UNITS:
                    raise QuerySyntaxError(f"Invalid select:{token.value}; expected {', '.join(UNITS)}", text,
                                           token.position)  # fmt: skip
                settings[name] = unit
            elif name == "count":
                if not value.isdigit() or int(value) < 1:
                    raise QuerySyntaxError("count: needs a positive integer", text, token.position)
                settings[name] = int(value)
            else:
                settings[name] = value in {"yes", "true", "on"}
            continue
        if token.kind == "numeric" and token.name.lower() == "iterations":
            iterations = (token.compare, float(token.value))
            continue
        tokens.append(token)
    select = settings.get("select", "node")
    node = _Parser(text, tokens, select).parse()
    if iterations is not None and not any(t.field == "loop" for t in _terms(node)):
        raise QuerySyntaxError("iterations needs a loop: term", text, 0)
    return GraphQuery(text, node, select, settings.get("count"), settings.get("case", False), iterations)


def _terms(node: Node | None) -> Iterator[Term]:
    if isinstance(node, Term):
        yield node
    elif isinstance(node, And | Or):
        for item in node.items:
            yield from _terms(item)
    elif isinstance(node, Not):
        yield from _terms(node.item)


class _Parser:
    def __init__(self, text: str, tokens: list[_Token], select: str) -> None:
        self.text, self.tokens, self.select, self.position = text, tokens, select, 0

    def fail(self, message: str, token: _Token | None = None) -> QuerySyntaxError:
        return QuerySyntaxError(message, self.text, token.position if token else len(self.text))

    def peek(self) -> _Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> _Token:
        self.position += 1
        return self.tokens[self.position - 1]

    def parse(self) -> Node | None:
        if not self.tokens:
            return None
        node = self.or_()
        if (token := self.peek()) is not None:
            raise self.fail("Unbalanced ')'" if token.kind == "rparen" else "Unexpected input", token)
        return node

    def or_(self) -> Node:
        items = [self.and_()]
        while (token := self.peek()) and token.kind == "or":
            self.take()
            items.append(self.and_())
        return items[0] if len(items) == 1 else Or(tuple(items))

    def and_(self) -> Node:
        items = [self.unary()]
        while (token := self.peek()) and token.kind not in {"or", "rparen"}:
            if token.kind == "and":
                self.take()
            items.append(self.unary())
        return items[0] if len(items) == 1 else And(tuple(items))

    def unary(self) -> Node:
        token = self.peek()
        if token is None:
            raise self.fail("Expected a term")
        if token.kind == "not":
            self.take()
            return Not(self.unary())
        if token.kind == "lparen":
            self.take()
            node = self.or_()
            if (closing := self.peek()) is None or closing.kind != "rparen":
                raise self.fail("Missing ')'", token)
            self.take()
            return node
        if token.kind in {"rparen", "and", "or"}:
            raise self.fail(f"Expected a term, not {token.kind!r}", token)
        self.take()
        term = self.term(token)
        return Not(term) if token.negated else term

    def term(self, token: _Token) -> Term:
        if token.kind == "pattern":
            return Term("content", "node", token.value, "contains", None, None, token.position)
        graph_kind, parts = _split_name(token.name)
        if token.kind == "numeric":
            level = parts[0] if len(parts) > 1 and parts[0] in _LEVELS else None
            prop = parts[-1]
            if level is None:
                level = self.select if self.select in _LEVELS else "node"
            return Term(prop, level, token.value, "number", graph_kind, token.compare, token.position)
        name, operator = parts[0], (parts[1] if len(parts) > 1 else None)
        if operator is not None and operator not in _OPERATORS:
            raise self.fail(f"Unknown operator {operator!r}; use contains, is, startswith or matches", token)
        if not token.value:
            raise self.fail(f"{token.name}: needs a value", token)
        value = token.value
        if operator is None and len(value) >= 2 and value[0] == value[-1] == "/":
            operator, value = "matches", value[1:-1]
        elif operator == "matches" and len(value) >= 2 and value[0] == value[-1] == "/":
            value = value[1:-1]
        operator = operator or ("is" if name in _EXACT else "startswith" if name in _PREFIX else "contains")
        if operator == "matches":
            try:
                re.compile(value)
            except re.error as exc:
                raise self.fail(f"Invalid regular expression {value!r}: {exc}", token) from exc
        level = FIELDS[name][0]
        if name in {"path", "loop", "edge", "reaches"}:
            try:
                _compile_pattern(name, value)
            except ValueError as exc:
                raise self.fail(str(exc), token) from exc
        return Term(name, level, value, operator, graph_kind, None, token.position)


# --- label patterns: path, loop, edge, reaches -------------------------------------------------------------------

_OPEN, _CLOSE = "\x02", "\x03"
_ANY = f"{_OPEN}[^{_CLOSE}]*{_CLOSE}"


def _element(text: str) -> str:
    """One step of a label pattern as a regex over encoded labels: ``*``, ``A``, ``A|B``, ``PREFIX*``, ``!A``."""
    text = text.strip()
    if not text:
        raise ValueError("empty step in a label pattern")
    if text == "*":
        return _ANY
    negated = text.startswith("!")
    alternatives = []
    for alternative in (text[1:] if negated else text).split("|"):
        alternative = alternative.strip().lower()
        if not alternative:
            raise ValueError(f"empty alternative in {text!r}")
        if alternative.endswith("*"):
            alternatives.append(re.escape(alternative[:-1]) + f"[^{_CLOSE}]*")
        else:
            alternatives.append(re.escape(alternative))
    one = f"{_OPEN}(?:{'|'.join(alternatives)}){_CLOSE}"
    return f"(?!{one}){_ANY}" if negated else one


def _compile_path(text: str) -> re.Pattern[str]:
    text = text.strip()
    start, end = text.startswith("^"), text.endswith("$")
    text = text.removeprefix("^").removesuffix("$")
    pieces = re.split(r"\s*(->>|->)\s*", text.strip())
    lead = trail = ""
    if len(pieces) > 1 and pieces[0] == "" and pieces[1] == "->>":  # "^ ->> A": A anywhere after the start.
        pieces, lead = pieces[2:], f"(?:{_ANY})*?"
    if len(pieces) > 1 and pieces[-1] == "" and pieces[-2] == "->>":  # "A ->> $": anything until the end.
        pieces, trail = pieces[:-2], f"(?:{_ANY})*"
    regex = ("^" if start else "") + lead
    for index, piece in enumerate(pieces):
        if index % 2:
            regex += f"(?:{_ANY})*?" if piece == "->>" else ""
        else:
            regex += f"({_element(piece)})"
    return re.compile(regex + trail + ("$" if end else ""))


def _loop_elements(text: str) -> list[str]:
    return [piece for piece in re.split(r"\s*->\s*|\s+", text.strip()) if piece]


def _compile_pattern(name: str, value: str) -> Any:
    if name == "path":
        return _compile_path(value)
    if name == "loop":
        elements = _loop_elements(value)
        if not elements:
            raise ValueError("loop: needs one or more states")
        return [re.compile("".join(_element(e) for e in elements[i:] + elements[:i])) for i in range(len(elements))]
    separator = "~>" if name == "reaches" else "->"
    if separator not in value:
        raise ValueError(f'{name}: needs "A {separator} B"')
    source, target = value.split(separator, 1)
    return re.compile(_element(source) + "$"), re.compile(_element(target) + "$")


def _encode(label: str | None) -> str:
    return f"{_OPEN}{(label or '').lower()}{_CLOSE}"


# --- the loaded artifacts ----------------------------------------------------------------------------------------


@dataclass(eq=False)
class Graph:
    key: str
    artifact: str
    folder: Path
    id: str
    kind: str
    title: str
    props: dict[str, Any]
    nodes: dict[str, GNode] = field(default_factory=dict)
    edges: dict[str, GEdge] = field(default_factory=dict)
    walks: dict[str, Walk] = field(default_factory=dict)
    annotations: list[Annotation] = field(default_factory=list)
    _adjacency: dict[str, list[str]] | None = None

    def adjacency(self) -> dict[str, list[str]]:
        if self._adjacency is None:
            self._adjacency = {}
            for edge in self.edges.values():
                self._adjacency.setdefault(edge.source, []).append(edge.target)
        return self._adjacency


@dataclass(eq=False)
class GNode:
    graph: Graph
    id: str
    kind: str
    label: str
    text: str
    props: dict[str, Any]
    occurrences: list[Occurrence] = field(default_factory=list)


@dataclass(eq=False)
class GEdge:
    graph: Graph
    id: str
    source: str
    target: str
    label: str
    props: dict[str, Any]
    occurrences: list[Occurrence] = field(default_factory=list)


@dataclass(eq=False)
class Walk:
    graph: Graph
    id: str
    session_id: str | None
    thread_id: str | None
    harness: str | None
    title: str | None
    cwd: str | None
    started_at: str | None
    outcome: str | None
    steps: list[str]
    props: dict[str, Any]
    occurrences: list[Occurrence] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    _encoded: tuple[str, list[int]] | None = None

    def labels(self) -> list[str]:
        return [self.graph.nodes[step].label if step in self.graph.nodes else step for step in self.steps]

    def encoded(self) -> tuple[str, list[int]]:
        """The labels as one string for pattern matching, and where each step starts in it."""
        if self._encoded is None:
            starts, text = [], []
            offset = 0
            for label in self.labels():
                piece = _encode(label)
                starts.append(offset)
                text.append(piece)
                offset += len(piece)
            self._encoded = ("".join(text), starts)
        return self._encoded


@dataclass(eq=False)
class Occurrence:
    graph: Graph
    element: str
    element_id: str
    walk: Walk | None
    step: int | None
    at: str | None
    event_ids: list[str]


@dataclass(eq=False)
class Annotation:
    graph: Graph
    id: str
    kind: str
    label: str
    text: str
    walk: Walk | None
    step: int | None
    event_ids: list[str]
    props: dict[str, Any]


@dataclass(eq=False)
class SessionRef:
    id: str
    walks: list[Walk] = field(default_factory=list)


@dataclass(eq=False)
class ThreadRef:
    id: str
    session_id: str | None
    walks: list[Walk] = field(default_factory=list)


@dataclass(eq=False)
class EventRef:
    id: str
    occurrences: list[Occurrence] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)


class World:
    """Every graph of some artifact directories, loaded and cross-linked."""

    def __init__(self, folders: Iterable[Path]) -> None:
        self.graphs: list[Graph] = []
        self.sessions: dict[str, SessionRef] = {}
        self.threads: dict[str, ThreadRef] = {}
        self._events: dict[str, EventRef] | None = None
        for folder in folders:
            self._load(folder)

    def _load(self, folder: Path) -> None:
        connection = sqlite3.connect(f"file:{folder / STORE}?mode=ro", uri=True)
        try:
            graphs = {}
            for graph_id, kind, title, props in connection.execute("SELECT id, kind, title, props FROM graphs"):
                graph = Graph(f"{folder.name}/{graph_id}", folder.name, folder, graph_id, kind, title or "",
                              json.loads(props))  # fmt: skip
                graphs[graph_id] = graph
                self.graphs.append(graph)
            for graph_id, node_id, kind, label, text, props in connection.execute("SELECT * FROM nodes"):
                graph = graphs[graph_id]
                graph.nodes[node_id] = GNode(graph, node_id, kind or "", label or "", text or "", json.loads(props))
            for graph_id, edge_id, source, target, label, props in connection.execute("SELECT * FROM edges"):
                graph = graphs[graph_id]
                graph.edges[edge_id] = GEdge(graph, edge_id, source, target, label or "", json.loads(props))
            for row in connection.execute("SELECT * FROM walks"):
                graph = graphs[row[0]]
                walk = Walk(graph, row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                            json.loads(row[9]), json.loads(row[10]))  # fmt: skip
                graph.walks[walk.id] = walk
                if walk.session_id:
                    self.sessions.setdefault(walk.session_id, SessionRef(walk.session_id)).walks.append(walk)
                if walk.thread_id:
                    thread = self.threads.setdefault(walk.thread_id, ThreadRef(walk.thread_id, walk.session_id))
                    thread.walks.append(walk)
            for graph_id, element, element_id, walk_id, step, at, events in connection.execute(
                    "SELECT * FROM occurrences"):  # fmt: skip
                graph = graphs[graph_id]
                owner = graph.walks.get(walk_id) if walk_id else None
                occurrence = Occurrence(graph, element, element_id, owner, step, at, json.loads(events))
                holder: GNode | GEdge | None = (graph.nodes.get(element_id) if element == "node"
                                                else graph.edges.get(element_id))  # fmt: skip
                if holder is not None:
                    holder.occurrences.append(occurrence)
                if owner is not None:
                    owner.occurrences.append(occurrence)
            for row in connection.execute("SELECT * FROM annotations"):
                graph = graphs[row[0]]
                annotated = graph.walks.get(row[5]) if row[5] else None
                annotation = Annotation(graph, row[1], row[2], row[3] or "", row[4] or "", annotated, row[6],
                                        json.loads(row[7]), json.loads(row[8]))  # fmt: skip
                graph.annotations.append(annotation)
                if annotated is not None:
                    annotated.annotations.append(annotation)
        finally:
            connection.close()

    @property
    def events(self) -> dict[str, EventRef]:
        if self._events is None:
            self._events = {}
            for graph in self.graphs:
                elements: list[GNode | GEdge] = [*graph.nodes.values(), *graph.edges.values()]
                for element in elements:
                    for occurrence in element.occurrences:
                        for event_id in occurrence.event_ids:
                            self._events.setdefault(event_id, EventRef(event_id)).occurrences.append(occurrence)
                for annotation in graph.annotations:
                    for event_id in annotation.event_ids:
                        self._events.setdefault(event_id, EventRef(event_id)).annotations.append(annotation)
        return self._events

    def units(self, unit: str) -> Iterable[Any]:
        if unit == "graph":
            return list(self.graphs)
        if unit == "node":
            return [n for g in self.graphs for n in g.nodes.values()]
        if unit == "edge":
            return [e for g in self.graphs for e in g.edges.values()]
        if unit == "walk":
            return [w for g in self.graphs for w in g.walks.values()]
        if unit == "occurrence":
            return [o for g in self.graphs for o in self.occurrences(g)]
        if unit == "annotation":
            return [a for g in self.graphs for a in g.annotations]
        if unit == "session":
            return list(self.sessions.values())
        if unit == "thread":
            return list(self.threads.values())
        return list(self.events.values())

    # --- relations ---------------------------------------------------------------------------------------------

    def occurrences(self, x: Any) -> list[Occurrence]:
        if isinstance(x, Graph):
            return [o for n in x.nodes.values() for o in n.occurrences] + [o for e in x.edges.values()
                                                                          for o in e.occurrences]  # fmt: skip
        if isinstance(x, GNode | GEdge | Walk | EventRef):
            return x.occurrences
        if isinstance(x, Occurrence):
            return [x]
        if isinstance(x, Annotation):
            return [o for o in (x.walk.occurrences if x.walk else ()) if o.step == x.step and o.element == "node"]
        return [o for walk in x.walks for o in walk.occurrences]

    def walks(self, x: Any) -> list[Walk]:
        if isinstance(x, Graph):
            return list(x.walks.values())
        if isinstance(x, Walk):
            return [x]
        if isinstance(x, Occurrence | Annotation):
            return [x.walk] if x.walk else []
        if isinstance(x, SessionRef | ThreadRef):
            return x.walks
        if isinstance(x, GEdge) and not x.occurrences:
            return list(x.graph.walks.values())  # An instance graph's edge belongs to its walk(s).
        found = {id(o.walk): o.walk for o in self.occurrences(x) if o.walk is not None}
        if isinstance(x, EventRef):
            found |= {id(a.walk): a.walk for a in x.annotations if a.walk is not None}
        return list(found.values())

    def related(self, x: Any, level: str) -> Iterable[Any]:
        if level == "walk":
            return self.walks(x)
        if level == "graph":
            if isinstance(x, Graph):
                return [x]
            if isinstance(x, GNode | GEdge | Walk | Occurrence | Annotation):
                return [x.graph]
            return list({id(w.graph): w.graph for w in self.walks(x)}.values())
        if level == "node":
            if isinstance(x, Graph):
                return list(x.nodes.values())
            if isinstance(x, GNode):
                return [x]
            if isinstance(x, GEdge):
                return [n for n in (x.graph.nodes.get(x.source), x.graph.nodes.get(x.target)) if n is not None]
            if isinstance(x, Walk):
                return [x.graph.nodes[s] for s in dict.fromkeys(x.steps) if s in x.graph.nodes]
            nodes = {}
            for o in self.occurrences(x):
                if o.element == "node" and (node := o.graph.nodes.get(o.element_id)) is not None:
                    nodes[id(node)] = node
            return list(nodes.values())
        if level == "edge":
            if isinstance(x, Graph):
                return list(x.edges.values())
            if isinstance(x, GEdge):
                return [x]
            if isinstance(x, GNode):
                return [e for e in x.graph.edges.values() if x.id in (e.source, e.target)]
            if isinstance(x, Walk):
                via = {o.element_id for o in x.occurrences if o.element == "edge"}
                if via:
                    return [x.graph.edges[e] for e in via if e in x.graph.edges]
                steps = set(x.steps)
                return [e for e in x.graph.edges.values() if e.source in steps and e.target in steps]
            edges = {}
            for o in self.occurrences(x):
                if o.element == "edge" and (edge := o.graph.edges.get(o.element_id)) is not None:
                    edges[id(edge)] = edge
            return list(edges.values())
        if level == "annotation":
            if isinstance(x, Graph):
                return x.annotations
            if isinstance(x, Annotation):
                return [x]
            if isinstance(x, Walk):
                return x.annotations
            if isinstance(x, EventRef):
                return x.annotations
            if isinstance(x, SessionRef | ThreadRef):
                return [a for w in x.walks for a in w.annotations]
            # A node, edge or occurrence: the annotations at the walk steps where it occurs.
            occurrences = [o for o in self.occurrences(x) if o.walk is not None]
            places = {(id(o.walk), o.step) for o in occurrences}
            walks = {id(o.walk): o.walk for o in occurrences if o.walk is not None}
            return [a for walk in walks.values() for a in walk.annotations if (id(walk), a.step) in places]
        if level == "event":
            if isinstance(x, EventRef):
                return [x]
            ids = {i for o in self.occurrences(x) for i in o.event_ids}
            if isinstance(x, Annotation):
                ids |= set(x.event_ids)
            return [self.events[i] for i in ids if i in self.events]
        if level == "session":
            if isinstance(x, SessionRef):
                return [x]
            return [self.sessions[w.session_id] for w in self.walks(x) if w.session_id in self.sessions]
        if level == "thread":
            if isinstance(x, ThreadRef):
                return [x]
            return [self.threads[w.thread_id] for w in self.walks(x) if w.thread_id in self.threads]
        return self.occurrences(x)


# --- evaluation --------------------------------------------------------------------------------------------------


def _text(term: Term, value: str | None, case_sensitive: bool) -> bool:
    if value is None:
        return False
    needle = term.value
    if term.operator == "matches":
        return re.search(needle, value, 0 if case_sensitive else re.IGNORECASE) is not None
    if not case_sensitive:
        value, needle = value.lower(), needle.lower()
    if term.operator == "is":
        return value == needle
    if term.operator == "startswith":
        return value.startswith(needle)
    return needle in value


def _number(value: Any, compare: str, other: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return {">": value > other, ">=": value >= other, "<": value < other, "<=": value <= other,
            "=": value == other, "!=": value != other}[compare]  # fmt: skip


def _property(x: Any, name: str) -> Any:
    if isinstance(x, Walk) and name in {"steps", "length"}:
        return len(x.steps)
    if isinstance(x, GNode | GEdge) and name == "occurrences" and "occurrences" not in x.props:
        return len(x.occurrences)
    if isinstance(x, Annotation) and name == "step":
        return x.step
    return getattr(x, "props", {}).get(name)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class Evaluator:
    def __init__(self, world: World, query: GraphQuery, now: datetime | None = None) -> None:
        self.world, self.query = world, query
        self.now = now or datetime.now(UTC)
        self.patterns: dict[tuple[str, str], Any] = {}
        self.reach: dict[tuple[int, str], bool] = {}

    def pattern(self, term: Term) -> Any:
        key = (term.field, term.value)
        if key not in self.patterns:
            self.patterns[key] = _compile_pattern(term.field, term.value)
        return self.patterns[key]

    def matches(self, x: Any, node: Node | None = None) -> bool:
        node = self.query.node if node is None else node
        if node is None:
            return True
        if isinstance(node, And):
            return all(self.matches(x, item) for item in node.items)
        if isinstance(node, Or):
            return any(self.matches(x, item) for item in node.items)
        if isinstance(node, Not):
            return not self.matches(x, node.item)
        return any(self.test(node, element) for element in self.elements(x, node))

    def elements(self, x: Any, term: Term) -> Iterable[Any]:
        found = self.world.related(x, term.level)
        if term.graph_kind is not None:
            found = [e for e in found if getattr(getattr(e, "graph", e), "kind", None) == term.graph_kind
                     or (isinstance(e, Graph) and e.kind == term.graph_kind)]  # fmt: skip
        return found

    def test(self, term: Term, x: Any) -> bool:
        case = self.query.case_sensitive
        if term.compare is not None:
            return _number(_property(x, term.field), term.compare, float(term.value))
        name = term.field
        if name == "graph":
            return _text(term, x.kind, case)
        if name == "artifact":
            return _text(term, x.artifact, case)
        if name == "reaches":
            return self.reaches(x, term)
        if name in {"label", "kind", "text"}:
            return _text(term, getattr(x, name), case)
        if name == "content":
            return _text(term, x.label, case) or _text(term, x.text, case)
        if name == "edge":
            source, target = self.pattern(term)
            labels = (x.graph.nodes[x.source].label if x.source in x.graph.nodes else x.source,
                      x.graph.nodes[x.target].label if x.target in x.graph.nodes else x.target)  # fmt: skip
            return bool(source.match(_encode(labels[0])) and target.match(_encode(labels[1])))
        if name == "relation":
            return _text(term, x.label, case)
        if name == "path":
            return self.pattern(term).search(x.encoded()[0]) is not None
        if name == "loop":
            return self.loop(term, x)
        if name == "outcome":
            if term.value.lower() == "failed":
                return (x.outcome or "") in FAILED
            return _text(term, x.outcome, case)
        if name in {"case", "session", "thread"}:
            return _text(term, {"case": x.id, "session": x.session_id, "thread": x.thread_id}[name], case)
        if name in {"harness", "title"}:
            return _text(term, getattr(x, name), case)
        if name == "cwd":
            expanded = Term(term.field, term.level, str(Path(term.value).expanduser()) if term.value.startswith("~")
                            else term.value, term.operator)  # fmt: skip
            return _text(expanded, x.cwd, case)
        if name in {"after", "before"}:
            started = _time(x.started_at)
            if started is None:
                return False
            moment = datetime.fromisoformat(_moment(term.value, self.now))
            return started >= moment if name == "after" else started < moment
        if name in {"finding", "critical", "signal"}:
            return x.kind == name and (_text(term, x.label, case) or (name == "signal" and _text(term, x.text, case)))
        if name in {"source", "confidence"}:
            return _text(term, x.props.get(name), case)
        if name == "event":
            return _text(term, x.id, case)
        return False

    def loop(self, term: Term, walk: Walk) -> bool:
        encoded = walk.encoded()[0]
        compare, target = self.query.iterations or (">=", 2)
        best = 0
        for rotation in self.pattern(term):
            for run in re.finditer(f"(?:{rotation.pattern})+", encoded):
                best = max(best, len(rotation.findall(run.group(0))))
        return best > 0 and _number(best, compare, target)

    def reaches(self, graph: Graph, term: Term) -> bool:
        key = (id(graph), term.value)
        if key not in self.reach:
            source, target = self.pattern(term)
            adjacency = graph.adjacency()
            starts = [n.id for n in graph.nodes.values() if source.match(_encode(n.label))]
            seen, queue, found = set(starts), deque(starts), False
            while queue and not found:
                current = queue.popleft()
                for following in adjacency.get(current, ()):
                    node = graph.nodes.get(following)
                    if node is not None and target.match(_encode(node.label)):
                        found = True
                        break
                    if following not in seen:
                        seen.add(following)
                        queue.append(following)
            self.reach[key] = found
        return self.reach[key]

    def path_steps(self, walk: Walk) -> list[int]:
        """The steps the query's (positive) path terms matched in ``walk``, for showing where."""
        text, starts = walk.encoded()
        steps: set[int] = set()
        for term in _positive(self.query.node):
            if term.field != "path":
                continue
            if (found := self.pattern(term).search(text)) is not None:
                for group in range(1, (found.lastindex or 0) + 1):
                    if found.start(group) >= 0:
                        steps.add(_step_at(starts, found.start(group)))
        return sorted(steps)


def _positive(node: Node | None) -> Iterator[Term]:
    if isinstance(node, Term):
        yield node
    elif isinstance(node, And | Or):
        for item in node.items:
            yield from _positive(item)


def _step_at(starts: list[int], offset: int) -> int:
    low, high = 0, len(starts) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if starts[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low


# --- results -----------------------------------------------------------------------------------------------------


@dataclass
class GraphMatch:
    type: str
    element: Any
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}


def _data(world: World, evaluator: Evaluator, unit: str, x: Any) -> dict[str, Any]:
    if unit == "graph":
        return {"artifact": x.artifact, "id": x.id, "kind": x.kind, "title": x.title, "nodes": len(x.nodes),
                "edges": len(x.edges), "walks": len(x.walks), "annotations": len(x.annotations), "props": x.props}
    if unit == "node":
        return {"artifact": x.graph.artifact, "graph": x.graph.id, "id": x.id, "kind": x.kind, "label": x.label,
                "text": x.text, "occurrences": len(x.occurrences), "props": x.props}
    if unit == "edge":
        return {"artifact": x.graph.artifact, "graph": x.graph.id, "id": x.id, "source": x.source,
                "target": x.target, "label": x.label, "occurrences": len(x.occurrences), "props": x.props}
    if unit == "walk":
        return {"artifact": x.graph.artifact, "graph": x.graph.id, "id": x.id, "session_id": x.session_id,
                "thread_id": x.thread_id, "harness": x.harness, "title": x.title, "started_at": x.started_at,
                "outcome": x.outcome, "steps": x.labels(), "match": evaluator.path_steps(x), "props": x.props}
    if unit == "occurrence":
        element = x.graph.nodes.get(x.element_id) if x.element == "node" else x.graph.edges.get(x.element_id)
        return {"artifact": x.graph.artifact, "graph": x.graph.id, "element": x.element, "id": x.element_id,
                "label": getattr(element, "label", x.element_id), "walk": x.walk.id if x.walk else None,
                "session_id": x.walk.session_id if x.walk else None, "run_id": x.walk.thread_id if x.walk else None,
                "step": x.step, "at": x.at, "event_ids": x.event_ids}
    if unit == "annotation":
        return {"artifact": x.graph.artifact, "graph": x.graph.id, "id": x.id, "kind": x.kind, "label": x.label,
                "text": x.text, "walk": x.walk.id if x.walk else None,
                "session_id": x.walk.session_id if x.walk else None, "step": x.step, "event_ids": x.event_ids,
                "props": x.props}
    if unit == "session":
        walk = x.walks[0]
        return {"id": x.id, "harness": walk.harness, "title": walk.title, "cwd": walk.cwd,
                "started_at": walk.started_at, "walks": len(x.walks)}
    if unit == "thread":
        return {"id": x.id, "session_id": x.session_id, "walks": len(x.walks)}
    walk = next((o.walk for o in x.occurrences if o.walk is not None), None)
    return {"id": x.id, "session_id": walk.session_id if walk else None, "run_id": walk.thread_id if walk else None}



def execute(folders: Iterable[Path], query: str | GraphQuery, *, limit: int | None = None,
            default_limit: int | None = 50, now: datetime | None = None) -> list[GraphMatch]:  # fmt: skip
    """Run a graph query over the artifacts in ``folders``."""
    parsed = parse(query) if isinstance(query, str) else query
    world = World(folders)
    evaluator = Evaluator(world, parsed, now)
    count = limit or parsed.count or default_limit
    found: list[GraphMatch] = []
    for x in world.units(parsed.select):
        if evaluator.matches(x):
            found.append(GraphMatch(parsed.select, x, _data(world, evaluator, parsed.select, x)))
            if count is not None and len(found) >= count:
                break
    return found


def check_labels(folders: Iterable[Path], query: GraphQuery) -> list[str]:
    """Hints for ``label.is:`` values no graph has (a typo returns nothing, silently, otherwise)."""
    exact = [t for t in _terms(query.node) if t.field == "label" and t.operator == "is"]
    if not exact:
        return []
    world = World(folders)
    labels = {n.label.lower(): n.label for g in world.graphs for n in g.nodes.values()}
    hints = []
    for term in exact:
        if term.value.lower() not in labels:
            close = [label for key, label in labels.items() if term.value.lower()[:3] in key][:8]
            hints.append(f"no node is labelled {term.value!r}" + (f"; did you mean: {', '.join(close)}" if close
                                                                    else ""))  # fmt: skip
    return hints


def describe_fields() -> str:
    lines = ["Fields (prefix with a graph kind to look only there, e.g. aeg.label:DEBUG):"]
    lines += [f"  {name:<11} [{level}] {text}" for name, (level, text) in FIELDS.items()]
    lines += [
        "Numbers: [kind.][level.]property OP number, OP one of > >= < <= = !=, e.g. count>20, edge.probability<0.1,",
        "  walk.steps>=40, node.visits>100 (level defaults to the selected unit, else node); iterations>=3 sets the",
        "  repetitions loop: needs.",
        "Modifiers:",
        f"  select       {', '.join(UNITS)} (default node)",
        "  count        how many results (default 50)",
        "  case         yes: text matching is case-sensitive",
    ]
    return "\n".join(lines)


__all__ = ["FIELDS", "UNITS", "GraphMatch", "GraphQuery", "World", "check_labels", "describe_fields", "execute",
           "parse"]  # fmt: skip
