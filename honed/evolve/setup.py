"""Setting a project up for evolution.

``honed skills install self-evolve --project . --harness claude`` adopts the project's configuration *for Claude* as a
AgentConfig, keeps it in a workspace repository at ``.honed/workspace`` (a git submodule of the project), and installs
the evolve and reflect skills into Claude's skills folder. A project evolves one harness's configuration, from that
harness's sessions. Neither skill is part of the AgentConfig (``ignore: ["skill:evolve", "skill:reflect"]``): they are
installed beside it, recorded as agent ``honed-evolve``, and never evolved.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import cast

from honed.harnesses import Harness
from honed.resources import AgentConfig, Skill
from honed.resources.git import GitError, _git, branch_name
from honed.resources.markdown import split_frontmatter

SKILL = "evolve"
SKILL_PATTERN = f"skill:{SKILL}"
REFLECT = "reflect"  # The in-session reflection skill, installed beside the evolve skill.
REFLECT_PATTERN = f"skill:{REFLECT}"
EVOLVE_AGENT = "honed-evolve"  # The evolve and reflect skills' owner in the install record.
REFLECT_AGENT = "honed-reflect"  # The reflect skill's owner when it is installed on its own, for any harness.
REFLECT_HARNESSES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode", "antigravity")
HARNESSES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode")
RUNTIMES: tuple[Harness, ...] = ("claude", "codex", "pi", "opencode")  # Harnesses that reflect, curate and merge.
WORKSPACE = Path(".honed/workspace")
CACHE = Path(".honed/cache")  # Rounds and the session index: local, never committed.
FIRST_VERSION = "1.0.0"


def honed_version() -> str:
    try:
        return metadata.version("honed")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def evolve_skill(harness: Harness, runtime: Harness) -> Skill:
    """The evolve skill for ``harness``: ``SKILL.md``, the default recipe ``evolve.py`` with ``HARNESS`` (whose
    sessions it reads) and ``RUNTIME`` (which reflects and curates) set, and the recipe's prompts in ``prompts/``. Its
    frontmatter names the honed version it was made with."""
    folder = resources.files("honed.evolve") / "skills" / SKILL
    text = (folder / "SKILL.md").read_text(encoding="utf-8").replace("HARNESS", harness)
    frontmatter, body, _ = split_frontmatter(text)
    frontmatter = dict(frontmatter or {})
    script = (folder / "evolve.py").read_text(encoding="utf-8")
    for constant, value in (("HARNESS", harness), ("RUNTIME", runtime)):
        script = re.sub(rf'^{constant} = "[^"]*"', f'{constant} = "{value}"', script, count=1, flags=re.MULTILINE)
    prompts = {f"prompts/{name}.md": (folder / "prompts" / f"{name}.md").read_text(encoding="utf-8")
               for name in ("reflect", "curate")}
    return Skill(name=SKILL, description=frontmatter.pop("description"), instructions=body,
                 metadata={"honed": honed_version()}, files={"evolve.py": script, **prompts})


def reflect_skill(harness: Harness) -> Skill:
    """The reflect skill for ``harness``: ``SKILL.md`` only. Its question is the recipe's ``prompts/reflect.md``, which
    ``honed reflect begin`` prints, so in-session and handed-off reflections ask the same thing."""
    folder = resources.files("honed.evolve") / "skills" / REFLECT
    text = (folder / "SKILL.md").read_text(encoding="utf-8").replace("HARNESS", harness)
    frontmatter, body, _ = split_frontmatter(text)
    frontmatter = dict(frontmatter or {})
    return Skill(name=REFLECT, description=frontmatter.pop("description"), instructions=body,
                 metadata={"honed": honed_version()})


def _toplevel(path: str | Path) -> Path:
    try:
        return Path(_git(Path(path), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    except GitError:
        raise ValueError(f"{Path(path).resolve()} is not in a git repository; evolution keeps its history there "
                         "(run `honed setup`, or `git init` first)") from None


def project_root(path: str | Path) -> Path:
    """The root of the git repository holding ``path``; a project must be one."""
    root = _toplevel(path)
    if (root / ".thearc").exists() and not (root / ".honed").exists():
        raise ValueError(f"{root} was set up by thearc, as honed was called before 0.4: run `honed setup` in it "
                         "to move .thearc to .honed and reinstall the skills")
    return root


def migrate_thearc(project: str | Path) -> bool:
    """Move a project set up by thearc (honed before 0.4) to honed's layout: ``.thearc`` becomes ``.honed`` (with
    ``git mv`` for what git tracks, the local cache too), the install record's ``thearc-*`` agents become
    ``honed-*``, and ``.gitignore`` names ``.honed``. Returns whether there was anything to move."""
    from honed.resources.harness import RECORD

    root = _toplevel(project)
    old, new = root / ".thearc", root / ".honed"
    if not old.exists() or new.exists():
        return False
    if _git(root, "ls-files", "--", ".thearc").strip():
        _git(root, "mv", ".thearc", ".honed")
    if old.exists():  # Untracked: the cache, or the whole folder.
        new.mkdir(exist_ok=True)
        for child in old.iterdir():
            child.rename(new / child.name)
        old.rmdir()
    record = root / RECORD
    if record.is_file():
        data = json.loads(record.read_text(encoding="utf-8"))
        data["agents"] = {re.sub(r"^thearc-", "honed-", name): agent for name, agent in data.get("agents", {}).items()}
        record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8")
        gitignore.write_text(text.replace("/.thearc/", "/.honed/"), encoding="utf-8")
    return True


def project_harness(project: str | Path) -> Harness:
    """The harness whose configuration ``project`` evolves (where its AgentConfig is installed)."""
    from honed.resources.harness import installed_agents

    root = project_root(project)
    if not (root / WORKSPACE / ".git").exists():
        raise ValueError("this project is not set up for evolution: run `honed setup` in it")
    name = AgentConfig.from_workspace(root / WORKSPACE, journal=False).name
    harnesses = list(installed_agents(root).get(name, {}))
    if len(harnesses) != 1:
        raise ValueError(f"{name} should be installed for one harness, not {harnesses or 'none'}; reinstall it")
    return cast("Harness", harnesses[0])


def recipe_folder(project: str | Path, harness: Harness) -> Path:
    """The installed evolve skill of ``harness``: its ``evolve.py`` and ``prompts/``."""
    from honed.resources.harness import get_harness

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
    repository at ``.honed/workspace``, added to the project as a git submodule (once), and install the evolve skill
    into the harness's skills folder. ``runtime`` reflects and curates (default: the harness itself). With ``remote``,
    the workspace's ``origin`` is set to it and ``main`` is pushed there; the submodule records that URL, so a
    teammate's clone fetches the workspace from it. Run again, it reinstalls the evolve skill, e.g. with another
    runtime; a project evolves one harness.

    With ``reflect`` the reflect skill is installed beside it (``/reflect``: the session's own agent reflects, in the
    session). Reinstalling upgrades a project set up before the reflect skill: its configuration then ignores
    ``skill:reflect`` too."""
    from honed.resources.harness import InstallConflict, install, installed_agents, uninstall

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
            agent = agent.ignore_targets([REFLECT_PATTERN], reason="Ignore skill:reflect: honed's reflect skill is "
                                         "installed beside the configuration, never evolved")
        if remote is not None:
            set_remote(root, remote)
    else:
        agent = adopt(root, harness, ignore=ignore).init_workspace(
            folder, reason=f"Adopt {root.name}'s {harness} configuration")
        if remote is not None:
            set_remote(root, remote)  # Pushed, and added to the project as a submodule.
        else:  # Local until it has a remote: kept out of the project, so no clone gets a submodule it can't fetch.
            _ignore_in_git(root, f"/{WORKSPACE.as_posix()}/")
        agent.install(harness, root, force=True)
    if reflect and harness in installed_agents(root).get(REFLECT_AGENT, {}):  # Installed on its own: taken over.
        uninstall(harness, root, name=REFLECT_AGENT)
    skills = {SKILL: evolve_skill(harness, runtime), **({REFLECT: reflect_skill(harness)} if reflect else {})}
    tool = AgentConfig(name=EVOLVE_AGENT, version=honed_version(), skills=skills)
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
    from honed.resources.harness import InstallConflict, install, installed_agents

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
        tool = AgentConfig(name=REFLECT_AGENT, version=honed_version(), skills={REFLECT: reflect_skill(harness)})
        try:
            install(tool, harness, root)
        except InstallConflict as error:
            raise ValueError(str(error)) from None
        done.append(harness)
    _ignore_in_git(root, f"/{CACHE.as_posix()}/")  # A reflection's question and the files it cites.
    return done


