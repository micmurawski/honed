"""Setting a project up for evolution.

``thearc skills install self-evolve --project . --harness claude`` adopts the project's configuration *for Claude* as a
AgentConfig, keeps it in a workspace repository at ``.thearc/workspace`` (a git submodule of the project), and installs
the evolve and reflect skills into Claude's skills folder. A project evolves one harness's configuration, from that
harness's sessions. Neither skill is part of the AgentConfig (``ignore: ["skill:evolve", "skill:reflect"]``): they are
installed beside it, recorded as agent ``thearc-evolve``, and never evolved.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import cast

from thearc.harnesses import Harness
from thearc.resources import AgentConfig, Skill
from thearc.resources.git import GitError, _git
from thearc.resources.markdown import split_frontmatter

SKILL = "evolve"
SKILL_PATTERN = f"skill:{SKILL}"
REFLECT = "reflect"  # The in-session reflection skill, installed beside the evolve skill.
REFLECT_PATTERN = f"skill:{REFLECT}"
EVOLVE_AGENT = "thearc-evolve"  # The evolve and reflect skills' owner in the install record.
REFLECT_AGENT = "thearc-reflect"  # The reflect skill's owner when it is installed on its own, for any harness.
REFLECT_HARNESSES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode", "antigravity")
HARNESSES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode")
RUNTIMES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode")  # Harnesses that reflect, curate and merge.
WORKSPACE = Path(".thearc/workspace")
CACHE = Path(".thearc/cache")  # Rounds and the session index: local, never committed.
FIRST_VERSION = "1.0.0"


def thearc_version() -> str:
    try:
        return metadata.version("thearc")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def evolve_skill(harness: Harness, runtime: Harness) -> Skill:
    """The evolve skill for ``harness``: ``SKILL.md``, the default recipe ``evolve.py`` with ``HARNESS`` (whose
    sessions it reads) and ``RUNTIME`` (which reflects and curates) set, and the recipe's prompts in ``prompts/``. Its
    frontmatter names the thearc version it was made with."""
    folder = resources.files("thearc.evolve") / "skills" / SKILL
    text = (folder / "SKILL.md").read_text(encoding="utf-8").replace("HARNESS", harness)
    frontmatter, body, _ = split_frontmatter(text)
    frontmatter = dict(frontmatter or {})
    script = (folder / "evolve.py").read_text(encoding="utf-8")
    for constant, value in (("HARNESS", harness), ("RUNTIME", runtime)):
        script = re.sub(rf'^{constant} = "[^"]*"', f'{constant} = "{value}"', script, count=1, flags=re.MULTILINE)
    prompts = {f"prompts/{name}.md": (folder / "prompts" / f"{name}.md").read_text(encoding="utf-8")
               for name in ("reflect", "curate")}
    return Skill(name=SKILL, description=frontmatter.pop("description"), instructions=body,
                 metadata={"thearc": thearc_version()}, files={"evolve.py": script, **prompts})


def reflect_skill(harness: Harness) -> Skill:
    """The reflect skill for ``harness``: ``SKILL.md`` only. Its question is the recipe's ``prompts/reflect.md``, which
    ``thearc reflect begin`` prints, so in-session and handed-off reflections ask the same thing."""
    folder = resources.files("thearc.evolve") / "skills" / REFLECT
    text = (folder / "SKILL.md").read_text(encoding="utf-8").replace("HARNESS", harness)
    frontmatter, body, _ = split_frontmatter(text)
    frontmatter = dict(frontmatter or {})
    return Skill(name=REFLECT, description=frontmatter.pop("description"), instructions=body,
                 metadata={"thearc": thearc_version()})


def project_root(path: str | Path) -> Path:
    """The root of the git repository holding ``path``; a project must be one."""
    try:
        return Path(_git(Path(path), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    except GitError:
        raise ValueError(f"{Path(path).resolve()} is not in a git repository; evolution keeps its history there "
                         "(run `git init` first)") from None


def project_harness(project: str | Path) -> Harness:
    """The harness whose configuration ``project`` evolves (where its AgentConfig is installed)."""
    from thearc.resources.harness import installed_agents

    root = project_root(project)
    if not (root / WORKSPACE / ".git").exists():
        raise ValueError("this project is not set up for evolution: run "
                         "`thearc skills install self-evolve --project . --harness <harness>`")
    name = AgentConfig.from_workspace(root / WORKSPACE, journal=False).name
    harnesses = list(installed_agents(root).get(name, {}))
    if len(harnesses) != 1:
        raise ValueError(f"{name} should be installed for one harness, not {harnesses or 'none'}; reinstall it")
    return cast("Harness", harnesses[0])


def recipe_folder(project: str | Path, harness: Harness) -> Path:
    """The installed evolve skill of ``harness``: its ``evolve.py`` and ``prompts/``."""
    from thearc.resources.harness import get_harness

    return project_root(project) / get_harness(harness).location("skills") / SKILL


def adopt(project: str | Path, harness: Harness, *, name: str | None = None, ignore: Sequence[str] = ()) -> AgentConfig:
    """The project's configuration for ``harness`` as an AgentConfig at its first version, without the evolve skill
    (``ignore`` holds ``skill:evolve``) and without what ``ignore`` names."""
    root = Path(project).resolve()
    patterns = list(dict.fromkeys([SKILL_PATTERN, REFLECT_PATTERN, *ignore]))
    imported = AgentConfig.from_installation(harness, root, name=name or root.name, ignore=patterns)
    return imported.model_copy(update={"version": FIRST_VERSION})


@dataclass(frozen=True)
class Setup:
    agent: AgentConfig  # The project's AgentConfig, on its workspace.
    harness: Harness
    runtime: Harness


def install_evolve_skill(project: str | Path, harness: Harness, *, runtime: Harness | None = None,
                         ignore: Sequence[str] = (), remote: str | None = None, reflect: bool = True) -> Setup:
    """Set ``project`` up for ``/evolve`` in ``harness``: adopt its configuration for that harness into a workspace
    repository at ``.thearc/workspace``, added to the project as a git submodule (once), and install the evolve skill
    into the harness's skills folder. ``runtime`` reflects and curates (default: the harness itself). With ``remote``,
    the workspace's ``origin`` is set to it and ``main`` is pushed there; the submodule records that URL, so a
    teammate's clone fetches the workspace from it. Run again, it reinstalls the evolve skill, e.g. with another
    runtime; a project evolves one harness.

    With ``reflect`` the reflect skill is installed beside it (``/reflect``: the session's own agent reflects, in the
    session). Reinstalling upgrades a project set up before the reflect skill: its configuration then ignores
    ``skill:reflect`` too."""
    from thearc.resources.harness import InstallConflict, install, installed_agents, uninstall

    if harness not in HARNESSES:
        raise ValueError(f"cannot evolve {harness!r}; harnesses: {', '.join(HARNESSES)}")
    runtime = runtime or harness
    if runtime not in RUNTIMES:
        raise ValueError(f"{runtime} cannot reflect or curate; pass a runtime ({', '.join(RUNTIMES)})")
    root = project_root(project)
    ours = harness in installed_agents(root).get(EVOLVE_AGENT, {})
    skill = recipe_folder(root, harness) / "SKILL.md"
    if skill.is_file() and not ours:
        raise ValueError(f"{skill.relative_to(root)} is a skill of the project's own named {SKILL!r}; rename it "
                         "to install the evolve skill")
    mine = skill.parent.parent / REFLECT / "SKILL.md"
    record = _installed_files(root, EVOLVE_AGENT, harness) | _installed_files(root, REFLECT_AGENT, harness)
    if reflect and mine.is_file() and mine.relative_to(root).as_posix() not in record:
        raise ValueError(f"{mine.relative_to(root)} is a skill of the project's own named {REFLECT!r}; rename it, "
                         "or install without the reflect skill (--no-reflect)")
    folder = root / WORKSPACE
    if (folder / ".git").exists():
        if (evolved := project_harness(root)) != harness:
            raise ValueError(f"this project already evolves its {evolved} configuration; a project evolves one "
                             "harness")
        agent = AgentConfig.from_workspace(folder)
        if REFLECT_PATTERN not in agent.ignore:  # Set up before the reflect skill: never evolve it either.
            from .round import _proposal_version

            if (version := _proposal_version(folder)) is not None:
                raise ValueError(f"proposal {version} is open: apply or discard it before upgrading")
            agent = agent.ignore_targets([REFLECT_PATTERN], reason="Ignore skill:reflect: thearc's reflect skill is "
                                         "installed beside the configuration, never evolved")
        if remote is not None:
            set_remote(root, remote)
    else:
        _unignore_in_git(root, f"/{WORKSPACE.as_posix()}/")  # An earlier thearc kept it out of the project.
        agent = adopt(root, harness, ignore=ignore).init_workspace(
            folder, reason=f"Adopt {root.name}'s {harness} configuration")
        if remote is not None:
            _git(folder, "remote", "add", "origin", remote)
            _git(folder, "push", "-q", "-u", "origin", "main")
        # An existing repository at the path is added as it is; without a remote the URL is a placeholder.
        _git(root, "submodule", "add", "-q", remote or f"./{WORKSPACE.as_posix()}", WORKSPACE.as_posix())
        agent.install(harness, root, force=True)
    if reflect and harness in installed_agents(root).get(REFLECT_AGENT, {}):  # Installed on its own: taken over.
        uninstall(harness, root, name=REFLECT_AGENT)
    skills = {SKILL: evolve_skill(harness, runtime), **({REFLECT: reflect_skill(harness)} if reflect else {})}
    tool = AgentConfig(name=EVOLVE_AGENT, version=thearc_version(), skills=skills)
    try:
        install(tool, harness, root)
    except InstallConflict as error:
        raise ValueError(str(error)) from None
    _ignore_in_git(root, f"/{CACHE.as_posix()}/")  # Rounds and the session index stay local.
    return Setup(agent, harness, runtime)


def install_reflect_skill(project: str | Path, harnesses: Sequence[Harness]) -> list[Harness]:
    """Install the reflect skill on its own, for each of ``harnesses``: ``/reflect`` then judges the configuration
    installed for that harness (no evolution workspace needed), and keeps its reflections in the session index,
    beside the sessions they judge. A harness the project evolves has it with the evolve skill already; it is
    skipped. Returns the harnesses it was installed for."""
    from thearc.resources.harness import InstallConflict, install, installed_agents

    root = project_root(project)
    done: list[Harness] = []
    for harness in harnesses:
        if harness not in REFLECT_HARNESSES:
            raise ValueError(f"cannot install the reflect skill for {harness!r}; harnesses: "
                             f"{', '.join(REFLECT_HARNESSES)}")
        installed = installed_agents(root)
        if harness in installed.get(EVOLVE_AGENT, {}) and (recipe_folder(root, harness).parent / REFLECT).is_dir():
            continue  # Installed with self-evolve.
        mine = recipe_folder(root, harness).parent / REFLECT / "SKILL.md"
        if mine.is_file() and mine.relative_to(root).as_posix() not in _installed_files(root, REFLECT_AGENT, harness):
            raise ValueError(f"{mine.relative_to(root)} is a skill of the project's own named {REFLECT!r}; rename it "
                             "to install the reflect skill")
        tool = AgentConfig(name=REFLECT_AGENT, version=thearc_version(), skills={REFLECT: reflect_skill(harness)})
        try:
            install(tool, harness, root)
        except InstallConflict as error:
            raise ValueError(str(error)) from None
        done.append(harness)
    _ignore_in_git(root, f"/{CACHE.as_posix()}/")  # A reflection's question and the files it cites.
    return done


def uninstall_reflect_skill(project: str | Path, harnesses: Sequence[Harness] | None = None) -> list[Harness]:
    """Remove the reflect skill installed on its own (for ``harnesses``, default all); reflections stay."""
    from thearc.resources.harness import installed_agents, uninstall

    root = project_root(project)
    installed = [cast("Harness", name) for name in installed_agents(root).get(REFLECT_AGENT, {})]
    removed = [harness for harness in (harnesses or installed) if harness in installed]
    for harness in removed:
        uninstall(harness, root, name=REFLECT_AGENT)
    return removed


def _installed_files(root: Path, name: str, harness: Harness) -> set[str]:
    """The files agent ``name`` installed for ``harness`` in the project, by its install record."""
    from thearc.resources.harness import RECORD

    path = root / RECORD
    if not path.is_file():
        return set()
    record = json.loads(path.read_text(encoding="utf-8"))
    return set(record.get("agents", {}).get(name, {}).get("harnesses", {}).get(harness, {}).get("files", []))


def set_remote(project: str | Path, url: str) -> None:
    """Point the workspace's ``origin`` at ``url``, and the project's submodule too (``.gitmodules``)."""
    root = project_root(project)
    folder = root / WORKSPACE
    remotes = _git(folder, "remote").decode().split()
    _git(folder, "remote", "set-url" if "origin" in remotes else "add", "origin", url)
    _git(root, "submodule", "set-url", WORKSPACE.as_posix(), url)


def remote_of(project: str | Path) -> str | None:
    """The workspace's ``origin``, if it has one."""
    try:
        return _git(project_root(project) / WORKSPACE, "remote", "get-url", "origin").decode().strip() or None
    except GitError:
        return None


