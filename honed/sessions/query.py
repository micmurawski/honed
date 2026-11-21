"""Sourcegraph-style queries over the session index.

    honed sessions query 'shell:graphify harness:codex at:root select:thread'

A query is a sequence of terms, joined by ``and``/``AND`` (also implied between terms), ``or``/``OR`` and
``not``/``NOT``, grouped with parentheses; ``and`` binds tighter than ``or``. To search for one of these words,
quote it: ``"and"``. A term is either a bare pattern, matched against event text, or a ``field:value``
filter; ``-field:value`` negates a filter. A field may name an operator, ``field.op:value``:

* ``contains`` (default for text fields) follows the pattern type;
* ``is`` matches the whole value exactly (default for attribute fields);
* ``startswith`` is a literal prefix, ignoring leading whitespace;
* ``matches`` is always a regular expression.

``shell`` matches the command itself, as ``ShellCommand`` reads it (also inside ``bash -lc`` and Codex
``exec`` scripts), not the call's JSON: ``shell.startswith:git`` and ``patterntype:regexp shell:^git``
both work. ``status`` on a tool call is its result's status, so ``shell.startswith:pytest
status:failure`` finds the commands that failed.

Modifiers configure the whole query and may appear anywhere at the top level:
``select:event|thread|session``, ``patterntype:keyword|regexp|lexical``, ``case:yes|no``,
``count:N`` and ``internal:yes|no``.

Pattern types, as in Sourcegraph:

* ``keyword`` (default): each bare word is a literal substring, all of them required; ``"quoted text"``
  is an exact phrase and ``/…/`` a regular expression;
* ``regexp`` (or ``regex``): every pattern is a regular expression; quotes only group text containing spaces;
* ``lexical``: full-text search over the word index, ranked by relevance (BM25): bare words are words,
  quotes a phrase of words; it ignores case and punctuation.

A parenthesis that does not pair up is text, not grouping: ``def fun(`` and ``foo)`` are searched for
as written.

Event results carry ``match_ranges``, the spans of their text that the query's patterns matched.

``select`` sets the unit a query matches. For ``event``, every event-level term must hold for the
same event. For ``thread`` and ``session``, each event-level term needs some event in that thread or
session, and a negated one needs none: ``shell:pytest -shell:--lf select:thread`` finds threads
that ran pytest but never with ``--lf``. For ``reflection``, every reflection is a result, also one whose session
is not indexed yet (``bound:no``): it has no session, so session terms do not hold for it.

``count`` gives how many results a query has, without loading them.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast

from .index import SessionIndex
from .models import Event, Run, SessionMetadata

if TYPE_CHECKING:
    from .session import Session, Trajectory

Unit = Literal["event", "thread", "session", "reflection"]
PatternType = Literal["keyword", "regexp", "lexical"]
Operator = Literal["contains", "is", "startswith", "matches"]


class QuerySyntaxError(ValueError):
    """A query that cannot be parsed or compiled; the message points at the offending position."""

    def __init__(self, message: str, query: str = "", position: int | None = None, *, parenthesis: bool = False):
        self.parenthesis = parenthesis  # Parentheses that do not pair up: the query is retried with them as text.
        if query and position is not None:
            message = f"{message}\n  {query}\n  {' ' * position}^"
        super().__init__(message)


# --- Syntax tree -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    """One condition: ``field.operator:value``; bare patterns use the ``content`` field."""

    field: str
    operator: Operator
    value: str
    regex: bool
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
class Query:
    text: str
    node: Node | None
    select: Unit = "event"
    pattern_type: PatternType = "keyword"
    case_sensitive: bool = False
    count: int | None = None
    internal: bool = False


# --- Fields ----------------------------------------------------------------------------------

# Every event reference is ``e``; threads are ``r`` (runs) and sessions are ``s``.
_CONVERSATION = "e.kind IN ('message','tool_call','tool_result') AND COALESCE(e.role,'') NOT IN ('system','developer')"


def _from_call(column: str) -> str:
    """A tool result's tool name or action, read from its call when the result does not record it."""
    return (
        f"COALESCE(e.{column}, (SELECT c.{column} FROM events c WHERE c.run_id=e.run_id "
        "AND c.call_id=e.call_id AND c.kind='tool_call' ORDER BY c.byte_offset LIMIT 1))"
    )


@dataclass(frozen=True)
class FieldSpec:
    """How a field reads its value: an SQL expression, a guard every match must also meet, and its level."""

    expression: str
    level: Literal["event", "thread", "session", "reflection", "rating"]
    default: Operator = "is"
    guard: str = ""
    text: bool = False  # Text fields follow the pattern type; attribute fields compare case-insensitively.
    help: str = ""