def uninstall_reflect_skill(project: str | Path, harnesses: Sequence[Harness] | None = None) -> list[Harness]:
    """Remove the reflect skill installed on its own (for ``harnesses``, default all); reflections stay."""
    from honed.resources.harness import installed_agents, uninstall

    root = project_root(project)
    installed = [cast("Harness", name) for name in installed_agents(root).get(REFLECT_AGENT, {})]
    removed = [harness for harness in (harnesses or installed) if harness in installed]
    for harness in removed:
        uninstall(harness, root, name=REFLECT_AGENT)
    return removed


def install_reflect_skill_global(harnesses: Sequence[Harness]) -> list[Path]:
    """Install the reflect skill on its own into each harness's own folder (``~/.claude/skills/reflect``, ...), so
    ``/reflect`` is offered in every project. Returns the folders it was installed into."""
    from honed.resources.harness import GLOBAL_HARNESSES, RECORD, InstallConflict, global_layout, install

    done: list[Path] = []
    for harness in harnesses:
        if harness not in GLOBAL_HARNESSES:
            raise ValueError(f"cannot install the reflect skill globally for {harness!r}; harnesses: "
                             f"{', '.join(GLOBAL_HARNESSES)}")
        root, layout = global_layout(harness)
        mine = root / layout.location("skills") / REFLECT / "SKILL.md"
        record = root / RECORD
        owned: set[str] = set()
        if record.is_file():
            agents = json.loads(record.read_text(encoding="utf-8")).get("agents", {})
            files = agents.get(REFLECT_AGENT, {}).get("harnesses", {}).get(harness, {}).get("files", [])
            owned = {Path(path).as_posix() for path in files}  # The record keeps them as ./skills/...
        if mine.is_file() and mine.relative_to(root).as_posix() not in owned:
            raise ValueError(f"{mine} is a skill of your own named {REFLECT!r}; rename it to install the reflect skill")
        tool = AgentConfig(name=REFLECT_AGENT, version=honed_version(), skills={REFLECT: reflect_skill(harness)})
        try:
            install(tool, layout, root)
        except InstallConflict as error:
            raise ValueError(str(error)) from None
        done.append(mine.parent)
    return done


