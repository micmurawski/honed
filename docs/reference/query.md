# Query language

`honed sessions query` (short: `hnd s q`) finds events, threads, sessions or reflections in the
[session index](../guides/sessions.md) with a Sourcegraph-style query:

```sh
hnd s q 'shell:graphify harness:claude at:root select:thread'
```

A query is a list of **terms**. Bare words match conversation text, `field:value` **filters** narrow the
results, and **modifiers** such as `select:` and `patterntype:` configure the whole query. Pass the query as one
quoted argument, or as several words that are joined with spaces. Quote it when it contains shell characters
such as `( ) * | " $`.

`hnd s q --help` prints a summary with examples. `hnd s q --fields`
lists every field and modifier.

## Terms and operators

| Write | Means |
| --- | --- |
| `def fun(` | both `def` and `fun(`: `AND` is implied between terms |
| `"def fun("` | the exact phrase |
| `/def\s+\w+\(/` | a regular expression (in the default keyword mode) |
| `a AND b`, `a and b` | both |
| `a OR b`, `a or b` | either one. `AND` binds tighter, so `a b OR c` is `(a AND b) OR c` |
| `NOT a`, `not a` | without `a` |
| `-harness:pi` | negates a filter. A leading `-` on a bare word is text: `--force` searches for `--force` |
| `( … )` | groups terms. A parenthesis that does not pair up is text: `foo(` and `bar)` are searched as written |

To search for `and`, `or` or `not` themselves, quote them: `'"and"'`.

## Pattern types

`patterntype:` decides how bare patterns and text filters match.

| Type | Matching |
| --- | --- |
| `keyword` (default) | Each bare word is a literal substring, and all of them are required. `"quotes"` make an exact phrase and `/…/` a regular expression. |
| `regexp` (or `regex`) | Every pattern is a regular expression. Quotes only group text that contains spaces. |
| `lexical` | Full-text search over the word index, best matches first (BM25). Bare words are whole words, quotes make a phrase of words, and case and punctuation are ignored. |

```sh
hnd s q 'connection refused'                       # both words, anywhere in an event
hnd s q '"connection refused"'                     # the phrase
hnd s q '/timed? ?out/'                            # a regex inside a keyword query
hnd s q 'patterntype:regexp shell:"^git\s+(diff|log)"'
hnd s q 'patterntype:lexical retry timeout'        # ranked by relevance
```

## Filters

Write a filter as `field:value`. Add an operator, `field.OPERATOR:value`, to choose the comparison:

| Operator | Compares |
| --- | --- |
| `contains` | default for text fields; follows the pattern type |
| `is` | the whole value, exactly; default for attribute fields |
| `startswith` | a literal prefix, ignoring leading whitespace |
| `matches` | always a regular expression |

Text filters ignore case unless the query has `case:yes`.

### Event fields

| Field | Matches |
| --- | --- |
| `content` | conversation text; bare patterns use this field |
| `message` | message text, any role |
| `user`, `assistant` | user or assistant messages |
| `thinking` | reasoning text |
| `call` | tool call arguments (JSON) |
| `result` | tool result output |
| `shell` | the shell command itself, not its JSON, also inside `bash -lc` and Codex `exec` scripts |
| `file` | file tool calls (read, write, edit) |
| `kind` | `message`, `tool_call`, `tool_result`, `thinking`, … |
| `role` | `user`, `assistant`, `tool`, `system`, `developer` |
| `tool` | the tool name as the harness records it |
| `action` | the normalized action: `action:shell` is the same as `shell.*` |
| `status` | `success` or `failure`. A tool call takes the status of its result |
| `tag` | event tags: `tag:reflection` marks the turns of an in-session reflection |

### Session and thread fields

| Field | Matches |
| --- | --- |
| `harness` | `claude`, `codex`, `opencode`, `pi`, … |
| `source` | a registered source ID |
| `session` | a session ID, native ID or ID prefix |
| `title` | the session title |
| `cwd` | the working folder and its subfolders; `~` expands to your home folder |
| `after`, `before` | when the session started: an ISO date (`2026-10-01`) or a time ago (`12h`, `3d`, `2w`) |
| `reflected` | `yes` or `no`: whether the session has a reflection |
| `thread` | a thread (run) ID or ID prefix |
| `at` | `root` for the main agent, `child` for a delegated subagent |
| `family` | a thread family: its root and every thread delegated below it, across sessions. Give any session ID, native ID or run ID (or a prefix) in it |

### Reflection and rating fields