FIELDS: dict[str, FieldSpec] = {
    "content": FieldSpec("e.text", "event", "contains", _CONVERSATION, True, "conversation text (bare patterns)"),
    "message": FieldSpec("e.text", "event", "contains", "e.kind='message'", True, "message text, any role"),
    "user": FieldSpec("e.text", "event", "contains", "e.kind='message' AND e.role='user'", True, "user messages"),
    "assistant": FieldSpec(
        "e.text", "event", "contains", "e.kind='message' AND e.role='assistant'", True, "assistant messages"
    ),
    "thinking": FieldSpec("e.text", "event", "contains", "e.kind='thinking'", True, "reasoning text"),
    "call": FieldSpec("e.text", "event", "contains", "e.kind='tool_call'", True, "tool call arguments (JSON)"),
    "result": FieldSpec("e.text", "event", "contains", "e.kind='tool_result'", True, "tool result output"),
    "shell": FieldSpec(
        "honed_shell(e.tool_name, e.action_kind, json_extract(e.data, '$.arguments'))",
        "event",
        "contains",
        "e.kind='tool_call'",
        True,
        "shell command text (not its JSON); with status: the call's outcome",
    ),
    "file": FieldSpec(
        "e.text", "event", "contains", "e.kind='tool_call' AND e.action_kind LIKE 'file.%'", True, "file tool calls"
    ),
    "kind": FieldSpec("e.kind", "event", help="event kind: message, tool_call, tool_result, thinking, …"),
    "role": FieldSpec("e.role", "event", help="user, assistant, tool, system, developer"),
    "tool": FieldSpec(_from_call("tool_name"), "event", help="tool name as the harness records it"),
    "action": FieldSpec(_from_call("action_kind"), "event", help="normalized action; action:shell = shell.*"),
    "status": FieldSpec(
        "COALESCE(e.status, (SELECT x.status FROM events x WHERE x.run_id=e.run_id AND x.call_id=e.call_id "
        "AND x.kind='tool_result' AND e.kind='tool_call' ORDER BY x.byte_offset LIMIT 1))",
        "event",
        help="tool result status (success, failure); a call has its result's status",
    ),
    "harness": FieldSpec("s.harness", "session", help="claude, codex, opencode, pi, …"),
    "source": FieldSpec("s.source_id", "session", help="registered source ID"),
    "session": FieldSpec("s.id", "session", help="session ID, native ID or ID prefix"),
    "title": FieldSpec("s.title", "session", "contains", text=True, help="session title"),
    "cwd": FieldSpec("s.cwd", "session", help="working folder, including its subfolders"),
    "thread": FieldSpec("r.id", "thread", help="thread (run) ID or ID prefix"),
    "at": FieldSpec("r.parent_run_id", "thread", help="root or child (delegated) thread"),
    "family": FieldSpec("r.id", "thread", help="the threads of a family: its root and every thread delegated below "
                        "it, across sessions; give any session, native or run ID (or prefix) in it"),
    "after": FieldSpec("s.started_at", "session", help="started at or after: ISO date, or 12h, 3d, 2w ago"),
    "before": FieldSpec("s.started_at", "session", help="started before: ISO date, or 12h, 3d, 2w ago"),
    "tag": FieldSpec("t.tag", "event", help="event tag: reflection (an in-session reflection's own turns)"),
    # Reflections (``x``) and their ratings (``g``): see ``honed.sessions.reflections``. Reflection terms side by
    # side hold for the same reflection, and rating terms beside them for the same rating of it.
    "reflected": FieldSpec("", "session", help="yes or no: the session has a reflection"),
    "reflection": FieldSpec("x.id", "reflection", help="reflection ID or ID prefix"),
    "bound": FieldSpec("", "reflection", help="yes or no: the reflection is joined to its session (no: the session "
                       "is not indexed yet)"),
    "prompt": FieldSpec("x.prompt_name", "reflection", help="what it was asked: reflect, reflect@1, or a hash p_…"),
    "asked": FieldSpec("x.asked", "reflection", help="the question's fingerprint (prompt and schema), or its prefix"),
    "schema": FieldSpec("x.schema_hash", "reflection", help="the schema's hash s_…, or its prefix"),
    "revision": FieldSpec("x.revision", "reflection", help="the configuration judged: 1.4.0 or 1.4.0@rev_…"),
    "mode": FieldSpec("x.mode", "reflection", help="in-session, handoff or attached"),
    "trigger": FieldSpec("x.trigger", "reflection", "contains", text=True, help="why it was made then"),
    "reflector": FieldSpec("x.reflector_harness", "reflection", help="the harness that reflected"),
    "model": FieldSpec("x.model", "reflection", help="the model that reflected"),
    "coverage": FieldSpec("", "reflection", help="none, partial or full: how far reflections on that question reach"),
    "rated": FieldSpec("g.rating", "rating", guard="g.scale NOT LIKE 'failure%'",
                       help="a rating: helpful, harmful, neutral"),
    "target": FieldSpec("g.target", "rating", help="what a rating cites: skill:NAME, context:FILE, or a file path"),
    "failure": FieldSpec("g.rating", "rating", guard="g.scale LIKE 'failure%'",
                         help="a failure mode rated: premature_done, no_verification, …"),
}
ALIASES = {"run": "thread", "type": "kind"}
_REFLECTION_LEVELS = {"reflection", "rating"}
_SESSION_OF = {"event": "e.session_id", "thread": "r.session_id", "session": "s.id"}
_HIDDEN = "NOT EXISTS (SELECT 1 FROM event_tags t WHERE t.event_id=e.id AND t.tag LIKE 'reflection:%')"
MODIFIERS = {"select", "patterntype", "case", "count", "internal"}
_SELECT = {"event": "event", "events": "event", "thread": "thread", "threads": "thread", "run": "thread",
           "session": "session", "sessions": "session", "reflection": "reflection",
           "reflections": "reflection"}  # fmt: skip
_PATTERN_TYPES = {"keyword": "keyword", "regexp": "regexp", "regex": "regexp", "lexical": "lexical"}
_BOOLEAN = {"yes": True, "true": True, "on": True, "no": False, "false": False, "off": False}


# --- Tokens ----------------------------------------------------------------------------------


