"""``sessions`` commands: register transcript sources, synchronize, find, and render sessions."""

from __future__ import annotations

import functools
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import click

from honed.harnesses import HARNESSES, Harness
from honed.utils import AliasedGroup

from .analysis.export import export_workgraphs
from .analysis.weighted_patterns import WeightedPatternView, export_weighted_pattern_view
from .events import UnsupportedConversion
from .export import FORMATS
from .install import INSTALLABLE, InstalledSession
from .session import EventSequence, Session
from .sessions import DEFAULT_INDEX, Sessions, default_index


def _errors(command: Callable[..., None]) -> Callable[..., None]:
    """Report expected failures (unknown references, bad input, unreadable files) as CLI errors."""

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            return command(*args, **kwargs)
        except (ValueError, LookupError, OSError) as exc:
            raise click.ClickException(str(exc).strip("'")) from exc

    return wrapper


def _one_line(text: str | None, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


@click.group(name="sessions", cls=AliasedGroup, aliases=["s"])
@click.option(
    "--index", type=click.Path(path_type=Path), default=default_index, show_default="$HONED_INDEX, else "
    f"{DEFAULT_INDEX}", help="Session index file: where sessions and their reflections are kept."
)
@click.pass_context
def sessions(ctx: click.Context, index: Path) -> None:
    """Collect agent transcripts into a session index, then find and render sessions."""
    ctx.obj = index


@sessions.command("sync", aliases=["sy"])
@click.argument("paths", nargs=-1, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--harness", type=click.Choice(HARNESSES), help="With PATHS: skip detection and use this harness.")
@click.option("--format", "source_format", type=click.Choice(["native", "dataclaw"]), default="native",
              show_default=True, help="With PATHS: native transcripts, or collector batches.")  # fmt: skip
@click.option("--source-id", multiple=True, help="With one PATH: its stable ID (default: harness, format and root). "
              "Without PATHS: sync only these sources.")  # fmt: skip
@click.pass_obj
@_errors
def sync_command(index: Path, paths: tuple[Path, ...], harness: Harness | None,
                 source_format: Literal["native", "dataclaw"], source_id: tuple[str, ...]) -> None:  # fmt: skip
    """Read new and changed transcripts into the index; PATHS are registered as sources first.

    \b
    Without PATHS every registered source is read (or those --source-id names). The report lists every registered
    source, then what was read.
    \b
      honed sessions sync ~/.claude/projects ~/.codex/sessions     # register (once) and read
      honed sessions sync                                          # read what changed since
      honed sessions sync batches/2026-11 --format dataclaw --source-id lab
    """
    if paths and len(source_id) > 1:
        raise click.UsageError("--source-id names one source when PATHS are given")
    if paths and len(paths) > 1 and source_id:
        raise click.UsageError("--source-id applies to a single path")
    with Sessions(index) as store:
        chosen = list(source_id) or None
        if paths:
            chosen = [store.add(path, harness=harness, format=source_format, source_id=source_id[0] if source_id
                                else None, sync=False).id for path in paths]  # fmt: skip
        report = store.sync(source_ids=chosen)
        sources = [{"id": s.id, "harness": s.harness, "format": s.format, "root": str(s.root)}
                   for s in store.sources()]  # fmt: skip
    click.echo(json.dumps({"sources": sources, **report.model_dump(mode="json")}, ensure_ascii=False, indent=2))


@sessions.command("trajectories", aliases=["t"])
@click.argument("session", required=False)
@click.pass_obj
@_errors
def trajectories_command(index: Path, session: str | None) -> None:
    """List the trajectories of SESSION (any ID, native ID, run ID or unique prefix; default: latest)."""
    with Sessions(index) as store:
        selected = store.get(session)
        split = selected.trajectories
        click.echo(
            f"{selected.metadata.native_id}: {len(split)} trajectories, "
            f"{len(split.unassigned_events)} unassigned events"
        )
        for number, trajectory in enumerate(split):
            click.echo(
                f"{number:>3}  {trajectory.boundary:<15} {len(trajectory.events):>5} events  "
                f"{_one_line(trajectory.prompt_text, 70)}"
            )


@sessions.command(
    "query", aliases=["q"], context_settings={"ignore_unknown_options": True, "help_option_names": ["--help"]}
)
@click.argument("words", nargs=-1)
@click.option("--limit", type=click.IntRange(1, 1000), help="Maximum results; overrides count: (default 50).")
@click.option("--offset", type=click.IntRange(min=0), default=0)
@click.option("--json", "as_json", is_flag=True, help='One JSON object: {"results": [{"type": ..., "data": {...}}]}.')
@click.option("--fields", "show_fields", is_flag=True, help="List the fields and modifiers, then exit.")
@click.pass_obj
@_errors
def query_command(
    index: Path, words: tuple[str, ...], limit: int | None, offset: int, as_json: bool, show_fields: bool
) -> None:
    """Find events, threads or sessions with a Sourcegraph-style query.

    A query is a list of terms. Bare words match conversation text; `field:value` filters narrow the results;
    modifiers such as `select:` and `patterntype:` configure the whole query. The query can be one quoted argument
    or several words (they are joined with spaces); quote it when it contains shell characters like ( ) * | " $.

    \b
    Terms and operators
      def fun(               both `def` and `fun(` (AND is implied between terms)
      "def fun("             the exact phrase; also how to search for `and`, `or`, `not` themselves
      /def\\s+\\w+\\(/          a regular expression (in keyword mode)
      a AND b, a and b       both
      a OR b, a or b         either; AND binds tighter: `a b OR c` is `(a AND b) OR c`
      NOT a, not a           without a
      -harness:pi            negates a filter (a leading `-` on a bare word is text: `--force`)
      ( ... )                groups; a parenthesis that does not pair up is text: `foo(`, `bar)`

    \b
    Pattern types (patterntype:)
      keyword   default: literal substrings, all required; "quotes" for a phrase, /…/ for a regex
      regexp    every pattern is a regular expression (also `regex`)
      lexical   full-text search by whole words, ranked by relevance; ignores case and punctuation

    \b
    Filters (field:value; field.contains|is|startswith|matches:value picks the comparison)
      content, message, user, assistant, thinking, call, result   text of those events
      shell        the shell command itself (not its JSON), also inside `bash -lc` and Codex exec
      file         file tool calls (read, write, edit)
      kind, role, tool, action, status       event attributes; a call has its result's status
      harness, source, session, title, cwd   the session; session: takes an ID prefix or native ID
      thread, at:root|child                  the thread (run): main agent or a subagent
      after:, before:                        session start: 2026-10-01, 12h, 3d, 2w
      tag                                    event tags: tag:reflection (a reflection's own turns)
      reflected:yes|no                       the session has a reflection
      prompt, asked, schema, revision, mode, trigger, reflector, model, coverage:none|partial|full
                                             a reflection: prompt:reflect, prompt:reflect@1, prompt:p_4be1
      rated, target, failure                 a rating of it: rated:harmful target:skill:deploy
    Reflection and rating filters side by side hold for the same reflection (and one rating of it).
    Text filters ignore case unless `case:yes`; `--fields` lists them all.

    \b
    Modifiers
      select:event|thread|session|reflection   what to return (default event)
      count:N                       how many (default 50; --limit overrides)
      case:yes                      match case
      internal:yes                  text also searches system prompts, metadata and reflections' own turns

    With select:event every term must hold for the same event. With select:thread or select:session each term
    needs some event in that thread or session, and a negated term needs none.

    \b
    Examples (`hnd s q` is short for `honed sessions query`)
      # Root threads that ran graphify in a shell
      honed sessions query shell:graphify at:root select:thread
      hnd s q shell:graphify at:root select:thread
    \b
      # Failed pytest runs in the last week, as JSON
      honed sessions query --json 'shell.startswith:pytest status:failure after:7d'
    \b
      # Commands that start with git diff or git log
      honed sessions query 'patterntype:regexp shell:"^git\\s+(diff|log)"'
    \b
      # Sessions that mention a phrase, outside the Pi harness
      honed sessions query '"connection refused" -harness:pi select:session'
    \b
      # Events containing both terms, or either one
      honed sessions query 'graphify and pytest'
      honed sessions query 'graphify OR pytest count:10'
    \b
      # Threads that ran pytest but never with --lf
      honed sessions query 'shell:pytest -shell:--lf select:thread'
    \b
      # A thread family (its root and every subagent below it, across sessions), and what it delegated
      honed sessions query 'family:abc123 select:thread'
      honed sessions query 'family:abc123 action:agent'
    \b
      # Full-text search, best matches first
      honed sessions query 'patterntype:lexical retry timeout'
    \b
      # What the user asked in Claude sessions under a folder
      honed sessions query 'user:refactor harness:claude cwd:~/GITHUB/honed'
    \b
      # Sessions never reflected on, or not with the reflect prompt (any version)
      honed sessions query 'select:session -reflected:yes'
      honed sessions query 'select:session -prompt:reflect'
    \b
      # Claude sessions of the last week not fully covered by reflect@1 on release 1.4.0
      honed sessions query 'harness:claude after:7d select:session NOT (prompt:reflect@1 revision:1.4.0 coverage:full)'
    \b
      # Every reflection on a session; in-session ones that rated the deploy skill harmful
      honed sessions query 'session:abc123 select:reflection'
      honed sessions query 'select:reflection mode:in-session rated:harmful target:skill:deploy'
    """
    from .query import describe_fields, execute

    if show_fields:
        click.echo(describe_fields())
        return
    text = " ".join(words)
    if not text.strip():
        raise click.UsageError("give a query, or --fields to list what it can use")
    with Sessions(index) as store:
        matches = execute(store.index, text, limit=limit, offset=offset, default_limit=50)
        if not matches:
            empty = store.index.connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone() is None
            if empty:
                click.echo(f"No sessions are indexed in {store.path}; run `honed sessions sync PATH`.", err=True)
            else:
                click.echo("No matches.", err=True)
    if as_json:
        click.echo(json.dumps({"results": [match.to_dict() for match in matches]}, ensure_ascii=False))
        return
    for match in matches:
        session = match.session
        if match.reflection is not None:
            found = match.reflection
            prompt = found["prompt_name"] or found["prompt_hash"] or "-"
            prompt += f"@{found['prompt_version']}" if found["prompt_name"] and found["prompt_version"] else ""
            ratings = len((found["data"] or {}).get("items") or [])
            bound = session.id[:12] if session is not None else "(unbound)"
            click.echo(f"{found['id'][:22]:<22}  {bound:<12}  {prompt:<12} {found['mode']:<10} "
                       f"{found['revision'][:24]:<24} {ratings:>3} rating(s)")
            continue
        assert session is not None  # Only a reflection may have no session.
        if match.event is not None:
            event = match.event
            label = event.tool_name or event.role or ""
            # Show the text around the first match, as a search engine's snippet does.
            start = max(0, match.match_ranges[0][0] - 20) if match.match_ranges else 0
            snippet = ("…" if start else "") + _one_line(event.text[start:], 69 if start else 70)
            click.echo(f"{event.id[:12]}  {session.id[:12]}  {event.kind:<11} {label:<10} {snippet}")
        elif match.thread is not None:
            thread = match.thread
            at = "child" if thread.parent_run_id else "root"
            click.echo(
                f"{thread.id[:12]}  {session.id[:12]}  {session.harness:<11} {at:<5} "
                f"{thread.event_count:>5} events  {_one_line(session.title or session.cwd, 50)}"
            )
        else:
            click.echo(
                f"{session.id[:12]}  {session.harness:<11} {(session.started_at or '')[:19]:<19}  "
                f"{session.event_count:>5} events  {_one_line(session.title or session.cwd, 60)}"
            )


@sessions.command("show", aliases=["sh"])
@click.argument("event_id")
@click.option("--context", type=click.IntRange(0, 1000), default=3, show_default=True)
@click.pass_obj
@_errors
def show_command(index: Path, event_id: str, context: int) -> None:
    """Show an event with surrounding events from its run (JSON lines)."""
    with Sessions(index) as store:
        try:
            events = store.index.get_context(event_id, context, context)
        except KeyError as exc:
            raise click.ClickException(f"Unknown event: {event_id}") from exc
        for event in events:
            click.echo(event.model_dump_json())


@sessions.command("render", aliases=["r"])
@click.argument("session", required=False)
@click.option("--trajectory", type=click.IntRange(min=0), help="Zero-based trajectory number (see `trajectories`).")
@click.option("--through", is_flag=True, help="Render the session up to and including the selected trajectory.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", *FORMATS, "agy"]),
    default="markdown",
    show_default=True,
    help="agy is short for antigravity.",
)
@click.option("--header/--no-header", default=True, help="Markdown: prefix a session summary.")
@click.option(
    "--best-effort",
    is_flag=True,
    help="Harness formats: keep events the format cannot represent as labelled text; list them on stderr.",
)
@click.pass_obj
@_errors
def render_command(
    index: Path,
    session: str | None,
    trajectory: int | None,
    through: bool,
    output_format: str,
    header: bool,
    best_effort: bool,
) -> None:
    """Render SESSION (any ID, native ID, run ID or unique prefix; default: latest)."""
    if through and trajectory is None:
        raise click.UsageError("--through requires --trajectory")
    with Sessions(index) as store:
        selected = store.get(session)
        meta = selected.metadata
        trajectories = selected.trajectories
        scope = "whole session"
        rendered: EventSequence = selected
        if trajectory is not None:
            if trajectory >= len(trajectories):
                count = len(trajectories)
                raise click.ClickException(f"Session has {count} trajectories; use 0..{count - 1}")
            scope = f"trajectory {trajectory} of {len(trajectories)}" + (" with prefix" if through else "")
            chosen = trajectories[trajectory]
            rendered = selected.through(chosen) if through else chosen
        if output_format != "markdown":
            try:
                exported = rendered.export(output_format, strict=not best_effort)
            except UnsupportedConversion as exc:
                raise click.ClickException(f"{exc}\nUse --best-effort to keep such events as labelled text.") from exc
            click.echo(exported.text, nl=False)
            if best_effort:
                for note in exported.transformations:
                    click.echo(f"note: {note}", err=True)
            return
        if header:
            click.echo(
                f"# Session {meta.native_id}\n\n"
                f"- Harness: {meta.harness}\n- Indexed ID: {meta.id}\n"
                f"- Started: {meta.started_at or 'unknown'}\n- Directory: {meta.cwd or 'unknown'}\n"
                f"- Showing: {scope} ({len(trajectories)} trajectories in session)\n"
            )
        click.echo(rendered.to_markdown(), nl=False)