| Field | Matches |
| --- | --- |
| `reflection` | a reflection ID or ID prefix |
| `bound` | `yes` or `no`: whether the reflection is joined to its session (`no` means that session is not indexed yet) |
| `prompt` | what the reflection was asked: `reflect`, `reflect@1`, or a hash `p_…` |
| `asked` | the question's fingerprint (prompt and schema), or a prefix of it |
| `schema` | the schema hash `s_…`, or a prefix of it |
| `revision` | the configuration judged: `1.4.0` or `1.4.0@rev_…` |
| `mode` | `in-session`, `handoff` or `attached` |
| `trigger` | why the reflection was made at that point |
| `reflector`, `model` | the harness and the model that reflected |
| `coverage` | `none`, `partial` or `full`: how far reflections on that question reach |
| `rated` | a rating: `helpful`, `harmful` or `neutral` |
| `target` | what a rating cites: `skill:NAME`, `context:FILE` or a file path |
| `failure` | a rated failure mode: `premature_done`, `no_verification`, … |

Reflection and rating filters written side by side apply to the same reflection, and to the same rating of it.
`prompt:reflect@1 revision:1.4.0` is one reflection that has both, and `NOT ( … )` means no reflection has all of
them.

## Modifiers

Modifiers configure the whole query and may appear anywhere at its top level.

| Modifier | Values |
| --- | --- |
| `select` | `event` (default), `thread`, `session`, `reflection`: what each result is |
| `patterntype` | `keyword` (default), `regexp`, `lexical` |
| `case` | `no` (default), `yes` |
| `count` | the maximum number of results, 50 by default. `--limit` overrides it |
| `internal` | `no` (default). With `yes`, text also searches system and developer prompts, metadata, and the turns of in-session reflections |

### What `select` changes

- `select:event`: every event-level term must hold for the **same** event.
- `select:thread` and `select:session`: each event-level term needs **some** event in that thread or session,
  and a negated term needs **none**. For example, `shell:pytest -shell:--lf select:thread` finds threads that
  ran pytest but never ran it with `--lf`.
- `select:reflection`: every reflection is a result, including one whose session is not indexed yet
  (`bound:no`). Such a reflection has no session, so session terms never hold for it.

## Output

The default output is one line per result. `--json` prints one object, `{"results": [{"type": …, "data": {…}}]}`.
Event results also carry `match_ranges`, the spans of their text that the query's patterns matched. Use
`--limit` and `--offset` to page through results.

`--json` output can be piped into `hnd s g` to [graph what a query selected](../guides/sessions.md#graphs-of-what-a-query-selected).
`hnd s g q` queries graph artifacts with the same syntax and adds graph fields; see
[Querying graph artifacts](../guides/sessions.md#querying-graph-artifacts).

## Examples

### Text

```sh
hnd s q 'graphify and pytest'                      # events containing both
hnd s q 'graphify OR pytest count:10'              # either one, at most 10
hnd s q '"connection refused" -harness:pi select:session'
hnd s q 'user:refactor harness:claude cwd:~/GITHUB/honed'
hnd s q 'case:yes TODO'                            # TODO only in capitals
```

### Shell commands and tools

```sh
hnd s q 'shell:graphify at:root select:thread'      # root threads that ran graphify
hnd s q --json 'shell.startswith:pytest status:failure after:7d'
hnd s q 'shell:pytest -shell:--lf select:thread'    # pytest, never with --lf
hnd s q 'file:README.md kind:tool_call'             # file tool calls on a README
hnd s q 'tool:Bash status:failure after:2026-11-01'
```

### Threads and sessions

```sh
hnd s q 'harness:codex select:session'
hnd s q 'after:3d before:1d select:session'         # sessions started between 3 and 1 days ago
hnd s q 'family:abc123 select:thread'               # a thread family across sessions
hnd s q 'family:abc123 action:agent'                # what that family delegated
hnd s q 'session:abc123 at:child select:thread'     # the subagents of one session
```

### Reflections and ratings

```sh
hnd s q 'select:session -reflected:yes'             # sessions never reflected on
hnd s q 'select:session -prompt:reflect'            # not reflected on with the reflect prompt
hnd s q 'harness:claude after:7d select:session NOT (prompt:reflect@1 revision:1.4.0 coverage:full)'
hnd s q 'session:abc123 select:reflection'
hnd s q 'select:reflection mode:in-session rated:harmful target:skill:deploy'
hnd s q 'select:reflection bound:no'                # reflections on sessions not indexed yet
```

### From Python

```python
from honed.sessions import Sessions

with Sessions("sessions.sqlite") as sessions:
    for match in sessions.query("shell.startswith:pytest status:failure select:thread"):
        print(match.thread.id, match.session.native_id)
```

A query that cannot be parsed raises `QuerySyntaxError`. Its message marks the position of the problem.
