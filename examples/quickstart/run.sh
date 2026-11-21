#!/usr/bin/env bash
# The quickstart: a toy project, three Claude Code sessions in it, one /evolve round, and its report.
#
#   examples/quickstart/run.sh [FOLDER]      # default: a new temporary folder
#
# Needs an authenticated Claude Code (`claude`) and git. honed is installed from this checkout with pip when it is
# not on PATH. It makes real model calls: three short coding sessions, three
# reflections and one curation, a few minutes in all. Set MODEL to run them with another model.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROJECT="${1:-$(mktemp -d)/shelf}"

# 0. What it needs: Claude Code, signed in; and honed, installed from this checkout if it is missing.
command -v claude >/dev/null || { echo "Claude Code (claude) is required: https://docs.claude.com/claude-code" >&2; exit 1; }
if ! command -v honed >/dev/null; then
  python3 -m pip install --quiet -e "$REPO" || {
    echo "Could not install honed with pip; activate a virtual environment (python3 -m venv .venv && . .venv/bin/activate) and run this again" >&2
    exit 1
  }
  hash -r
fi

# 1. The project, and one command to set it up: `honed setup` makes it a git repository, adopts its Claude
#    configuration (CLAUDE.md, which names a test runner that isn't there, and one skill) as a versioned AgentConfig,
#    installs the /evolve and /reflect skills beside it, and commits what it installed.
mkdir -p "$PROJECT" && cp -R "$HERE/project/." "$PROJECT"
echo "Project: $PROJECT"
honed setup "$PROJECT" --harness claude --local  # A throwaway project: its workspace needs no remote.
cd "$PROJECT"
git add -A && git commit -qm "shelf"  # The rest of the toy project.

# 2. Work in it: three Claude Code sessions, one per task. Edits are accepted; shell commands other than Python
#    are refused, as a cautious user would.
for task in "$HERE"/tasks/*.md; do
  echo "Session: $(basename "$task")"
  claude -p ${MODEL:+--model "$MODEL"} --permission-mode acceptEdits --allowedTools "Bash(python3:*)" \
    < "$task" > /dev/null || echo "  (the session ended with an error; going on)"
done

# 3. A round: reflect on each session against CLAUDE.md and the skill, then curate a new version. It prints the
#    proposal and the path of its report: every section, the ratings it got, and what changed.
honed evolve
honed evolve diff
echo "Install the new version with: (cd $PROJECT && honed evolve apply)"