@dataclass
class _Token:
    kind: Literal["pattern", "filter", "lparen", "rparen", "and", "or", "not"]
    position: int
    value: str = ""
    quoted: bool = False
    field: str = ""
    negated: bool = False
    end: int = 0


_FIELD = re.compile(r"(-?)([A-Za-z_][\w.]*):")


def _read_value(text: str, start: int, slash: bool = False, groups: bool = True) -> tuple[str, bool, int]:
    """Read a quoted string, a ``/regex/`` (which may contain spaces) when ``slash``, or a bare run of
    characters; with ``groups``, a bare value ends at a ``)`` it did not open."""
    if slash and text.startswith("/", start):
        position = start + 1
        while position < len(text):
            if text[position] == "\\":
                position += 2
                continue
            if text[position] == "/" and (
                position + 1 == len(text) or text[position + 1].isspace() or text[position + 1] == ")"
            ):
                return text[start : position + 1], False, position + 1
            position += 1
    if start < len(text) and text[start] in "\"'":
        quote, position, chars = text[start], start + 1, []
        while position < len(text):
            char = text[position]
            if char == "\\" and position + 1 < len(text) and text[position + 1] in (quote, "\\"):
                chars.append(text[position + 1])
                position += 2
            elif char == quote:
                return "".join(chars), True, position + 1
            else:
                chars.append(char)
                position += 1
        raise QuerySyntaxError("Unterminated quoted string", text, start)
    position, depth = start, 0
    while position < len(text) and not text[position].isspace():
        if not groups:
            pass
        elif text[position] == "(":
            depth += 1
        elif text[position] == ")":
            if depth == 0:
                break
            depth -= 1
        position += 1
    return text[start:position], False, position


def _tokens(text: str, groups: bool = True) -> list[_Token]:
    """Tokens of ``text``; without ``groups`` parentheses are text (a query whose parentheses do not pair up)."""
    tokens, position = [], 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
        elif groups and char in "()":
            tokens.append(_Token("lparen" if char == "(" else "rparen", position, end=position + 1))
            position += 1
        elif (match := _FIELD.match(text, position)) and match.group(2).split(".")[0].lower() in (
            FIELDS.keys() | ALIASES.keys() | MODIFIERS
        ):
            name, _, operator = match.group(2).lower().partition(".")
            spec = FIELDS.get(ALIASES.get(name, name))
            slash = operator == "matches" or (spec is not None and spec.text)
            value, quoted, end = _read_value(text, match.end(), slash, groups)
            tokens.append(_Token("filter", position, value, quoted, match.group(2).lower(), bool(match.group(1)), end))
            position = end
        else:
            value, quoted, end = _read_value(text, position, True, groups)
            # As in Sourcegraph, ``word:`` with an unknown field is text: URLs and ``host:port`` stay searchable.
            if not quoted and value.lower() in {"and", "or", "not"}:
                tokens.append(_Token(value.lower(), position, end=end))  # type: ignore[arg-type]
            else:
                tokens.append(_Token("pattern", position, value, quoted, end=end))
            position = end
    return tokens


# --- Parser ----------------------------------------------------------------------------------


def _modifier(name: str, token: _Token, text: str) -> tuple[str, object]:
    value = token.value.lower()
    try:
        if name == "select":
            return name, _SELECT[value]
        if name == "patterntype":
            return name, _PATTERN_TYPES[value]
        if name in {"case", "internal"}:
            return name, _BOOLEAN[value]
        if name == "count":
            count = int(value)
            if count < 1:
                raise ValueError
            return name, count
    except (KeyError, ValueError):
        pass
    choices = {
        "select": "event, thread, session, reflection",
        "patterntype": "keyword, regexp, lexical",
        "case": "yes, no",
        "internal": "yes, no",
        "count": "a positive integer",
    }[name]
    raise QuerySyntaxError(f"Invalid {name}:{token.value!r}; expected {choices}", text, token.position)


def parse(text: str) -> Query:
    """Parse a query string; raises ``QuerySyntaxError`` with the position of the problem.

    As in Sourcegraph, a query whose parentheses do not pair up is read again with them as text."""
    try:
        return _parse(text, groups=True)
    except QuerySyntaxError as error:
        if not error.parenthesis:
            raise
        return _parse(text, groups=False)


def _delimited(value: str) -> bool:
    return len(value) >= 2 and value[0] == value[-1] == "/"


def _parse(text: str, *, groups: bool) -> Query:
    tokens: list[_Token] = []
    settings: dict[str, Any] = {}
    depth = 0
    for token in _tokens(text, groups):
        depth += {"lparen": 1, "rparen": -1}.get(token.kind, 0)
        name = token.field.split(".")[0]
        if token.kind != "filter" or name not in MODIFIERS:
            tokens.append(token)
            continue
        if depth or token.negated or "." in token.field:
            raise QuerySyntaxError(
                f"{name}: configures the whole query; use it once, at top level", text, token.position
            )
        if name in settings:
            raise QuerySyntaxError(f"{name}: given more than once", text, token.position)
        settings.update([_modifier(name, token, text)])
    pattern_type = settings.get("patterntype", "keyword")
    if pattern_type == "lexical" and settings.get("case"):
        raise QuerySyntaxError("patterntype:lexical always ignores case; drop case:yes", text, 0)
    parser = _Parser(text, tokens, pattern_type)
    node = parser.parse()
    return Query(
        text=text,
        node=node,
        select=settings.get("select", "event"),
        pattern_type=pattern_type,
        case_sensitive=settings.get("case", False),
        count=settings.get("count"),
        internal=settings.get("internal", False),
    )