def uninstall_reflect_skill_global(harnesses: Sequence[Harness] | None = None) -> list[Harness]:
    """Remove the reflect skill from the harnesses' own folders (default: every one that has it)."""
    from honed.resources.harness import GLOBAL_HARNESSES, global_layout, installed_agents, uninstall

    removed: list[Harness] = []
    for harness in harnesses or GLOBAL_HARNESSES:
        root, layout = global_layout(harness)
        if harness in installed_agents(root).get(REFLECT_AGENT, {}):
            uninstall(layout, root, name=REFLECT_AGENT)
            removed.append(harness)
    return removed


# --- one-command setup --------------------------------------------------------------------------------------------

# A harness's marks in a project folder, and the command that runs it.
_MARKERS: dict[Harness, tuple[str, ...]] = {
    "claude": (".claude", "CLAUDE.md", ".mcp.json"),
    "codex": (".codex",),
    "pi": (".pi",),
    "opencode": (".opencode", "opencode.json", "opencode.jsonc"),
}
_COMMANDS: dict[Harness, str] = {"claude": "claude", "codex": "codex", "pi": "pi", "opencode": "opencode"}


def detect_harness(project: str | Path) -> Harness:
    """The harness to evolve in ``project``: the only one whose configuration is in the folder, else the only one
    installed on this machine (on PATH). Ambiguous or none: a ValueError that names the candidates."""
    root = Path(project).resolve()
    found = [harness for harness, marks in _MARKERS.items() if any((root / mark).exists() for mark in marks)]
    if len(found) == 1:
        return found[0]
    if not found:
        found = [harness for harness, command in _COMMANDS.items() if shutil.which(command)]
        if len(found) == 1:
            return found[0]
        if not found:
            raise ValueError(f"no coding agent found in {root} or on PATH; install one of "
                             f"{', '.join(HARNESSES)}, or pass --harness")
    raise ValueError(f"{root} could evolve {', '.join(found)}; pick one with --harness (a project evolves one)")


def harness_command(harness: Harness) -> str | None:
    """Where ``harness``'s command is on PATH, if it is."""
    return shutil.which(_COMMANDS[harness])


