# Runtime support

Reflectors, curators, and mergers run in the harnesses you already use. thearc connects its
tools (`submit_reflection`, or `commit_change` and `finish`) to each run as an MCP server served
from your own process, and turns off your own customizations — hooks, skills, context files —
where the harness allows it.

| Harness | Reflect | Hand off a session | Curate / merge | How |
| --- | --- | --- | --- | --- |
| Codex | yes | yes | yes | `codex exec --json` (`exec fork` to hand off, `exec resume` for later turns); read-only sandbox to reflect, `workspace-write` to edit |
| Claude Code | yes | yes | yes | `claude -p`; Read/Grep/Glob, plus Edit/Write to edit |
| Pi | yes | yes | yes | `pi -p --mode json`; read/grep/find/ls, plus edit/write; tools through a run-only extension |
| OpenCode | yes | yes | yes | `opencode run --format json`; isolated run config, restricted permissions, per-run MCP tools |
| Antigravity | no | no | no | reads MCP servers only from your own configuration, so thearc cannot connect its tools for one run |

Sessions can be indexed from Claude Code, Codex, Pi, OpenCode, and Antigravity, and exported to
any of their formats; `--best-effort` keeps what a format cannot represent as labelled text.

Notes:

- Agents never run commands while curating or merging; every commit goes through
  `commit_change`.
- Handed-off sessions are installed as copies in the harness's home (or `home=`), redacted (see
  [redaction](../guides/reflections.md#redaction)); the original transcript is never changed.
- `timeout=` bounds each turn: the subprocess is killed for Claude Code and Pi, and for Codex the turn is abandoned
  and its client closed. Either way the run fails with a `timeout` error.
- Each run's token usage comes from the harness: Claude Code's JSON result (`usage`, `total_cost_usd`), Pi's
  assistant messages (`usage`, one per model call), Codex's thread token usage (its running total, so each turn
  counts the difference), and OpenCode's JSON `step_finish` events (`tokens`, `cost`). See
  [token usage](../guides/report.md#token-usage).