class _Parser:
    """``or`` binds loosest, then ``and`` (or adjacency), then ``not``/``-``."""

    def __init__(self, text: str, tokens: list[_Token], pattern_type: PatternType) -> None:
        self.text, self.tokens, self.pattern_type, self.position = text, tokens, pattern_type, 0

    def peek(self) -> _Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def fail(self, message: str, token: _Token | None = None, *, parenthesis: bool = False) -> QuerySyntaxError:
        position = token.position if token else len(self.text)
        return QuerySyntaxError(message, self.text, position, parenthesis=parenthesis)

    def parse(self) -> Node | None:
        if not self.tokens:
            return None
        node = self.or_()
        if (token := self.peek()) is not None:
            if token.kind == "rparen":
                raise self.fail("Unbalanced ')'", token, parenthesis=True)
            raise self.fail("Unexpected input", token)
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
            if (closing := self.peek()) is None:
                raise self.fail("Missing ')'", token, parenthesis=True)
            if closing.kind == "rparen":
                raise self.fail("Empty parentheses", closing, parenthesis=True)
            node = self.or_()
            if (closing := self.peek()) is None or closing.kind != "rparen":
                raise self.fail("Missing ')'", token, parenthesis=True)
            self.take()
            return node
        if token.kind == "rparen":
            raise self.fail("Unbalanced ')'", token, parenthesis=True)
        if token.kind in {"and", "or"}:
            raise self.fail(f"Expected a term before {token.kind!r}", token)
        self.take()
        term = self.term(token)
        return Not(term) if token.negated else term

    def term(self, token: _Token) -> Term:
        if token.kind == "pattern":
            name, operator = "content", None
        else:
            name, _, operator = token.field.partition(".")
            name = ALIASES.get(name, name)
            if operator and operator not in {"contains", "is", "startswith", "matches"}:
                raise self.fail(f"Unknown operator {operator!r}; use contains, is, startswith or matches", token)
        spec = FIELDS[name]
        operator = operator or spec.default
        value = token.value
        if not value and not token.quoted:
            raise self.fail(f"{token.field}: needs a value", token)
        delimited = not token.quoted and _delimited(value)
        regex = operator == "matches" or (
            operator == "contains" and (self.pattern_type == "regexp" or (spec.text and delimited))
        )
        if delimited and (spec.text or operator == "matches"):
            value = value[1:-1]
        if regex:
            try:
                re.compile(value)
            except re.error as exc:
                raise self.fail(f"Invalid regular expression {value!r}: {exc}", token) from exc
        return Term(name, operator, value, regex, token.position)  # type: ignore[arg-type]


# --- Compiler --------------------------------------------------------------------------------


def _reflection_term(node: Node) -> bool:
    """A reflection or rating term (not negated): one that groups with its siblings onto one reflection."""
    return isinstance(node, Term) and FIELDS[node.field].level in _REFLECTION_LEVELS


@lru_cache(maxsize=256)
def _compiled(pattern: str, flags: int) -> re.Pattern[str]:
    return re.compile(pattern, flags)


def _shell(tool_name: str | None, action_kind: str | None, arguments: str | None) -> str | None:
    """The command text of a shell call, read like ``ShellCommand``; None for other calls.

    ``json_extract`` gives objects as JSON text and strings as themselves; ``_commands`` accepts both.
    Several literal commands in one Codex ``exec`` script are joined with newlines.
    """
    from .scanner.shell import _commands

    call = SimpleNamespace(tool_name=tool_name, action_kind=action_kind, arguments=arguments, cwd=None)
    commands = [command for command, *_ in _commands(cast(Event, call))]
    return "\n".join(commands) if commands else None


def register_functions(connection: sqlite3.Connection) -> None:
    """Add the matching functions compiled queries call; Python casefold gives explicit Unicode behavior."""

    def regexp(pattern: str, value: str | None, flags: int) -> int:
        return int(value is not None and _compiled(pattern, flags).search(value) is not None)

    def icontains(value: str | None, needle: str) -> int:
        return int(value is not None and needle.casefold() in value.casefold())

    def iequals(value: str | None, other: str) -> int:
        return int(value is not None and value.casefold() == other.casefold())

    def startswith(value: str | None, prefix: str) -> int:
        return int(value is not None and value.lstrip().startswith(prefix))

    def istartswith(value: str | None, prefix: str) -> int:
        return int(value is not None and value.lstrip().casefold().startswith(prefix.casefold()))

    connection.create_function("honed_regexp", 3, regexp, deterministic=True)
    connection.create_function("honed_icontains", 2, icontains, deterministic=True)
    connection.create_function("honed_iequals", 2, iequals, deterministic=True)
    connection.create_function("honed_istartswith", 2, istartswith, deterministic=True)
    connection.create_function("honed_startswith", 2, startswith, deterministic=True)
    connection.create_function("honed_shell", 3, _shell, deterministic=True)


_RELATIVE = re.compile(r"(\d+)\s*(h|d|w)", re.IGNORECASE)