def ensure_repository(project: str | Path, *, init: bool = True) -> tuple[Path, bool]:
    """The root of the git repository holding ``project``, and whether it was just created: with ``init``, a folder
    outside any repository gets ``git init`` (never the home folder or the file system's root)."""
    folder = Path(project).expanduser().resolve()
    if shutil.which("git") is None:
        raise ValueError("git is not installed; honed keeps the configuration's history in git")
    try:
        return _toplevel(folder), False
    except ValueError:
        if not init:
            raise
    if folder in {Path.home().resolve(), Path(folder.anchor)}:
        raise ValueError(f"{folder} is not a project folder; run setup inside a project")
    folder.mkdir(parents=True, exist_ok=True)
    _git(folder, "init", "-q")
    return _toplevel(folder), True


def setup_paths(root: Path) -> list[str]:
    """What setting a project up wrote, relative to its root: the submodule, the install record, .gitignore, and
    every file, context document, hooks file and MCP file the install record names."""
    from honed.resources.harness import RECORD, get_harness

    paths = [".gitmodules", *([WORKSPACE.as_posix()] if is_submodule(root) else []), RECORD, ".gitignore"]
    record = json.loads((root / RECORD).read_text(encoding="utf-8")) if (root / RECORD).is_file() else {}
    for agent in record.get("agents", {}).values():
        paths += list(agent.get("context", {}))
        for harness, entry in agent.get("harnesses", {}).items():
            paths += entry.get("files", [])
            layout = get_harness(harness)
            if entry.get("hooks") and layout.hooks_file:
                paths.append(layout.hooks_file)
            if entry.get("mcps") and layout.mcp_file:
                paths.append(layout.mcp_file)
    existing = [path for path in dict.fromkeys(paths) if (root / path).exists()]
    try:  # thearc's folder, moved: its removal goes into the same commit.
        if _git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", ".thearc").strip():
            existing.append(".thearc")
    except GitError:  # No commit yet.
        pass
    return existing


def commit_setup(root: Path, harness: Harness) -> tuple[str | None, list[str]]:
    """Commit what setup wrote, and nothing else the index holds: the new commit's short hash (None when there was
    nothing to commit), and the paths left out because .gitignore ignores them. Needs a git identity."""
    paths = setup_paths(root)
    present = [path for path in paths if (root / path).exists()]
    try:
        ignored = _git(root, "check-ignore", "--", *present).decode().split() if present else []
    except GitError:  # Exit status 1: none is ignored.
        ignored = []
    paths = [path for path in paths if path not in ignored]
    if added := [path for path in present if path not in ignored]:
        _git(root, "add", "--", *added)
    if not paths or not _git(root, "status", "--porcelain", "--", *paths).strip():
        return None, ignored
    try:
        _git(root, "commit", "-q", "-m", f"Set up honed to evolve the {harness} configuration", "--", *paths)
    except GitError as error:
        if "identity" in str(error) or "user.email" in str(error):
            raise ValueError("git has no identity to commit with: set it with `git config --global user.name "
                             "'Your Name'` and `git config --global user.email you@example.com`, or pass "
                             "--no-commit") from None
        raise
    return _git(root, "rev-parse", "--short", "HEAD").decode().strip(), ignored


@dataclass(frozen=True)
class ProjectSetup:
    setup: Setup
    root: Path
    git_initialized: bool
    commit: str | None  # The setup commit, if one was made.
    command: str | None  # Where the harness's command is, or None when it is not on PATH.
    migrated: bool = False  # Moved from thearc's .thearc first.
    ignored: tuple[str, ...] = ()  # Installed, but not committed: .gitignore ignores them.
    remote: str | None = None  # The workspace's origin; None while it is on this machine only.


def setup_project(project: str | Path = ".", *, harness: Harness | None = None, ignore: Sequence[str] = (),
                  remote: str | None = None, github: str | None = None, reflect: bool = True,
                  init_git: bool = True, commit: bool = True) -> ProjectSetup:
    """Everything ``/evolve`` needs, in one call: a git repository (``git init`` with ``init_git``), the harness
    (detected unless given), the evolve and reflect skills (``install_evolve_skill``), and with ``commit`` one commit
    of what was installed. The workspace gets ``remote``, or the GitHub repository ``github`` (``OWNER/NAME``, created
    if missing); without either it stays on this machine. The harness reflects and curates its own sessions (its
    runtime is itself). A project set up by thearc is moved to honed's layout first. Run again, it reinstalls the
    skills and commits what changed."""
    root, created = ensure_repository(project, init=init_git)
    migrated = migrate_thearc(root)
    harness = harness or (project_harness(root) if (root / WORKSPACE / ".git").exists() else detect_harness(root))
    if github is not None and remote is None:
        remote = create_github_remote(root, github)
    done = install_evolve_skill(root, harness, ignore=ignore, remote=remote, reflect=reflect)
    made, ignored = commit_setup(root, harness) if commit else (None, [])
    return ProjectSetup(done, root, created, made, harness_command(harness), migrated, tuple(ignored),
                        remote_of(root))