def uninstall_evolve_skill(project: str | Path) -> Harness:
    """Remove the evolve skill; the evolved configuration and its workspace stay. Returns the harness."""
    from thearc.resources.harness import uninstall

    harness = project_harness(project)
    uninstall(harness, project_root(project), name=EVOLVE_AGENT)
    return harness


def init_from_remote(project: str | Path) -> AgentConfig:
    """Check out the project's workspace submodule on ``main``: for a teammate who cloned a project another set up for
    evolution without ``--recurse-submodules``."""
    root = project_root(project)
    folder = root / WORKSPACE
    _git(root, "submodule", "update", "--init", "--", WORKSPACE.as_posix())
    try:
        _git(folder, "checkout", "-q", "main")  # A submodule is checked out detached; rounds work on main.
    except GitError:
        _git(folder, "checkout", "-q", "-B", "main", "origin/main")
    return AgentConfig.from_workspace(folder)


def _ignore_in_git(root: Path, line: str) -> None:
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if line not in text.splitlines():
        gitignore.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n", encoding="utf-8")


def _unignore_in_git(root: Path, line: str) -> None:
    gitignore = root / ".gitignore"
    if gitignore.is_file() and line in (lines := gitignore.read_text(encoding="utf-8").splitlines()):
        gitignore.write_text("".join(f"{kept}\n" for kept in lines if kept != line), encoding="utf-8")