def _moment(value: str, now: datetime) -> str:
    if match := _RELATIVE.fullmatch(value.strip()):
        amount, unit = int(match.group(1)), match.group(2).lower()
        moment = now - timedelta(**{{"h": "hours", "d": "days", "w": "weeks"}[unit]: amount})
    else:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def quoted(value: str) -> str:
    """``value`` as one query value: quoted, its quotes and backslashes escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def any_of(field: str, values: Sequence[str]) -> str:
    """A query term that holds when ``field`` is any of ``values``: ``(field:"a" OR field:"b")``."""
    return "(" + " OR ".join(f"{field}:{quoted(value)}" for value in values) + ")"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _lexical(value: str) -> str:
    """An FTS5 expression: the value's words as one phrase, quoted so its punctuation is not syntax."""
    return '"' + value.replace('"', '""') + '"'


def _lexical_terms(node: Node | None, query: Query) -> list[Term]:
    """The positive terms a lexical query ranks events by: those every match must meet (not under or/not)."""
    if isinstance(node, And):
        return [term for item in node.items for term in _lexical_terms(item, query)]
    if isinstance(node, Term) and _uses_fts(node, query):
        return [node]
    return []


def _uses_fts(term: Term, query: Query) -> bool:
    return (
        query.pattern_type == "lexical"
        and term.operator == "contains"
        and not term.regex
        and FIELDS[term.field].expression == "e.text"
    )


SCOPE = "temp.honed_scope"  # Event IDs a query is restricted to (``EventSequence.query``), when present.