def _installed_files(root: Path, name: str, harness: Harness) -> set[str]:
    """The files agent ``name`` installed for ``harness`` in the project, by its install record."""
    from honed.resources.harness import RECORD

    path = root / RECORD
    if not path.is_file():
        return set()
    record = json.loads(path.read_text(encoding="utf-8"))
    return set(record.get("agents", {}).get(name, {}).get("harnesses", {}).get(harness, {}).get("files", []))


def is_submodule(project: str | Path) -> bool:
    """Whether the project holds its workspace as a git submodule (it does once the workspace has a remote)."""
    root = project_root(project)
    try:
        _git(root, "config", "-f", ".gitmodules", f"submodule.{WORKSPACE.as_posix()}.path")
    except GitError:
        return False
    return True


def set_remote(project: str | Path, url: str) -> None:
    """Point the workspace's ``origin`` at ``url`` and push its ``main`` and releases there; then the project holds
    the workspace as a submodule with that URL (added now if the workspace was local), so a clone fetches it."""
    root = project_root(project)
    folder = root / WORKSPACE
    remotes = _git(folder, "remote").decode().split()
    _git(folder, "remote", "set-url" if "origin" in remotes else "add", "origin", url)
    main, releases = branch_name(folder, "main", "").rstrip("/"), f"refs/heads/{branch_name(folder, 'release', '')}"
    _git(folder, "push", "-q", "-u", "origin", main)
    if _git(folder, "for-each-ref", "--format=%(refname)", releases).strip():
        _git(folder, "push", "-q", "origin", f"{releases}*:{releases}*")
    if is_submodule(root):
        _git(root, "submodule", "set-url", WORKSPACE.as_posix(), url)
    else:
        _unignore_in_git(root, f"/{WORKSPACE.as_posix()}/")
        _git(root, "submodule", "add", "-q", url, WORKSPACE.as_posix())  # The repository at the path, as it is.


_GITHUB = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


def github_suggestion(project: str | Path) -> str | None:
    """``OWNER/NAME`` for a new GitHub repository to hold the workspace: the project's GitHub owner (else the
    signed-in account) and ``<project>-honed``. None without an authenticated GitHub CLI (``gh``)."""
    if shutil.which("gh") is None:
        return None
    if subprocess.run(["gh", "auth", "status"], capture_output=True, check=False).returncode != 0:
        return None
    root = project_root(project)
    owner = None
    try:
        if match := _GITHUB.search(_git(root, "remote", "get-url", "origin").decode().strip()):
            owner = match["owner"]
    except GitError:
        pass
    if owner is None:
        login = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False)
        owner = login.stdout.strip() or None
    return f"{owner}/{root.name}-honed" if owner else None


def create_github_remote(project: str | Path, repository: str) -> str:
    """Create the private GitHub repository ``OWNER/NAME`` (or reuse it, if it exists) with ``gh``; its clone URL,
    over SSH when the project's own origin is."""
    if shutil.which("gh") is None:
        raise ValueError("creating the workspace's repository needs the GitHub CLI (gh); or pass --remote URL")
    root = project_root(project)
    created = subprocess.run(
        ["gh", "repo", "create", repository, "--private",
         "--description", f"honed workspace: the versions of {root.name}'s agent configuration"],
        capture_output=True, text=True, check=False)
    if created.returncode != 0 and "already exists" not in created.stderr + created.stdout:
        raise ValueError(f"gh repo create {repository}: {created.stderr.strip() or created.stdout.strip()}")
    try:
        ssh = _git(root, "remote", "get-url", "origin").decode().strip().startswith("git@")
    except GitError:
        ssh = False
    return f"git@github.com:{repository}.git" if ssh else f"https://github.com/{repository}.git"


def remote_of(project: str | Path) -> str | None:
    """The workspace's ``origin``, if it has one."""
    try:
        return _git(project_root(project) / WORKSPACE, "remote", "get-url", "origin").decode().strip() or None
    except GitError:
        return None


def uninstall_evolve_skill(project: str | Path) -> Harness:
    """Remove the evolve skill; the evolved configuration and its workspace stay. Returns the harness."""
    from honed.resources.harness import uninstall

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
