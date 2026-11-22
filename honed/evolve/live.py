"""In-session reflection: the session's own agent reflects on its work, through the reflect skill.

    honed reflect begin [--trigger WHY]     a marker, and the question: the recipe's prompts/reflect.md
    honed reflect submit MARKER --file F    check the answer; keep it in the session index when it is right

The reflection is a turn of the live session, so the session's history is read from the harness's prompt cache. Its
marker, printed into the transcript by ``begin``, is how the session index later finds the session it judged
(``honed.sessions.reflections``). The agent cites the files it has in context by their installed paths
(``CLAUDE.md#L12``, ``.claude/skills/x/SKILL.md#L3``): ``submit`` maps them to the release's files by their text.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from honed.harnesses import Harness
from honed.reflection import Prompt, Reflection, Submission
from honed.reflection.prompts import schema_hash
from honed.reflection.reflector import fingerprint
from honed.resources import AgentConfig
from honed.resources.files import AgentFiles, FileReference
from honed.resources.git import agent_at, branch_name
from honed.resources.harness import installed_agents
from honed.resources.markers import unstamp_file
from honed.resources.reflection import ReflectionSchema
from honed.sessions.reflections import MARKER

from .defaults import SCHEMA
from .setup import CACHE, WORKSPACE, project_harness, project_root, recipe_folder

PENDING = CACHE / "reflect"  # One file per begun reflection, until it is accepted.
FILES = CACHE / "files"  # The release's files, as reflections cite them: one folder per revision.
PREFACE = """You are reflecting on this session, in it: on the work since it started, or since your last reflection
in it. The configuration you are judging is the one this session loaded — the project's instructions and skills you
have in context — at revision {revision}. Treat the transcript as evidence; do not continue the task."""


@dataclass(frozen=True)
class Begun:
    marker: str
    revision: str
    question: str
    schema: dict[str, Any]
    files: Path

    def __str__(self) -> str:
        return "\n".join([
            f"Reflection marker: {self.marker}",
            "",
            PREFACE.format(revision=self.revision),
            "",
            "## The question",
            "",
            self.question.strip(),
            "",
            "## The answer",
            "",
            "Answer with one JSON value that follows this schema:",
            "",
            json.dumps(self.schema, separators=(",", ":")),
            "",
            ("Cite lines as `path#Lx` or `path#Lx-Ly` (1-based, inclusive): the installed files you have in context "
             "(`CLAUDE.md#L12`, `.claude/skills/<name>/SKILL.md#L3`), or the copies of the release's files in "
             f"`{self.files}` (paths relative to it). Leave a rating empty when the session does not show its effect."),
            "",
            (f"Write the JSON to a file and run `honed reflect submit {self.marker} --file <path>`. If it reports "
             "problems, fix them and submit again. Do not edit the configuration while reflecting."),
        ])


def _release(root: Path, version: str | None = None) -> AgentConfig:
    """The installed release (the workspace's ``main``), or the release ``version`` when main has moved on."""
    workspace = root / WORKSPACE
    if not (workspace / ".git").exists():
        raise ValueError("this project is not set up for evolution: run "
                         "`honed skills install self-evolve --project . --harness <harness>`")
    main = branch_name(workspace, "main", "").rstrip("/")
    release = agent_at(workspace, main)
    if release is None:
        raise ValueError("the evolution workspace holds no configuration")
    if version is not None and release.version != version:
        older = agent_at(workspace, branch_name(workspace, "release", version))
        release = older or release
    return release


@dataclass(frozen=True)
class Judged:
    """What an in-session reflection judges and asks: the configuration, the question, and the schema."""

    agent: AgentConfig
    prompt: Prompt
    schema: ReflectionSchema
    evolved: bool  # The release of the project's evolution, not the installation as it is.


def judged(root: Path, harness: Harness, version: str | None = None) -> Judged:
    """For the harness the project evolves: its installed release and the recipe's prompt and schema. For any other
    harness (the reflect skill installed on its own): the configuration installed for it, honed's own skills left
    out, and the default prompt and schema — the same question, so the reflections count once the project evolves."""
    if (root / WORKSPACE / ".git").exists() and project_harness(root) == harness:
        prompt, schema = _recipe(root, harness)
        return Judged(_release(root, version), prompt, schema, True)
    agent = AgentConfig.from_installation(harness, root, name=root.name,
                                        ignore=["skill:reflect", "skill:evolve"])
    return Judged(agent, default_prompt(), ReflectionSchema.of(SCHEMA), False)


def default_prompt() -> Prompt:
    """The default recipe's reflect prompt, as the package ships it."""
    from importlib import resources

    text = (resources.files("honed.evolve") / "skills" / "evolve" / "prompts" / "reflect.md").read_text(
        encoding="utf-8")
    return Prompt.parse(text, name="reflect")


def which_harness(root: Path, harness: str | None) -> Harness:
    """The harness a reflection is for: ``harness``, else the one the project evolves, else the only one the reflect
    skill is installed for."""
    from honed.resources.harness import installed_agents

    from .setup import REFLECT_AGENT, REFLECT_HARNESSES

    if harness is not None:
        if harness not in REFLECT_HARNESSES:
            raise ValueError(f"unknown harness {harness!r}; harnesses: {', '.join(REFLECT_HARNESSES)}")
        return cast("Harness", harness)
    if (root / WORKSPACE / ".git").exists():
        return project_harness(root)
    installed = list(installed_agents(root).get(REFLECT_AGENT, {}))
    if len(installed) == 1:
        return cast("Harness", installed[0])
    raise ValueError("name the harness this session runs in: honed reflect begin --harness <harness>")


def _recipe(root: Path, harness: Harness) -> tuple[Prompt, ReflectionSchema]:
    """The recipe's reflect prompt, and its schema (the default one unless the recipe declares its own)."""
    from .round import load_recipe

    folder = recipe_folder(root, harness)
    prompt = Prompt.load(folder / "prompts" / "reflect.md")
    try:
        schema = getattr(load_recipe(folder / "evolve.py"), "SCHEMA", SCHEMA)
    except (OSError, TypeError, ValueError, ImportError):
        schema = SCHEMA
    return prompt, ReflectionSchema.of(schema)  # The model itself (``SCHEMA = Lesson``), or a ReflectionSchema.


def _files_folder(root: Path, release: AgentConfig) -> Path:
    folder = root / FILES / release.revision.replace("@", "-")
    if not folder.is_dir():
        try:
            release.files().write(folder)
        except FileExistsError:  # Another session wrote it first.
            pass
    return folder


def begin(project: str | Path = ".", *, harness: str | None = None, trigger: str | None = None) -> Begun:
    """Start an in-session reflection in a session of ``harness``: a marker, the question, and where the judged
    configuration's files are."""
    root = project_root(project)
    harness = which_harness(root, harness)
    what = judged(root, harness)
    marker = MARKER + uuid.uuid4().hex[:16]
    files = _files_folder(root, what.agent)
    state = {"marker": marker, "harness": harness, "version": what.agent.version, "revision": what.agent.revision,
             "trigger": trigger, "at": datetime.now(UTC).isoformat(), "attempts": 0}
    (root / PENDING).mkdir(parents=True, exist_ok=True)
    (root / PENDING / f"{marker.removeprefix(MARKER)}.json").write_text(json.dumps(state), encoding="utf-8")
    return Begun(marker, what.agent.revision, what.prompt.text, what.schema.json_schema(), files.relative_to(root))


@dataclass(frozen=True)
class Accepted:
    reflection: Reflection
    index: Path  # The session index it is kept in.
    bound: bool  # Joined to its session already (its marker found in an indexed transcript).

    def __str__(self) -> str:
        counts: dict[str, int] = {}
        for item in self.reflection:
            counts[str(item.rating)] = counts.get(str(item.rating), 0) + 1
        rated = ", ".join(f"{n} {rating}" for rating, n in sorted(counts.items())) or "no ratings"
        marker = self.reflection.evidence.get("marker")
        return f"Accepted {self.reflection.id} for {marker}: {len(self.reflection)} rating(s) ({rated})."


def submit(project: str | Path, marker: str, raw: Any) -> Accepted:
    """Check an in-session reflection and keep it in the session index (``honed.sessions.default_index``), beside the
    session it judges; ``ValueError`` lists what to fix."""
    root = project_root(project)
    state_file = root / PENDING / f"{marker.removeprefix(MARKER)}.json"
    if not state_file.is_file():
        raise ValueError(f"no reflection was begun with marker {marker}; run `honed reflect begin` first")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["attempts"] += 1
    state_file.write_text(json.dumps(state), encoding="utf-8")
    harness = state["harness"]
    what = judged(root, harness, state["version"])
    release, prompt, schema = what.agent, what.prompt, what.schema
    if release.revision != state["revision"]:
        raise ValueError(f"the {harness} configuration changed since this reflection began ({state['revision']} → "
                         f"{release.revision}); begin again")
    files = release.files()
    raw = translate(raw, schema, files, root, root / FILES / release.revision.replace("@", "-"))
    submission = Submission(files, schema, release.is_frozen)
    submission.submit(raw)  # ValueError with every problem.
    reflection = Reflection(
        id="reflection_" + uuid.uuid4().hex,
        output=submission.output,
        items=tuple(submission.items or ()),
        revision=release.revision,
        at=datetime.now(UTC),
        reflector={"harness": harness, "model": None, "session": None},
        evidence={"marker": marker, "asked": fingerprint(prompt, schema), "harness": harness,
                  "project": str(root)},
        attempts=state["attempts"],
        mode="in-session",
        trigger=state.get("trigger"),
        prompt={**prompt.identity(), "text": prompt.text},
        schema=schema_hash(schema),
    )
    from honed.sessions import Sessions, default_index

    with Sessions(default_index()) as sessions:
        try:  # The session's transcript, so the reflection joins it now; it does on a later sync anyway.
            sessions.add_harness_store(harness, root)
        except Exception:  # noqa: BLE001, S110 -- never lose an accepted reflection over reading transcripts
            pass
        sessions.add_reflection(reflection)
        bound = sessions.count(f'select:reflection reflection:"{reflection.id}" bound:yes') > 0
        where = sessions.path
    state_file.unlink(missing_ok=True)
    return Accepted(reflection, where, bound)


# --- citations of installed files ---------------------------------------------------------------------------------


def translate(raw: Any, schema: ReflectionSchema, files: AgentFiles, root: Path, copies: Path) -> Any:
    """``raw`` with citations of installed files (and of the release's copies) rewritten as citations of the
    release's files, where their text says unambiguously which lines they are. Others are left for the check."""
    try:
        _, _, refs = schema.validate(raw)
    except ValueError:
        return raw  # The check reports the shape problems.
    raw = json.loads(json.dumps(raw))
    for where, ref in refs:
        if ref.path in files.files:
            continue
        mapped = _map(ref, files, root, copies)
        if mapped is not None:
            _set(raw, where, str(mapped))
    return raw


def _set(value: Any, where: str, new: str) -> None:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in where.split("/")[1:]]
    for part in parts[:-1]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    last = parts[-1]
    if isinstance(value, list):
        value[int(last)] = new
    else:
        value[last] = new