@dataclass
class _Compiler:
    query: Query
    now: datetime
    scoped: bool = False
    params: list[object] = field(default_factory=list)

    def compile(self, node: Node | None, unit: Unit) -> str:
        if node is None:
            return "1"
        if isinstance(node, And):
            grouped = [item for item in node.items if _reflection_term(item)]
            rest = [self.compile(item, unit) for item in node.items if not _reflection_term(item)]
            if grouped:
                rest.append(self.reflections(cast("list[Term]", grouped), unit))
            return "(" + " AND ".join(rest) + ")"
        if isinstance(node, Or):
            return "(" + " OR ".join(self.compile(item, unit) for item in node.items) + ")"
        if isinstance(node, Not):
            return f"NOT {self.compile(node.item, unit)}"
        if _reflection_term(node):
            return self.reflections([node], unit)
        return self.term(node, unit)

    def reflections(self, terms: list[Term], unit: Unit) -> str:
        """Reflection and rating terms side by side, held by one reflection (``x``) and one rating of it (``g``):
        the reflection selected by ``select:reflection``, else one of the session's."""
        coverage = [term for term in terms if term.field == "coverage"]
        if len(coverage) > 1:
            raise QuerySyntaxError("coverage: given twice for one reflection", self.query.text, coverage[1].position)
        none = bool(coverage) and coverage[0].value.lower() == "none"
        if none:
            if unit == "reflection":
                return "0"  # A reflection always covers something of its question.
            terms = [term for term in terms if term.field != "coverage"]
        conditions = [self.reflection_condition(term) for term in terms if FIELDS[term.field].level == "reflection"]
        if ratings := [self.condition(term, FIELDS[term.field], unit) for term in terms
                       if FIELDS[term.field].level == "rating"]:
            conditions.append("EXISTS (SELECT 1 FROM reflection_ratings g WHERE g.reflection_id=x.id AND "
                              + " AND ".join(ratings) + ")")
        if unit == "reflection":
            return "(" + (" AND ".join(conditions) or "1") + ")"
        found = (f"EXISTS (SELECT 1 FROM reflections x WHERE x.session_id={_SESSION_OF[unit]}"
                 + "".join(f" AND {condition}" for condition in conditions) + ")")
        return f"NOT {found}" if none else found

    def reflection_condition(self, term: Term) -> str:
        value = term.value
        if term.field == "coverage":
            state = value.lower()
            if state not in {"partial", "full"}:
                raise QuerySyntaxError("coverage: takes none, partial or full", self.query.text, term.position)
            self.params.append(state)
            return ("EXISTS (SELECT 1 FROM reflection_coverage c WHERE c.session_id=x.session_id AND c.asked=x.asked "
                    "AND c.revision=x.revision AND c.state=?)")
        if term.field == "prompt" and term.operator == "is":
            if value.lower().startswith("p_"):
                self.params.append(_escape_like(value) + "%")
                return "x.prompt_hash LIKE ? ESCAPE '\\'"
            name, _, version = value.partition("@")
            self.params.append(name)
            if not version:
                return "honed_iequals(x.prompt_name, ?)"
            self.params.append(version)
            return "(honed_iequals(x.prompt_name, ?) AND x.prompt_version = ?)"
        if term.field == "bound":
            answer = _BOOLEAN.get(value.lower())
            if answer is None:
                raise QuerySyntaxError("bound: takes yes or no", self.query.text, term.position)
            return f"x.session_id IS {'NOT ' if answer else ''}NULL"
        if term.field in {"asked", "schema", "reflection"} and term.operator == "is":
            self.params.append(_escape_like(value) + "%")
            return f"{FIELDS[term.field].expression} LIKE ? ESCAPE '\\'"
        if term.field == "revision" and term.operator == "is" and "@" not in value:
            self.params.extend([value, _escape_like(value) + "@%"])
            return "(x.revision = ? OR x.revision LIKE ? ESCAPE '\\')"
        return self.match(term, FIELDS[term.field])

    def term(self, term: Term, unit: Unit) -> str:
        spec = FIELDS[term.field]
        condition = self.condition(term, spec, unit)
        if spec.level == "event" and unit != "event":
            column = "e.run_id=r.id" if unit == "thread" else "e.session_id=s.id"
            scope = f" AND e.id IN {SCOPE}" if self.scoped else ""
            return f"EXISTS (SELECT 1 FROM events e WHERE {column}{scope} AND {condition})"
        if spec.level == "thread" and unit in {"session", "reflection"}:
            return f"EXISTS (SELECT 1 FROM runs r WHERE r.session_id=s.id AND {condition})"
        return condition

    def condition(self, term: Term, spec: FieldSpec, unit: Unit) -> str:
        guard = spec.guard
        if term.field == "content" and self.query.internal:
            guard = ""
        if spec.level == "event" and spec.text and not self.query.internal:
            guard = f"{guard} AND {_HIDDEN}" if guard else _HIDDEN  # An in-session reflection's own turns.
        if term.field == "at":
            return self.at(term, unit)
        if term.field == "family":
            return self.family(term)
        if term.field == "reflected":
            answer = _BOOLEAN.get(term.value.lower())
            if answer is None:
                raise QuerySyntaxError("reflected: takes yes or no", self.query.text, term.position)
            found = "EXISTS (SELECT 1 FROM reflections x WHERE x.session_id=s.id)"
            return found if answer else f"NOT {found}"
        if term.field == "tag":
            self.params.extend([term.value, _escape_like(term.value) + ":%"])
            return ("EXISTS (SELECT 1 FROM event_tags t WHERE t.event_id=e.id AND "
                    "(honed_iequals(t.tag, ?) OR t.tag LIKE ? ESCAPE '\\'))")
        if term.field == "target" and term.operator == "is":
            value = term.value
            kind, _, name = value.partition(":")
            self.params.extend([value, _escape_like(value) + "#%", _escape_like(value) + "%"])
            match = "g.target = ? OR g.target LIKE ? ESCAPE '\\' OR g.ref LIKE ? ESCAPE '\\'"
            if kind == "skill" and name:  # A skill holds its files.
                self.params.append(f"skill_file:{_escape_like(name)}/%")
                match += " OR g.target LIKE ? ESCAPE '\\'"
            return f"({match})"
        if term.field in {"after", "before"}:
            try:
                self.params.append(_moment(term.value, self.now))
            except ValueError as exc:
                raise QuerySyntaxError(
                    f"Invalid date {term.value!r}; use an ISO date or 12h, 3d, 2w", self.query.text, term.position
                ) from exc
            return f"{spec.expression} {'>=' if term.field == 'after' else '<'} ?"
        if term.operator == "is" and term.field in {"session", "thread"}:
            prefix = _escape_like(term.value) + "%"
            if term.field == "session":
                self.params.extend([prefix, term.value])
                match = "(s.id LIKE ? ESCAPE '\\' OR s.native_id = ?)"
            else:
                self.params.append(prefix)
                match = "r.id LIKE ? ESCAPE '\\'"
        elif term.operator == "is" and term.field == "cwd":
            folder = os.path.expanduser(term.value).rstrip("/") or "/"
            self.params.extend([folder, _escape_like(folder).rstrip("/") + "/%"])
            # A reflection's: its session's folder, or, not yet joined to one, the project it was made in.
            cwd = "COALESCE(s.cwd, json_extract(x.data, '$.evidence.project'))" if unit == "reflection" else "s.cwd"
            match = f"({cwd} = ? OR {cwd} LIKE ? ESCAPE '\\')"
        elif term.operator == "is" and term.field == "action":
            self.params.extend([term.value, _escape_like(term.value.lower()) + ".%"])
            match = f"(honed_iequals({spec.expression}, ?) OR lower({spec.expression}) LIKE ? ESCAPE '\\')"
        else:
            match = self.match(term, spec)
        return f"({guard} AND {match})" if guard else match

    def match(self, term: Term, spec: FieldSpec) -> str:
        expression = spec.expression
        sensitive = self.query.case_sensitive and spec.text
        if _uses_fts(term, self.query):
            self.params.append(_lexical(term.value))
            return "e.id IN (SELECT event_id FROM event_text WHERE event_text MATCH ?)"
        self.params.append(term.value)
        if term.regex:
            flags = 0 if sensitive else re.IGNORECASE
            self.params.append(flags)
            # Parameters bind in order; the pattern precedes the value it is matched against.
            return f"honed_regexp(?, {expression}, ?)"
        if term.operator == "is":
            return f"{expression} = ?" if sensitive else f"honed_iequals({expression}, ?)"
        if term.operator == "startswith":
            return f"honed_{'' if sensitive else 'i'}startswith({expression}, ?)"
        return f"instr({expression}, ?) > 0" if sensitive else f"honed_icontains({expression}, ?)"

    def family(self, term: Term) -> str:
        """The thread is in the family of a thread the reference names: up its parent links to the root (possibly in
        another session, as Codex records subagents), then down every child link from there."""
        prefix = _escape_like(term.value) + "%"
        self.params.extend([prefix, prefix, term.value])
        return (
            "r.id IN (WITH RECURSIVE "
            "start(id) AS (SELECT r0.id FROM runs r0 JOIN sessions s0 ON s0.id = r0.session_id "
            "WHERE r0.id LIKE ? ESCAPE '\\' OR s0.id LIKE ? ESCAPE '\\' OR s0.native_id = ?), "
            "up(id, parent) AS (SELECT r1.id, r1.parent_run_id FROM runs r1 WHERE r1.id IN (SELECT id FROM start) "
            "UNION SELECT r2.id, r2.parent_run_id FROM runs r2 JOIN up ON r2.id = up.parent), "
            "roots(id) AS (SELECT id FROM up WHERE parent IS NULL OR parent NOT IN (SELECT id FROM runs)), "
            "down(id) AS (SELECT id FROM roots "
            "UNION SELECT r3.id FROM runs r3 JOIN down ON r3.parent_run_id = down.id) "
            "SELECT id FROM down)"
        )

    def at(self, term: Term, unit: Unit) -> str:
        value = term.value.lower()
        if value not in {"root", "child"}:
            raise QuerySyntaxError("at: takes root or child", self.query.text, term.position)
        column = "s.parent_session_id" if unit in {"session", "reflection"} else "r.parent_run_id"
        return f"{column} IS {'' if value == 'root' else 'NOT '}NULL"


