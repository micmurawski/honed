"""A task's prompt, written by one rule from its change, so no prompt depends on who wrote it.

The pull requests' own descriptions are empty, so a prompt is an issue as a user would file it: what to change (the
change's title, in words) and one of its hidden tests as the example that should work. The example is the shortest
test that flips, with the module-level code it needs (the app, its models and routes). The other hidden tests stay
hidden: the agent has to get the behaviour right, not just this one case.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from .tasks import Task

TEMPLATE = """{request}

For example, this test should pass once it's done:

```python
{example}
```

Make the change in FastAPI's library code (`fastapi/`), keep existing behaviour working, and add tests for it.
"""
MAX_LINES = 90


def request(title: str) -> str:
    """The change's title as a request: no emoji, no pull request number, a full stop."""
    text = re.sub(r"\s*\(#\d+\)\s*$", "", title)
    text = re.sub(r"^[^\w`@]+", "", text).strip()
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def example(task: Task, repo: Path) -> str:
    """The shortest flipping test, with the imports and module-level code it uses."""
    flipping = {node.split("::", 1)[1].split("[", 1)[0] for node in task.fail_to_pass}
    best: tuple[int, str] | None = None
    for path in task.tests:
        source = subprocess.run(["git", "-C", str(repo), "show", f"{task.commit}:{path}"],
                                check=True, capture_output=True, text=True).stdout
        tree = ast.parse(source)
        tests = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in flipping]
        for test in tests:
            text = _with_context(source, tree, test)
            if best is None or len(text.splitlines()) < best[0]:
                best = (len(text.splitlines()), text)
    if best is None:
        raise ValueError(f"{task.id}: no flipping test found in its files")
    lines = best[1].splitlines()
    return "\n".join(lines[:MAX_LINES] + (["# …"] if len(lines) > MAX_LINES else []))


def _with_context(source: str, tree: ast.Module, test: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """``test`` with what it needs from its module: imports always; the module's app, models and routes only when the
    test does not build its own app; never the module's other tests or fixtures."""
    own_app = "FastAPI(" in (ast.get_source_segment(source, test) or "") or "APIRouter(" in (
        ast.get_source_segment(source, test) or "")
    parts = []
    for node in tree.body:
        if node is test:
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            parts.append(ast.get_source_segment(source, node))
        elif not own_app and not _is_test_or_fixture(node):
            parts.append(_segment(source, node))
    parts.append(_segment(source, test))
    return "\n\n".join(part for part in parts if part)


def _is_test_or_fixture(node: ast.stmt) -> bool:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    decorators = [ast.unparse(d) for d in node.decorator_list]
    return node.name.startswith("test_") or any("fixture" in d or "parametrize" in d for d in decorators)


def _segment(source: str, node: ast.stmt) -> str:
    """A node's source with its decorators (``get_source_segment`` starts at ``def``)."""
    lines = source.splitlines()
    first = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])
    return "\n".join(lines[first - 1:node.end_lineno])


def write(task: Task, repo: Path, folder: Path) -> Path:
    path = folder / "prompt.md"
    path.write_text(TEMPLATE.format(request=request(task.title), example=example(task, repo)), encoding="utf-8")
    return path
