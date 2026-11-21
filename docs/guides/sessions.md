# Index and select sessions

Sessions are read from each harness's transcripts into a local SQLite index.

```sh
honed sessions --index sessions.sqlite sync logs/codex-training     # register the folder, read it
honed sessions --index sessions.sqlite sync                          # later: read what changed
honed sessions --index sessions.sqlite query 'harness:codex select:session'
honed sessions --index sessions.sqlite trajectories <session>
honed sessions --index sessions.sqlite render <session> --trajectory 0
```

The same from Python:

```python
from honed.sessions import Sessions

with Sessions("sessions.sqlite") as sessions:
    source = sessions.add("logs/claude-training")            # detects the harness, synchronizes
    records = sessions.list(source=[source.id])               # newest first
    session = sessions.get(records[0].id)                     # any ID, native ID, or unique prefix
```

## Trajectories

A **trajectory** is one user prompt and the subsequent recorded work in a single thread. The split
is made independently for each event stream (`source_id`, `session_id`, `run_id`, and transcript
segment); events from a parent and child thread are never stitched into one trajectory. Events before
the first prompt remain unassigned. `session.trajectories` exposes the split, and
`session.through(trajectory)` selects a session prefix only when the session has one event stream; it
raises an ambiguity error when multiple run or transcript streams are present.

## Finding sessions

```python
from honed.sessions.scanner import ShellCommand, SkillUsage

hits = sessions.search("SyntaxError", kind="tool_result", status="failure")
used = {m.session_id for m in sessions.match(ShellCommand(contains="graphify query"))}
records = [r for r in sessions.list() if r.id in used]
```

`match` runs behavioural patterns over complete event sequences (loops, abandoned fixes, ignored
instructions, shell commands, skill usage). A match is a signal to inspect, not proof of a cause.

## Thread families

Honed uses these terms for different boundaries:

| Honed term | Definition | Boundary and caveat |
|---|---|---|
| **Session** | An indexed transcript container. Its `session_id` is taken from a harness-native conversation ID or, when unavailable, an adapter fallback. | One session can contain multiple threads (Claude Code). A delegated thread can instead be a separate session (Codex and OpenCode). “Session” therefore does not mean the same native object in every harness. |
| **Thread** | One independently ordered execution stream. | Currently represented by a normalized `run_id`; all events with that ID belong to the stream. Honed’s thread is a common abstraction and may map to a native agent, thread, or session. |
| **Trajectory** | One user prompt and its recorded response work within one thread. | Prompt splitting is done per stream. It does not join parent and child activity, even if they overlap in time. |
| **Parent / child** | A directed relationship between two threads: the child records the parent thread ID. | These are direct links, not a stored root-to-leaf path. Links may cross sessions. A link identifies lineage, but does not necessarily identify the exact tool call that spawned the child. |
| **Family** | The selected thread’s highest available linked parent plus its known descendants. | Family membership follows indexed parent links. The root is derived from those links; ancestors are not copied onto every thread. A missing parent record can make the family incomplete. |

`parent_id` and `child_ids` are direct thread IDs. `None` means that no parent link was recorded for
that thread; it does not prove that the harness had no parent. A thread with a parent ID whose parent
is absent from the index is not a `root_thread`. A **family root** is the family member whose parent
is outside the included family; when an ancestor is missing from the index, this boundary
thread can still have a non-null `parent_id`. `root_thread` means a thread with no recorded parent
link, not necessarily the harness’s designated “main agent.”

### Mapping harness records to Honed

The adapters map transcript identities and links into Honed’s `session_id`, `run_id`, and
`parent_run_id`. The table describes current adapter behavior, not a claim that the harnesses use the
same underlying concept.