def compile_query(query: Query, *, now: datetime | None = None, scoped: bool = False) -> tuple[str, list[object]]:
    """The SQL selecting the query's unit, and its parameters, without limits. ``scoped`` restricts it to the
    events in ``SCOPE``. Lexical event queries are ordered by relevance, the rest newest session first."""
    compiler = _Compiler(query, now or datetime.now(UTC), scoped)
    where = compiler.compile(query.node, query.select)
    scope = (
        {
            "reflection": "",
            "event": f" AND e.id IN {SCOPE}",
            "thread": f" AND EXISTS (SELECT 1 FROM events e WHERE e.run_id=r.id AND e.id IN {SCOPE})",
            "session": f" AND EXISTS (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.id IN {SCOPE})",
        }[query.select]
        if scoped
        else ""
    )
    if query.select == "reflection":
        scope = f" AND EXISTS (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.id IN {SCOPE})" if scoped else ""
        sql = (f"SELECT x.*, x.session_id AS session_id FROM reflections x LEFT JOIN sessions s ON s.id=x.session_id "
               f"WHERE {where}{scope} ORDER BY x.at DESC, x.id")
    elif query.select == "event":
        rank, order = "", ""
        if ranked := _lexical_terms(query.node, query):
            rank = (
                " LEFT JOIN (SELECT event_id, bm25(event_text) AS score FROM event_text WHERE event_text MATCH ?) "
                "ranked ON ranked.event_id=e.id"
            )
            compiler.params.insert(0, " ".join(_lexical(term.value) for term in ranked))
            order = "ranked.score, "
        sql = (
            "SELECT e.data AS data, e.session_id AS session_id FROM events e "
            f"JOIN runs r ON r.id=e.run_id JOIN sessions s ON s.id=e.session_id{rank} "
            f"WHERE {where}{scope} ORDER BY {order}s.started_at DESC, e.source_id, e.session_id, e.path, "
            "e.byte_offset, e.subrecord, e.id"
        )
    elif query.select == "thread":
        sql = (
            "SELECT r.* FROM runs r JOIN sessions s ON s.id=r.session_id "
            f"WHERE {where}{scope} ORDER BY s.started_at DESC, r.started_at, r.id"
        )
    else:
        sql = f"SELECT s.* FROM sessions s WHERE {where}{scope} ORDER BY s.started_at DESC, s.id"
    return sql, compiler.params


def match_ranges(query: Query, text: str) -> tuple[tuple[int, int], ...]:
    """Where ``query``'s positive text patterns matched ``text`` (an event's text), merged and in order."""
    found: list[tuple[int, int]] = []
    for term in _positive_terms(query.node):
        if FIELDS[term.field].expression != "e.text":
            continue  # Shell commands, titles and attributes are not spans of the event's text.
        sensitive = query.case_sensitive
        if _uses_fts(term, query):
            for word in re.findall(r"\w+", term.value):
                found += SessionIndex._literal_ranges(text, word, False)
        elif term.regex:
            pattern = _compiled(term.value, 0 if sensitive else re.IGNORECASE)
            found += [match.span() for match in pattern.finditer(text) if match.end() > match.start()]
        elif term.operator == "contains":
            found += SessionIndex._literal_ranges(text, term.value, sensitive)
        elif term.operator == "startswith":
            start = len(text) - len(text.lstrip())
            found += [(start, start + len(term.value))]
        elif term.operator == "is":
            found += [(0, len(text))]
    merged: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _positive_terms(node: Node | None) -> list[Term]:
    if isinstance(node, Term):
        return [node]
    if isinstance(node, And | Or):
        return [term for item in node.items for term in _positive_terms(item)]
    return []


