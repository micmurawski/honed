#!/usr/bin/env bash
# The quickstart: a toy project, three Claude Code sessions in it, one /evolve round, and its report.
#
#   examples/quickstart/run.sh [FOLDER]      # default: a new temporary folder
#
# Needs thearc and an authenticated Claude Code (`claude`). It makes real model calls: three short coding sessions,
# three reflections and one curation, a few minutes in all. Set MODEL to run them with another model.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="${1:-$(mktemp -d)/shelf}"

# 1. The project: a git repository with a CLAUDE.md and one skill. CLAUDE.md names a test runner that isn't there.
mkdir -p "$PROJECT" && cp -R "$HERE/project/." "$PROJECT"
cd "$PROJECT"
git init -q && git add -A && git commit -qm "shelf"
echo "Project: $PROJECT"

# 2. Its configuration becomes a versioned AgentConfig, with the /evolve skill installed beside it.
thearc skills install self-evolve --project . --harness claude
git add -A && git commit -qm "Evolve with thearc"

# 3. Work in it: three Claude Code sessions, one per task. Edits are accepted; shell commands other than Python
# are refused, as a cautious user would.
for task in "$HERE"/tasks/*.md; do
  echo "Session: $(basename "$task")"
  claude -p ${MODEL:+--model "$MODEL"} --permission-mode acceptEdits --allowedTools "Bash(python3:*)" \
    < "$task" > /dev/null || echo "  (the session ended with an error; going on)"
done

# 4. A round: reflect on each session against CLAUDE.md and the skill, then curate a new version. It prints the
# proposal and the path of its report: every section, the ratings it got, and what changed.
thearc evolve
thearc evolve diff
echo "Install the new version with: (cd $PROJECT && thearc evolve apply)"