| Harness | Native record used for Honed session | Native record mapped to Honed thread | Parent/child mapping and current limits |
|---|---|---|---|
| **Claude Code** | `sessionId`; child-agent files use the enclosing session directory. | Main transcript and each `subagents/agent-*.jsonl` file become separate threads in that session. | The adapter links each child-agent thread to the main thread based on file layout. It does not retain nested child-to-child parentage, so nested agents appear as children of the main thread. This link is adapter-inferred from layout. |
| **Codex** | `session_meta.payload.id` (or `session_id`). | Each native thread ID becomes a Honed session with its own main thread. | `source.subagent.thread_spawn.parent_thread_id` becomes the child thread’s parent link. The family crosses Honed session boundaries. Both parent and child records must be indexed to inspect both streams. |
| **OpenCode** | Exported `info.id`. | Each exported session becomes a Honed session with a main thread. | Exported `info.parentID` (also accepted as `parentId`) becomes the parent link. Parent and child are separate Honed sessions; both exports must be indexed for a complete family. |
| **Pi** | The JSONL session header’s `id`. | The current adapter maps the session to one main thread. | Per-record `parentId` is retained as event-level native metadata; it is not converted into thread parentage. Pi history branching is therefore not represented as agent delegation in the current thread family. Extension-created workers have no standardized link in this adapter. |
| **Antigravity** | `conversation_id`, falling back to an ID derived from the transcript path. | The current adapter maps the transcript to one main thread. | The adapter does not currently extract a parent-thread link for delegated work. Tool steps remain events in the conversation thread; separate, unlinked transcripts cannot be safely attached to a family. |

Honed’s normalized `run_id` is generated from the adapter’s session identity and agent identity; it is
not necessarily a native ID. `parent_run_id` is projected to `Thread.parent_id`, and reverse links
produce `child_ids`. Claude’s parent link is inferred from file layout; Codex and OpenCode adapters
use explicit parent IDs from their records. Pi branching metadata and Antigravity relationships are
not converted into thread links by the current adapters. Treat other lineage as unknown until an
adapter can extract it reliably.

```python
from honed.sessions import DecisionTrace, Sessions, WeightedPatternView, WorkGraph
from honed.sessions.scanner import ShellCommand

with Sessions("sessions.sqlite") as sessions:
    session = sessions.get("<session-id>")
    session.threads            # Threads in this transcript container
    session.root_threads       # Threads with no recorded parent link
    session.root_thread        # Convenience when there is exactly one root
    thread = session.thread("<run-id>")

    # Parent and child links are direct IDs; they can cross session boundaries.
    parent = sessions.thread(thread.parent_id) if thread.parent_id else None
    children = [sessions.thread(child_id) for child_id in thread.child_ids]

    family = sessions.family("<session-id-or-run-id>")
    matches = family.match(ShellCommand(contains="pytest"))
    patterns = WeightedPatternView(family.threads, case_scope="thread", pattern_lengths=(2, 3))
    graph = WorkGraph(thread)
    trace = DecisionTrace(thread)
```

`Sessions.threads()` lists all indexed threads, including threads whose parent is in another session;
`Sessions.root_threads()` selects those without a recorded parent. `Sessions.thread(reference)` resolves
a run ID. `sessions.family(reference, exclude_root=False)` follows parent links to build the known
family and includes its derived root by default; `exclude_root=True` omits that thread. From the command
line, `honed sessions query 'family:<reference> select:thread'` lists a family (add `at:child` to leave its
root out). Analyses receive a selected thread or an explicit thread collection; they do not merge parent and
child event streams into a single timeline.
`Session`, `Thread`, and `Trajectory` implement `EventSequence`, so graph constructors accept any of
them. A graph from a multi-stream Session keeps components and signal detection within each stream;
it omits the global conversation overview because those streams have no single total order.

## Graphs of what a query selected

`honed sessions graph <kind>` (short: `honed s g <kind>`) reads the results of
`honed sessions query --json` (or session and run IDs, one per line) from stdin and graphs them:

- `workgraph`: one graph per session or thread: its events, tool calls and signals.
- `decision-trace`: one graph per session or thread: its decisions and what they were based on.
- `weighted`: one view across all: counted activity sequences and their support.
- `aeg`: one view across all: the Abstract Execution Graph (states, loops, outcomes, failure modes,
  critical steps, archetypes and drift).

The query decides what is graphed. `select:session` graphs whole sessions (for `aeg`, their root threads),
`select:thread` graphs those threads (add `at:root` for main threads only), and an event match stands for the
thread it occurred in. `honed sessions query` returns 50 results unless the query says `count:N`.