# --- Execution -------------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryMatch:
    """One result: its session (None only for a reflection not yet joined to one); the thread for
    ``select:thread``; the event for ``select:event``, with the spans of its text the query matched; the reflection
    for ``select:reflection``. ``history`` and ``trajectory`` need a loader: results of ``Sessions.query`` and
    ``EventSequence.query`` have one."""

    session: SessionMetadata | None
    thread: Run | None = None
    event: Event | None = None
    match_ranges: tuple[tuple[int, int], ...] = ()
    reflection: dict[str, Any] | None = None  # For ``select:reflection``: its index row, ``data`` its whole record.
    loader: Callable[[str], Session] | None = field(default=None, compare=False, repr=False)

    @property
    def history(self) -> Session:
        """The session this result belongs to, with its events, threads and trajectories."""
        if self.loader is None:
            raise ValueError("This result has no session loader; use Sessions.query or EventSequence.query")
        if self.session is None:
            raise ValueError("This reflection is not joined to an indexed session")
        return self.loader(self.session.id)

    @property
    def trajectory(self) -> Trajectory | None:
        """The trajectory an event result belongs to; None for other results and unassigned events."""
        if self.event is None:
            return None
        event_id = self.event.id
        return next((t for t in self.history.trajectories if any(e.id == event_id for e in t.selected_events)), None)

    @property
    def type(self) -> Unit:
        if self.reflection is not None:
            return "reflection"
        return "event" if self.event is not None else "thread" if self.thread is not None else "session"

    def to_dict(self) -> dict[str, Any]:
        """``{"type": "event"|"thread"|"session", "data": {...}}``; event and thread data carry ``session_id``."""
        if self.event is not None:
            # The native record is large and redundant with the normalized fields; `reference` still locates it.
            data = self.event.model_dump(mode="json", exclude={"raw", "raw_pointer"})
            return {"type": "event", "data": data, "match_ranges": [list(span) for span in self.match_ranges]}
        if self.reflection is not None:
            return {"type": "reflection", "data": self.reflection}
        item = self.thread or self.session
        assert item is not None  # Only a reflection may have no session.
        return {"type": self.type, "data": item.model_dump(mode="json")}


def count(index: SessionIndex, query: str | Query, *, now: datetime | None = None) -> int:
    """How many results ``query`` has in a ``SessionIndex`` (its ``count:`` aside), without loading them."""
    parsed = parse(query) if isinstance(query, str) else query
    sql, params = compile_query(parsed, now=now)
    register_functions(index.connection)
    return index.connection.execute(f"SELECT count(*) FROM ({sql})", params).fetchone()[0]


def execute(
    index: SessionIndex,
    query: str | Query,
    *,
    limit: int | None = None,
    offset: int = 0,
    now: datetime | None = None,
    scope: Sequence[str] | None = None,
    loader: Callable[[str], Session] | None = None,
    default_limit: int | None = None,
) -> list[QueryMatch]:
    """Run a query against a ``SessionIndex``. ``limit`` overrides the query's ``count:``; without either, results
    stop at ``default_limit`` (None: all). ``scope`` restricts the query to these event IDs; ``loader`` gives results
    their ``history`` and ``trajectory``."""
    parsed = parse(query) if isinstance(query, str) else query
    sql, params = compile_query(parsed, now=now, scoped=scope is not None)
    count = limit or parsed.count or default_limit or -1  # SQLite: a negative limit is none.
    register_functions(index.connection)
    if scope is not None:
        index.connection.execute(f"CREATE TEMP TABLE IF NOT EXISTS {SCOPE.split('.')[1]}(id TEXT PRIMARY KEY)")
        index.connection.execute(f"DELETE FROM {SCOPE}")
        index.connection.executemany(f"INSERT OR IGNORE INTO {SCOPE} VALUES (?)", ((i,) for i in scope))
    rows = index.connection.execute(f"{sql} LIMIT ? OFFSET ?", (*params, count, offset)).fetchall()
    sessions: dict[str, SessionMetadata] = {}

    def session(session_id: str) -> SessionMetadata:
        if session_id not in sessions:
            sessions[session_id] = index.get_session(session_id)
        return sessions[session_id]

    build: Callable[[sqlite3.Row], QueryMatch]
    if parsed.select == "event":

        def build(row: sqlite3.Row) -> QueryMatch:
            event = index._enrich(Event.model_validate_json(row["data"]))
            ranges = match_ranges(parsed, event.text)
            return QueryMatch(session(row["session_id"]), event=event, match_ranges=ranges, loader=loader)

    elif parsed.select == "reflection":

        def build(row: sqlite3.Row) -> QueryMatch:
            found = {key: row[key] for key in row.keys() if key not in {"data", "session_id"}}  # noqa: SIM118
            found["session_id"] = row["session_id"]
            found["data"] = json.loads(row["data"])
            bound = session(row["session_id"]) if row["session_id"] is not None else None
            return QueryMatch(bound, reflection=found, loader=loader)

    elif parsed.select == "thread":

        def build(row: sqlite3.Row) -> QueryMatch:
            return QueryMatch(session(row["session_id"]), thread=SessionIndex._run_from_row(row), loader=loader)

    else:

        def build(row: sqlite3.Row) -> QueryMatch:
            return QueryMatch(SessionIndex._session_from_row(row), loader=loader)

    return [build(row) for row in rows]


def describe_fields() -> str:
    """A table of fields and modifiers for ``--fields``."""
    lines = ["Fields (field:value, -field:value, field.contains|is|startswith|matches:value):"]
    for name, spec in FIELDS.items():
        lines.append(f"  {name:<10} {spec.level:<10} {spec.help}")
    lines += [
        "",
        "Reflection and rating fields side by side hold for the same reflection (and the same rating of it):",
        "  `prompt:reflect@1 revision:1.4.0` is one reflection with both; NOT ( … ) is no reflection with all of them.",
        "",
        "Modifiers:",
        "  select       event (default), thread, session, reflection",
        '  patterntype  keyword (default: every word, anywhere; "quotes" for a phrase), regexp (or regex),',
        "               lexical (full-text word search, ranked by relevance)",
        "  case         no (default), yes",
        "  count        maximum results (default 50)",
        "  internal     no (default), yes: text patterns also search system/developer prompts, metadata, and",
        "               in-session reflections' own turns",
        "",
        'Operators: and/AND (implicit), or/OR, not/NOT, -field:, ( ). Patterns: word, "exact phrase", /regex/.',
    ]
    return "\n".join(lines)