def _results(store: Sessions, text: str) -> list[dict[str, Any]]:
    """Query results as ``query --json`` gives them: from its JSON, or from IDs (session or run), one per line."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return list(json.loads(text)["results"])
        except (ValueError, KeyError, TypeError) as exc:
            raise click.UsageError("stdin is neither `honed sessions query --json` output nor IDs, one per line") \
                from exc  # fmt: skip
    return [{"type": "session", "data": {"id": store.resolve(line.strip())}} for line in text.splitlines()
            if line.strip()]  # fmt: skip


def _histories(store: Sessions, results: list[dict[str, Any]], *, roots: bool) -> list[Any]:
    """The sessions and threads the results name, in order and once each. An event stands for its thread, a
    reflection for its session; with ``roots``, a session stands for its root threads."""
    keys: list[tuple[str, str | None]] = []
    lifted = 0
    for item in results:
        data = item.get("data", {})
        kind = item.get("type")
        if kind == "thread":
            keys.append((data["session_id"], data["id"]))
        elif kind == "event":
            keys.append((data["session_id"], data["run_id"]))
            lifted += 1
        elif kind == "reflection":
            keys.append((data["session_id"], None))
        else:
            keys.append((data["id"], None))
    keys = list(dict.fromkeys(keys))
    if lifted:
        threads = sum(thread is not None for _, thread in keys)
        click.echo(f"{lifted} event match(es) stand for the {threads} thread(s) they belong to", err=True)
    histories: list[Any] = []
    sessions: dict[str, Any] = {}
    for session_id, thread_id in keys:
        session = sessions.setdefault(session_id, store.get(session_id))
        if thread_id is not None:
            histories.append(session.thread(thread_id))
        elif roots:
            histories += session.root_threads
        else:
            histories.append(session)
    return histories


def _piped(store: Sessions, kind: str, *, roots: bool = False) -> list[Any]:
    """The sessions and threads piped in on stdin."""
    if sys.stdin.isatty():
        raise click.UsageError("pipe the sessions in: honed s q --json 'select:session count:50' | "
                               f"honed s g {kind}")  # fmt: skip
    histories = _histories(store, _results(store, sys.stdin.read()), roots=roots)
    if not histories:
        raise click.ClickException("No sessions selected: the query matched nothing")
    click.echo(f"graphing {len(histories)} {'thread(s)' if roots else 'session(s)/thread(s)'} ({kind})…", err=True)
    return histories


def _output(formats: tuple[str, ...], *, png: bool) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """The options every kind shares: what to print, where to export, and (for Mermaid-drawn kinds) PNGs."""

    def decorate(command: Callable[..., Any]) -> Callable[..., Any]:
        if png:
            drawn = click.option("--png", is_flag=True, help="With --export: also render PNGs (Mermaid CLI, `mmdc`).")
            command = drawn(command)
        command = click.option("--export", "export_dir", type=click.Path(file_okay=False, path_type=Path),
                               help="Write report files to this folder instead of printing; add -o to print as well."
                               )(command)  # fmt: skip
        return click.option("-o", "--output", "output_format", type=click.Choice(formats),
                            help="Print in this format (default markdown, unless --export is given).")(command)

    return decorate


@sessions.group("graph", aliases=["g", "graphs"])
def graph_group() -> None:
    """Graph the sessions a query selected: pipe `honed sessions query --json` output (or IDs) in.

    \b
    Kinds:
      workgraph       one graph per session or thread: its events, tool calls and signals
      decision-trace  one graph per session or thread: the decisions and what they were based on
      weighted        one view across all: counted activity sequences and their support
      aeg             one view across all: the Abstract Execution Graph (states, loops, outcomes, failure
                      modes, critical steps, archetypes, drift)

    \b
    The query decides what is graphed: `select:session` graphs whole sessions (aeg: their root threads),
    `select:thread` those threads (`at:root` for main threads only), and an event match the thread it occurred
    in. `honed sessions query` returns 50 results unless the query says `count:N`.

    \b
      honed s q --json 'harness:claude after:30d select:session count:200' | honed s g aeg
      honed s q --json 'shell.startswith:pytest status:failure' | honed s g aeg --export artifacts/aeg
      honed s q --json 'harness:codex select:thread' | honed s g weighted --pattern-length 3 -o json
      honed s q --json 'session:abc123 select:session' | honed s g workgraph -o mermaid
      honed s q --json 'harness:claude after:7d select:session' | honed s g decision-trace --export out
      echo 0f3c2a91 | honed s g workgraph
    """


def _per_session(kind: str) -> click.Command:
    @graph_group.command(kind, aliases=[{"workgraph": "wg", "decision-trace": "dt"}[kind]])
    @_output(("markdown", "json", "mermaid"), png=True)
    @click.pass_obj
    @_errors
    def command(index: Path, output_format: str | None, export_dir: Path | None, png: bool) -> None:
        with Sessions(index) as store:
            _session_graphs(_piped(store, kind), kind, output_format, export_dir, png=png, index=store.path)

    command.help = {
        "workgraph": "One WorkGraph per piped session or thread: its events, tool calls and signals.",
        "decision-trace": "One DecisionTrace per piped session or thread: the decisions and what they were based on.",
    }[kind] + "\n\n`-o mermaid` prints one graph, so it needs a single result; `--export` writes them all."
    return command


_per_session("workgraph")
_per_session("decision-trace")


@graph_group.command("weighted", aliases=["w"])
@_output(("markdown", "json", "mermaid"), png=True)
@click.option("--pattern-length", "pattern_lengths", type=click.IntRange(min=2), multiple=True, default=(2,),
              show_default=True, help="Sequence length to count; repeat for several.")
@click.option("--case-scope", type=click.Choice(["session", "trajectory"]), default="session", show_default=True,
              help="Count support by whole session or by trajectory (sessions only; threads are their own case).")
@click.option("--top", type=click.IntRange(min=1), default=100, show_default=True, help="Patterns to print.")
@click.pass_obj
@_errors
def weighted_command(index: Path, output_format: str | None, export_dir: Path | None, png: bool,
                     pattern_lengths: tuple[int, ...], case_scope: Literal["session", "trajectory"],
                     top: int) -> None:  # fmt: skip
    """Counted activity sequences across the piped sessions or threads, with their case support."""
    with Sessions(index) as store:
        _weighted(_piped(store, "weighted"), output_format, export_dir, png=png, pattern_lengths=pattern_lengths,
                  case_scope=case_scope, top=top, index=store.path)  # fmt: skip


@graph_group.command("aeg", aliases=["a"])
@_output(("markdown", "json", "mermaid", "sankey"), png=False)
@click.option("--abstraction", default="phase+outcome", show_default=True,
              help="phase, phase+outcome, label, or tail:K (the last K phase+outcome states).")
@click.option("--compare", default="auto", show_default=True,
              help="Abstraction to re-check patterns under (auto: phase, or phase+outcome for phase; none: skip).")
@click.option("--llm", is_flag=True, help="Label free text and name archetypes with a model (`claude -p`), cached.")
@click.option("--judge", type=click.IntRange(min=1), metavar="N", help="Have a model judge N windows around "
              "critical steps (cached; estimate printed first; model: HONED_JUDGE_MODEL).")  # fmt: skip
@click.option("--revisions", "revisions_project", type=click.Path(file_okay=False, path_type=Path),
              help="Split drift by the release of this project's evolved configuration.")
@click.option("--gold", type=click.Path(dir_okay=False, exists=True, path_type=Path),
              help="Score rules and judge against a hand-labelled gold_set.csv (from an --export).")
@click.option("--min-support", type=click.FloatRange(0, 1), default=0.03, show_default=True,
              help="Minimum share of cases for frequent subsequences.")
@click.pass_obj
@_errors
def aeg_command(index: Path, output_format: str | None, export_dir: Path | None, abstraction: str, compare: str,
                llm: bool, judge: int | None, revisions_project: Path | None,
                gold: Path | None, min_support: float) -> None:  # fmt: skip
    """The Abstract Execution Graph of the piped sessions' root threads (or of the piped threads).

    Labels every step (EXPLORE, EDIT, TEST:fail, USER_TURN:CORRECTION, …), collapses repeats, and mines the
    sequences: variants, loops and when they stop, failure and success indicators, failure modes and critical
    steps, intervention points, cost hotspots, files and models in failure loops, archetypes, and drift.
    `--export` adds tables, an event log for process-mining tools, findings, a gold set to label, and index.html.

    \b
      honed s q --json 'harness:claude select:session count:300' | honed s g aeg --judge 20 --export artifacts/aeg
      honed s q --json 'harness:claude select:session' | honed s g aeg --revisions . --gold aeg/gold_set.csv
    """
    with Sessions(index) as store:
        _aeg(store, _piped(store, "aeg", roots=True), output_format, export_dir, abstraction=abstraction,
             compare=compare, llm=llm, min_support=min_support, judge=judge,
             revisions_project=revisions_project, gold=gold)  # fmt: skip


@graph_group.command(
    "query", aliases=["q"], context_settings={"ignore_unknown_options": True, "help_option_names": ["--help"]}
)
@click.argument("words", nargs=-1)
@click.option("--limit", type=click.IntRange(min=1), help="Maximum results; overrides count: (default 50).")
@click.option("--json", "as_json", is_flag=True, help='One JSON object: {"results": [{"type": ..., "data": {...}}]}.')
@click.option("--info", is_flag=True, help="Describe the artifacts (kinds, graphs, inputs, staleness), then exit.")
@click.option("--fields", "show_fields", is_flag=True, help="List the fields and modifiers, then exit.")
@click.pass_obj
@_errors
def graph_query_command(index: Path, words: tuple[str, ...], limit: int | None, as_json: bool, info: bool,
                        show_fields: bool) -> None:  # fmt: skip
    """Query graph artifacts: the directories `honed sessions graph <kind> --export DIR` wrote.

    Leading arguments that are directories are the artifacts (a parent finds every artifact below it); the rest is
    the query, with the syntax of `honed sessions query`: `field:value` terms, AND/OR/NOT/-, parentheses, and
    `select:` (graph, node, edge, walk, occurrence, annotation, session, thread, event; default node).

    \b
    A term holds when some related element of its level satisfies it: a node's walks, a walk's nodes, a session's
    walks, an event's occurrences. Prefix a field with a kind to look only there: aeg.label:DEBUG.

    \b
      # States of the AEG, and the walks through a pattern
      honed s g q artifacts/claude 'graph:aeg label.startswith:TEST select:node'
      honed s g q artifacts/claude 'path:"EDIT -> TEST:fail ->> DONE" outcome:failed select:walk'
      honed s g q artifacts/claude 'loop:"TEST:fail DEBUG" iterations>=3 select:walk'
    \b
      # Rare transitions, findings, and sessions with a WorkGraph signal
      honed s g q artifacts/claude 'edge.probability<0.05 edge.count>=3 select:edge'
      honed s g q artifacts/claude 'finding:premature_done select:annotation'
      honed s g q artifacts/claude 'workgraph.signal:StubbornToolLoop select:session'
    \b
      # Across kinds, joined on shared events; and back into a graph
      honed s g q artifacts/claude 'aeg.label:DEBUG workgraph.signal:Stubborn select:event'
      honed s g q --json artifacts/claude 'critical:guardrail select:thread' | honed s g decision-trace
    """
    from .analysis import graphquery, graphstore
    from .query import QuerySyntaxError

    if show_fields:
        click.echo(graphquery.describe_fields())
        return
    targets: list[Path] = []
    rest = list(words)
    while rest and Path(rest[0]).expanduser().is_dir():
        targets.append(Path(rest.pop(0)))
    if not targets:
        raise click.UsageError("give the artifact directory first: honed s g q DIR 'QUERY'")
    folders = graphstore.discover(targets)
    if not folders:
        raise click.ClickException(f"No graph artifacts in {', '.join(map(str, targets))}; export one with "
                                   "`honed s g <kind> --export DIR`")  # fmt: skip
    for folder in folders:
        for note in graphstore.stale(folder, index):
            click.echo(f"warning: {folder.name}: {note}; export it again to refresh", err=True)
    if info:
        for folder in folders:
            manifest = graphstore.read_manifest(folder)
            click.echo(f"{folder}")
            for kind, entry in sorted(manifest.get("exports", {}).items()):
                inputs = entry["input"]
                click.echo(f"  {kind:<15} {entry['graphs']:>5} graph(s)  {len(inputs['sessions']):>5} session(s)  "
                           f"built {entry['built_at']}  {json.dumps(entry['parameters'])}")  # fmt: skip
        return
    text = " ".join(rest)
    if not text.strip():
        raise click.UsageError("give a query after the directories, or --info / --fields")
    try:
        parsed = graphquery.parse(text)
    except QuerySyntaxError as error:
        raise click.UsageError(str(error)) from error
    for hint in graphquery.check_labels(folders, parsed):
        click.echo(f"hint: {hint}", err=True)
    matches = graphquery.execute(folders, parsed, limit=limit)
    if not matches:
        click.echo("No matches.", err=True)
    if as_json:
        click.echo(json.dumps({"results": [match.to_dict() for match in matches]}, ensure_ascii=False, default=str))
        return
    for match in matches:
        click.echo(_graph_line(match.type, match.data))


def _graph_line(unit: str, data: dict[str, Any]) -> str:
    where = f"{data.get('artifact', '')}/{data.get('graph', '')}"
    if unit == "graph":
        return (f"{data['artifact']}/{data['id']:<30} {data['kind']:<15} {data['nodes']:>5} nodes {data['edges']:>5} "
                f"edges {data['walks']:>5} walks  {_one_line(data['title'], 40)}")  # fmt: skip
    if unit == "node":
        props = data["props"]
        numbers = " ".join(f"{k}={props[k]}" for k in ("visits", "cases", "occurrences", "avg_repeat")
                           if isinstance(props.get(k), int | float))  # fmt: skip
        return f"{where:<28} {_one_line(data['label'], 34):<34} {data['occurrences']:>5}×  {numbers}  " + \
            _one_line(data["text"], 50)  # fmt: skip
    if unit == "edge":
        props = data["props"]
        numbers = " ".join(f"{k}={props[k]}" for k in ("count", "probability", "cases", "occurrences", "success_rate")
                           if isinstance(props.get(k), int | float))  # fmt: skip
        return f"{where:<28} {_one_line(data['label'], 60):<60} {numbers}"
    if unit == "walk":
        steps = data["steps"]
        marked = [f"[{label}]" if i in data["match"] else label for i, label in enumerate(steps)]
        return (f"{data['id'][:24]:<24} {(data['session_id'] or '')[:12]:<12} {(data['outcome'] or '-'):<16} "
                f"{len(steps):>4} steps  {_one_line(' → '.join(marked), 110)}")  # fmt: skip
    if unit == "occurrence":
        return (f"{where:<28} {_one_line(data['label'], 34):<34} {(data['walk'] or '-')[:24]:<24} "
                f"#{data['step']}  {(data['at'] or '')[:19]}  {len(data['event_ids'])} event(s)")  # fmt: skip
    if unit == "annotation":
        return (f"{data['kind']:<9} {data['label']:<24} {(data['walk'] or '-')[:24]:<24} #{data['step']}  "
                f"{_one_line(data['text'], 90)}")  # fmt: skip
    if unit == "session":
        return (f"{data['id'][:12]}  {(data['harness'] or ''):<11} {(data['started_at'] or '')[:19]:<19}  "
                f"{data['walks']:>3} walk(s)  {_one_line(data['title'] or data['cwd'], 60)}")  # fmt: skip
    if unit == "thread":
        return f"{data['id'][:12]}  {(data['session_id'] or '')[:12]}  {data['walks']:>3} walk(s)"
    return f"{data['id'][:12]}  {(data['session_id'] or '')[:12]}  {(data['run_id'] or '')[:12]}"


def _wrote(summary: str, output: Path, output_format: str | None) -> None:
    # With a printed format, stdout carries only that format; the report's location goes to stderr.
    err = output_format is not None
    click.echo(summary, err=err)
    click.echo(f"Wrote report files to {output}; open {output / 'index.html'}", err=err)
    click.echo(f"Query the graphs: honed sessions graph query {output} '…'", err=err)


def _session_graphs(histories: list[Any], method: str, output_format: str | None, export_dir: Path | None, *,
                    png: bool, index: Path | None = None) -> None:  # fmt: skip
    from .analysis import graphstore
    from .analysis.export import session_graph

    if png and export_dir is None:
        raise click.UsageError("--png needs --export DIR")
    if output_format == "mermaid" and len(histories) != 1:
        raise click.UsageError(f"-o mermaid prints one graph; the query selected {len(histories)} (use --export)")
    graphs = [(history, session_graph(history, method)) for history in histories]
    if export_dir is not None:
        try:
            records = export_workgraphs(None, export_dir / method, histories=histories,
                                        graphs=[graph for _, graph in graphs], png=png, representation=method)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        graphstore.save(export_dir, method, [graphstore.from_session_graph(method, h, g) for h, g in graphs],
                        histories=histories, parameters={}, index=index)  # fmt: skip
        _wrote(f"Wrote {len(records)} {method} graph(s).", export_dir.expanduser().resolve(), output_format)
        if output_format is None:
            return
    if output_format == "json":
        click.echo(json.dumps({"graphs": [{
            "session_id": getattr(history, "session_id", None) or history.id,
            "thread_id": None if isinstance(history, Session) else history.id,
            "title": history.metadata.title, "graph": graph.to_dict()} for history, graph in graphs]},
            ensure_ascii=False, indent=2, default=str))  # fmt: skip
    elif output_format == "mermaid":
        click.echo(graphs[0][1].to_mermaid())
    else:
        for history, graph in graphs:
            title = history.metadata.title or history.metadata.native_id or history.id
            click.echo(f"# {title}\n\n{graph.to_markdown()}\n")


def _weighted(histories: list[Any], output_format: str | None, export_dir: Path | None, *, png: bool,
              pattern_lengths: tuple[int, ...], case_scope: Literal["session", "trajectory"],
              top: int, index: Path | None = None) -> None:  # fmt: skip
    if png and export_dir is None:
        raise click.UsageError("--png needs --export DIR")
    if all(isinstance(history, Session) for history in histories):
        view = WeightedPatternView.from_sessions(histories, case_scope=case_scope, pattern_lengths=pattern_lengths)
    else:
        threads = [t for history in histories for t in (history.threads if isinstance(history, Session) else [history])]
        view = WeightedPatternView.from_threads(threads, pattern_lengths=pattern_lengths)
    summary = (f"Aggregated {view.case_count} {view.case_scope} case(s) into {len(view.patterns)} pattern(s) "
               f"of lengths {view.pattern_lengths}.")  # fmt: skip
    if export_dir is not None:
        from .analysis import graphstore

        try:
            export_weighted_pattern_view(view, export_dir / "weighted", png=png)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        graphstore.save(export_dir, "weighted", graphstore.from_weighted(view, histories), histories=histories,
                        parameters={"pattern_lengths": list(pattern_lengths), "case_scope": view.case_scope},
                        index=index)  # fmt: skip
        _wrote(summary, export_dir.expanduser().resolve(), output_format)
        if output_format is None:
            return
    if output_format == "json":
        data = view.to_dict()
        # Ranked as the Markdown table is: by case support, then occurrences.
        ranked = sorted(data["patterns"], key=lambda p: (-p["cases"], -p["occurrences"], len(p["activities"]),
                                                         p["activities"]))  # fmt: skip
        data["patterns"] = ranked[:top]
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    elif output_format == "mermaid":
        click.echo(view.to_mermaid(limit=top))
    else:
        click.echo(view.to_markdown(limit=top))


def _aeg(store: Sessions, threads: list[Any], output_format: str | None, export_dir: Path | None, *,
         abstraction: str, compare: str, llm: bool, min_support: float, judge: int | None,
         revisions_project: Path | None, gold: Path | None) -> None:  # fmt: skip
    from .analysis.aeg import ExecutionGraphView, export_execution_graph
    from .analysis.aeg.judge import FailureJudge
    from .analysis.aeg.labels import LLMLabeler

    labeler = LLMLabeler(cache=store.path.with_suffix(".aeg-labels.json")) if llm else None
    failure_judge = None
    if judge is not None:
        model = os.environ.get("HONED_JUDGE_MODEL") or os.environ.get("THEARC_JUDGE_MODEL") or "claude-sonnet-5-5"

        def announce(cost: dict[str, int]) -> None:
            click.echo(f"judging {cost['windows']} window(s) with {model}: about {cost['total_tokens']:,} tokens "
                       f"({cost['cached']} cached, free)", err=True)  # fmt: skip

        failure_judge = FailureJudge(cache=store.path.with_suffix(".aeg-judge.json"), model=model,
                                     on_estimate=announce)  # fmt: skip
    revision_of = None
    if revisions_project is not None:
        from honed.evolve.round import release_at, releases

        try:
            timeline = releases(revisions_project)
        except Exception as error:  # Not an evolved project, or no git: say so.
            raise click.ClickException(f"--revisions {revisions_project}: no releases found ({error})") from error
        revision_of = lambda case: release_at(timeline, case.started_at)
    view = ExecutionGraphView.build(
        threads,
        abstraction=abstraction,
        compare=None if compare == "none" else compare,
        labeler=labeler,
        name_clusters=llm,
        min_support=min_support,
        judge=failure_judge,
        judge_limit=judge or 0,
        revision_of=revision_of,
        gold=gold,
    )
    summary = view.summary()
    line = (f"{summary['cases']} case(s) from {summary['sessions']} session(s): {summary['states']} states, "
            f"{summary['edges']} transitions; outcomes {summary['outcomes']}.")  # fmt: skip
    if export_dir is not None:
        from .analysis import graphstore

        export_execution_graph(view, export_dir / "aeg")
        graphstore.save(export_dir, "aeg", graphstore.from_aeg(view), histories=threads,
                        parameters={"abstraction": abstraction, "compare": compare, "llm": llm, "judge": judge,
                                    "min_support": min_support}, index=store.path)  # fmt: skip
        _wrote(line, export_dir.expanduser().resolve(), output_format)
        if output_format is None:
            return
    if output_format == "json":
        click.echo(json.dumps(view.to_dict(), ensure_ascii=False, indent=2, default=str))
    elif output_format == "mermaid":
        click.echo(view.to_mermaid())
    elif output_format == "sankey":
        click.echo(view.to_sankey())
    else:
        click.echo(view.to_markdown())


@sessions.command("install", aliases=["i"])
@click.argument("references", nargs=-1)
@click.option("--to", "target", required=True, type=click.Choice(INSTALLABLE),
              help="Harness that should be able to resume the session.")  # fmt: skip
@click.option("--trajectory", type=click.IntRange(min=0), help="Only this trajectory (single session).")
@click.option("--through", is_flag=True, help="The session up to and including --trajectory.")
@click.option("--home", type=click.Path(file_okay=False, path_type=Path),
              help="Harness home to install into (default: the harness's own, e.g. ~/.codex).")  # fmt: skip
@click.option("--overwrite", is_flag=True, help="Replace a session already installed under the same ID.")
@click.option("--keep-id", is_flag=True, help="Use the source session's ID instead of a copy ID.")
@click.option("--best-effort", is_flag=True, help="Keep events the target cannot represent as labelled text.")
@click.pass_obj
@_errors
def install_command(index: Path, references: tuple[str, ...], target: str, trajectory: int | None, through: bool,
                    home: Path | None, overwrite: bool, keep_id: bool, best_effort: bool) -> None:  # fmt: skip
    """Install sessions or threads where TARGET finds them, as copies it can resume or fork.

    REFERENCES are session IDs, native IDs, run IDs or unique prefixes. Without them, pipe the results of
    `honed sessions query --json` (or IDs, one per line) in: sessions install whole, threads as themselves.

    \b
      honed sessions install 0f3c2a91 --to codex
      honed sessions install 0f3c2a91 --to claude --trajectory 2 --through
      honed sessions query --json 'harness:claude after:7d select:session' | honed sessions install --to codex
    """
    if through and trajectory is None:
        raise click.UsageError("--through requires --trajectory")
    options: dict[str, Any] = {"home": home, "overwrite": overwrite, "strict": not best_effort, "keep_id": keep_id}
    with Sessions(index) as store:
        if references:
            histories: list[Any] = [store.get(reference) for reference in references]
        elif sys.stdin.isatty():
            raise click.UsageError("give session references, or pipe `honed sessions query --json` results in")
        else:
            histories = _histories(store, _results(store, sys.stdin.read()), roots=False)
            if not histories:
                raise click.ClickException("No sessions selected: the query matched nothing")
        if trajectory is not None:
            if len(histories) != 1 or not isinstance(histories[0], Session):
                raise click.UsageError("--trajectory and --through select from a single session")
            session = histories[0]
            if trajectory >= len(session.trajectories):
                count = len(session.trajectories)
                raise click.ClickException(f"Session has {count} trajectories; use 0..{count - 1}")
            chosen = session.trajectories[trajectory]
            histories = [session.through(chosen) if through else chosen]
            records = [session.metadata]
        else:
            records = [history.metadata for history in histories]
        results = [(record, _try(functools.partial(history.install, target, **options)))
                   for record, history in zip(records, histories, strict=True)]  # fmt: skip
    failed = 0
    for record, outcome in results:
        if isinstance(outcome, Exception):
            failed += 1
            hint = " (use --best-effort)" if isinstance(outcome, UnsupportedConversion) else ""
            click.echo(f"error: {record.native_id}: {outcome}{hint}", err=True)
            continue
        state = "installed" if outcome.imported else "written; import it with the command below"
        click.echo(f"{record.native_id} -> {target} {outcome.session_id} {state}")
        click.echo(f"  {outcome.path}\n  {outcome.command}")
        for note in outcome.transformations if best_effort else ():
            click.echo(f"note: {note}", err=True)
    if failed:
        raise click.ClickException(f"{failed} of {len(results)} session(s) not installed")


def _try(install: Callable[[], InstalledSession]) -> InstalledSession | Exception:
    try:
        return install()
    except (ValueError, OSError) as exc:
        return exc