```sh
honed s q --json 'harness:codex select:session count:50' | honed s g weighted
honed s q --json 'harness:codex select:session' | honed s g weighted --pattern-length 2 --pattern-length 3
honed s q --json 'session:abc123 select:session' | honed s g workgraph -o mermaid
honed s q --json 'shell.startswith:pytest status:failure' | honed s g aeg --export artifacts/aeg
```

Every kind prints Markdown by default, or `-o json|mermaid` (and `sankey` for `aeg`); for the per-session
kinds `-o mermaid` prints one graph, so it needs a single result. `--export DIR` writes report files (to
`DIR/<kind>/`) instead; add `-o` to print as well. `--png` (not for `aeg`) also renders PNGs when Mermaid CLI
(`mmdc`) is installed. Each kind has its own options: see `honed s g <kind> --help`.

Weighted patterns report each pattern's occurrence count and number of distinct cases, with source event IDs
for tracing examples. Windows are counted only within one recorded run stream; unrelated runs are never
joined, and an observed sequence does not establish causation. The default case is a whole session;
`--case-scope trajectory` uses each prompt trajectory. Case support is descriptive, not a significance test.
Use example event IDs with `honed sessions show` to inspect source records.

The AEG adds `--abstraction`, `--compare`, `--llm`,
`--judge N` (model: `HONED_JUDGE_MODEL`), `--revisions PROJECT` (drift by release) and `--gold FILE`.

## Querying graph artifacts

An export directory is an artifact: next to each kind's reports in `DIR/<kind>/` it holds `graphs.sqlite`, every
graph exported into it in one model, and `manifest.json`, what each kind was built from and how. Export several
kinds into one directory and query them together; exporting a kind again replaces only that kind.

The model: **graphs**; their **nodes** and **edges**, each with a kind, a label (an AEG state, a weighted
activity, a WorkGraph event kind) and properties; **walks**, one case's ordered path through the nodes;
**occurrences**, where a node or edge happened (walk, step, time, event IDs); and **annotations**, the AEG's
findings and critical steps and the WorkGraph's signals.

`honed sessions graph query DIR… 'QUERY'` (short: `honed s g q`) uses the syntax of session queries.
Leading directories are the artifacts (a parent finds every artifact below it); `select:` returns graphs,
nodes (default), edges, walks, occurrences, annotations, sessions, threads or events.

```sh
honed s q --json 'harness:claude after:30d select:session' | honed s g aeg --export artifacts/claude
honed s q --json 'harness:claude after:30d select:session' | honed s g workgraph --export artifacts/claude
honed s g q artifacts/claude --info
honed s g q artifacts/claude 'graph:aeg label.startswith:TEST select:node'
honed s g q artifacts/claude 'path:"EDIT -> TEST:fail ->> DONE" outcome:failed select:walk'
honed s g q artifacts/claude 'edge.probability<0.05 edge.count>=3 select:edge'
honed s g q artifacts/claude 'aeg.finding:premature_done workgraph.signal:Stubborn select:session'
honed s g q --json artifacts/claude 'critical:guardrail select:thread' | honed s g decision-trace
```

A term holds when some element of its level related to the result satisfies it: a node's walks, a walk's nodes
and annotations, a session's walks, an event's occurrences. So `label:EDIT label:TEST:fail select:walk` finds
walks with both, and a negated term needs none. A kind prefix (`aeg.label:`, `workgraph.signal:`) looks only in
graphs of that kind; with `select:event`, `thread` or `session` that joins kinds on shared events. Path patterns
use `->` (next) and `->>` (later), `*`, `A|B`, `!A`, `PREFIX*`, `^` and `$`; `loop:"A B"` with `iterations>=N`
finds repeated cycles; `reaches:"A ~> B"` follows edges. Numbers compare properties: `count>20`,
`edge.probability<0.1`, `walk.steps>=40`. `--fields` lists everything. Queries warn when sessions an artifact
was built from have changed in the index since.

## Export and install

`session.export("codex")` converts a session to another harness's transcript format;
`session.install("claude")` writes a copy that harness can resume or fork. Reflection's handoff
uses this to start a reflector from a session's history.