def _unstamped(path: str, line: str, names: set[str]) -> str | None:
    """An installed line without the install's stamps; None for a line that is only a stamp."""
    text = line + "\n"
    for name in names:
        text = unstamp_file(path, text, name)
    return None if not text else text.rstrip("\n")


def _map(ref: FileReference, files: AgentFiles, root: Path, copies: Path) -> FileReference | None:
    path = Path(ref.path)
    path = path if path.is_absolute() else root / path
    try:
        relative = path.resolve().relative_to(copies.resolve()).as_posix()
    except ValueError:
        relative = None
    if relative is not None:  # A copy of the release's files: the same lines.
        return FileReference(relative, ref.start, ref.end) if relative in files.files else None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if ref.end > len(lines):
        return None
    names = set(installed_agents(root))
    cited = [kept.rstrip() for line in lines[ref.start - 1:ref.end]
             if (kept := _unstamped(str(path), line, names)) is not None]
    if not any(cited):
        return None
    found = []
    for candidate, text in files.files.items():
        own = [line.rstrip() for line in text.splitlines()]
        found += [(candidate, start + 1) for start in range(len(own) - len(cited) + 1)
                  if own[start:start + len(cited)] == cited]
    name = path.name
    same = [hit for hit in found if hit[0].rsplit("/", 1)[-1] == name and _parent(hit[0]) == _parent(ref.path)]
    chosen = same if len(same) == 1 else found if len(found) == 1 else []
    if not chosen:
        return None
    candidate, start = chosen[0]
    return FileReference(candidate, start, start + len(cited) - 1)


def _parent(path: str) -> str:
    """The folder a file is in (a skill's name), or nothing for a file at the top (a context document)."""
    parts = Path(path).parts
    return parts[-2] if len(parts) >= 2 else ""
