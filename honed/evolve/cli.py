"""``honed skills install self-evolve``, ``honed skills uninstall self-evolve``, and ``honed evolve``."""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from honed.utils import AliasedGroup

from .setup import HARNESSES, REFLECT_HARNESSES, RUNTIMES

PROJECT = click.Path(file_okay=False, path_type=Path)


def _errors(command: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            return command(*args, **kwargs)
        except ValueError as error:
            raise click.ClickException(str(error)) from None

    return wrapper


def _json_option(command: Callable[..., None]) -> Callable[..., None]:
    return click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")(command)


# --- skills -------------------------------------------------------------------------------------------------------


@click.group(name="skills", cls=AliasedGroup, aliases=["sk"])
def skills() -> None:
    """honed's own skills: install them into a project, or remove them."""


@skills.group(name="install", cls=AliasedGroup, aliases=["i"])
def install() -> None:
    """Install one of honed's skills into a project."""


@install.command("self-evolve", aliases=["se"])
@click.option("--project", type=PROJECT, default=Path("."), show_default=True,
              help="The project to evolve (any folder in its git repository).")
@click.option("--harness", required=True, type=click.Choice(HARNESSES),
              help="The harness whose configuration is evolved, from its own sessions.")
@click.option("--runtime", type=click.Choice(RUNTIMES),
              help="The harness that reflects and curates (default: --harness).")
@click.option("--ignore", "ignore", multiple=True, help="Leave a target out of evolution, e.g. skill:vendor-*.")
@click.option("--remote", help="The workspace repository's URL (e.g. git@github.com:org/project-honed.git): "
              "pushed to now, and recorded in the submodule.")
@click.option("--no-reflect", "no_reflect", is_flag=True,
              help="Leave out the reflect skill (/reflect: the session's own agent reflects, in the session).")
@_errors
def install_evolve_skill_command(project: Path, harness: str, runtime: str | None, ignore: tuple[str, ...],
                                 remote: str | None, no_reflect: bool) -> None:
    """Set PROJECT up for /evolve in HARNESS: adopt its configuration for that harness as an AgentConfig in a workspace
    repository, added to the project as the git submodule .honed/workspace, and install the evolve skill and the
    reflect skill beside it."""
    from .setup import WORKSPACE, install_evolve_skill

    setup = install_evolve_skill(project, harness, runtime=runtime, ignore=list(ignore),  # type: ignore[arg-type]
                                 remote=remote, reflect=not no_reflect)
    agent = setup.agent
    click.echo(f"{agent.name} {agent.version} is set up for evolution in {setup.harness} "
               f"(runtime: {setup.runtime}; workspace: {WORKSPACE}).")
    click.echo(f"  skills: {', '.join(sorted(agent.skills)) or '-'}")
    click.echo(f"  context: {', '.join(sorted(agent.context)) or '-'}")
    click.echo(f"  hooks: {', '.join(sorted(agent.hooks)) or '-'}")
    click.echo(f"  ignored: {', '.join(agent.ignore)}")
    if not no_reflect:
        click.echo("  /reflect: installed; reflections are kept in the session index, beside their sessions")
    click.echo("Commit the installed files, .gitmodules, .honed/workspace and .gitignore"
               + ("." if remote else "; give the workspace a remote with `honed evolve remote URL` to share it.")
               + " Then type /evolve in your agent.")


@install.command("reflect", aliases=["r"])
@click.option("--project", type=PROJECT, default=None,
              help="The project (any folder in its git repository; default: the current folder).")
@click.option("--global", "global_", is_flag=True,
              help="Install it for every project: into the harness's own folder (~/.claude/skills, ...).")
@click.option("--harness", "harnesses", required=True, multiple=True, type=click.Choice(REFLECT_HARNESSES),
              help="A harness to install /reflect for; repeat it for several.")
@_errors
def install_reflect_skill_command(project: Path | None, global_: bool, harnesses: tuple[str, ...]) -> None:
    """Install /reflect on its own for each HARNESS: the session's agent reflects on the configuration installed for
    its harness, at a milestone, in the session. Reflections are kept in the session index, beside their
    sessions (`honed sessions query 'select:reflection'`). With --global, /reflect is offered in every project."""
    from .setup import install_reflect_skill, install_reflect_skill_global

    if global_:
        if project is not None:
            raise click.UsageError("choose --project or --global, not both")
        for folder in install_reflect_skill_global(list(harnesses)):  # type: ignore[arg-type]
            click.echo(f"/reflect installed in {folder}.")
        click.echo("It is offered in every project; reflections are kept in the session index.")
        return
    done = install_reflect_skill(project or Path("."), list(harnesses))  # type: ignore[arg-type]
    for harness in harnesses:
        if harness in done:
            click.echo(f"/reflect installed for {harness}.")
        else:
            click.echo(f"/reflect is installed for {harness} with the evolve skill already.")
    click.echo("Reflections are kept in the session index ($HONED_INDEX, else ~/.honed/learning.sqlite), beside "
               "their sessions. Commit the installed skill files.")


@skills.group(name="uninstall", cls=AliasedGroup, aliases=["u"])
def uninstall() -> None:
    """Remove one of honed's skills from a project."""


@uninstall.command("self-evolve", aliases=["se"])
@click.option("--project", type=PROJECT, default=Path("."), show_default=True)
@_errors
def uninstall_evolve_skill_command(project: Path) -> None:
    """Remove the evolve and reflect skills; the evolved configuration, its history and the reflections stay."""
    from .setup import uninstall_evolve_skill

    click.echo(f"Removed the evolve skill for {uninstall_evolve_skill(project)}.")


@uninstall.command("reflect", aliases=["r"])
@click.option("--project", type=PROJECT, default=None)
@click.option("--global", "global_", is_flag=True, help="Remove it from the harnesses' own folders.")
@click.option("--harness", "harnesses", multiple=True, type=click.Choice(REFLECT_HARNESSES),
              help="A harness to remove /reflect from (default: every one).")
@_errors
def uninstall_reflect_skill_command(project: Path | None, global_: bool, harnesses: tuple[str, ...]) -> None:
    """Remove /reflect installed on its own; the reflections stay."""
    from .setup import uninstall_reflect_skill, uninstall_reflect_skill_global

    if global_ and project is not None:
        raise click.UsageError("choose --project or --global, not both")
    removed = (uninstall_reflect_skill_global(list(harnesses) or None) if global_  # type: ignore[arg-type]
               else uninstall_reflect_skill(project or Path("."), list(harnesses) or None))  # type: ignore[arg-type]
    click.echo(f"Removed /reflect for {', '.join(removed)}." if removed else "/reflect was not installed on its own.")


# --- setup --------------------------------------------------------------------------------------------------------


def _github_suggestion(project: Path) -> str | None:
    """The GitHub repository to create for the workspace, as ``github_suggestion`` names it; for a folder that is
    not a repository yet, ``<signed-in account>/<folder>-honed``."""
    from .setup import github_suggestion

    try:
        return github_suggestion(project)
    except ValueError:  # Not a repository yet (setup makes it one).
        import shutil
        import subprocess

        if shutil.which("gh") is None:
            return None
        login = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False)
        owner = login.stdout.strip()
        return f"{owner}/{project.resolve().name}-honed" if login.returncode == 0 and owner else None


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask_for_remote(project: Path, *, quiet: bool) -> tuple[str | None, str | None]:
    """Where the workspace goes, asked in a terminal: ``(remote URL, GitHub OWNER/NAME)``; both None for local. A
    workspace that has a remote already, or no terminal to ask in, keeps what it has."""
    from .setup import remote_of

    try:
        if remote_of(project) is not None:
            return None, None
    except ValueError:  # Not a repository yet, or not migrated: ask.
        pass
    if quiet or not _interactive():
        return None, None
    import shutil

    suggestion = _github_suggestion(project) if shutil.which("gh") else None
    click.echo("The workspace (.honed/workspace) keeps every version of the configuration and why it changed. "
               "With a remote, teammates, CI and your other machines share it.")
    choices = []
    if suggestion:
        choices.append(("github", f"create the private GitHub repository {suggestion}"))
    choices += [("url", "use a repository URL"), ("local", "keep it on this machine for now")]
    for number, (_, label) in enumerate(choices, 1):
        click.echo(f"  [{number}] {label}")
    picked = click.prompt("Workspace remote", type=click.IntRange(1, len(choices)), default=1)
    kind = choices[picked - 1][0]
    if kind == "github":
        return None, suggestion
    if kind == "url":
        return click.prompt("Repository URL (an empty repository)"), None
    return None, None


@click.command(name="setup")
@click.argument("project", type=PROJECT, default=Path("."), required=False)
@click.option("--harness", type=click.Choice(HARNESSES),
              help="The harness to evolve (default: detected from the project's files, else the one on PATH).")
@click.option("--ignore", "ignore", multiple=True, help="Leave a target out of evolution, e.g. skill:vendor-*.")
@click.option("--remote", metavar="URL", help="The workspace repository's URL: pushed to now, and the project holds "
              "the workspace as a submodule with it.")
@click.option("--create-remote", "github", metavar="OWNER/NAME", is_flag=False, flag_value="", default=None,
              help="Create a private GitHub repository for the workspace with gh (default name: "
                   "<owner>/<project>-honed).")
@click.option("--local", is_flag=True, help="Keep the workspace on this machine only, without asking (give it a "
              "remote later with `honed evolve remote URL`).")
@click.option("--no-reflect", "no_reflect", is_flag=True, help="Leave out the reflect skill.")
@click.option("--git-init/--no-git-init", "init_git", default=True, show_default=True,
              help="Run `git init` when PROJECT is not in a git repository.")
@click.option("--commit/--no-commit", "commit", default=True, show_default=True,
              help="Commit what setup installed (and only that).")
@_json_option
@_errors
def setup_command(project: Path, harness: str | None, ignore: tuple[str, ...], remote: str | None,
                  github: str | None, local: bool, no_reflect: bool, init_git: bool, commit: bool,
                  as_json: bool) -> None:
    """Set PROJECT (default: the current folder) up for /evolve in one step: make it a git repository if it is not
    one, detect its harness, install the evolve and reflect skills, and commit them. The harness reflects on and
    curates its own sessions; `skills install self-evolve --runtime` picks another. Safe to run again.

    The workspace (.honed/workspace) holds every version of the configuration and why it changed. Give it a remote,
    so teammates, CI and your other machines share it: setup asks, offering a private GitHub repository it creates
    with gh. A local workspace is kept out of the project until it has one.

    \b
    honed setup                                  # here; asks where the workspace goes
    honed setup --create-remote                  # a private GitHub repository, <owner>/<project>-honed
    honed setup ~/code/app --harness codex --remote git@github.com:me/app-honed.git
    """
    from .setup import WORKSPACE, setup_project

    if (remote is not None) + (github is not None) + local > 1:
        raise click.UsageError("choose one of --remote, --create-remote and --local")
    if remote is None and github is None and not local:
        remote, github = _ask_for_remote(project, quiet=as_json)
    if github == "":
        github = _github_suggestion(project)
        if github is None:
            raise click.ClickException("--create-remote needs the GitHub CLI (gh), signed in (`gh auth login`); or "
                                       "name it: --create-remote OWNER/NAME")
    done = setup_project(project, harness=harness, ignore=list(ignore),  # type: ignore[arg-type]
                         remote=remote, github=github, reflect=not no_reflect, init_git=init_git, commit=commit)
    agent = done.setup.agent
    if as_json:
        click.echo(json.dumps({"project": str(done.root), "agent": agent.name, "version": agent.version,
                               "harness": done.setup.harness, "runtime": done.setup.runtime,
                               "git_initialized": done.git_initialized, "migrated": done.migrated,
                               "commit": done.commit, "ignored": list(done.ignored), "remote": done.remote,
                               "command": done.command}))
        return
    if done.git_initialized:
        click.echo(f"Initialized a git repository in {done.root}.")
    if done.migrated:
        click.echo("Moved thearc's .thearc to .honed.")
    click.echo(f"{agent.name} {agent.version} is set up for evolution in {done.setup.harness} "
               f"(runtime: {done.setup.runtime}; workspace: {WORKSPACE}).")
    click.echo(f"  skills: {', '.join(sorted(agent.skills)) or '-'}")
    click.echo(f"  context: {', '.join(sorted(agent.context)) or '-'}")
    if done.commit:
        click.echo(f"Committed the setup as {done.commit}.")
    elif commit:
        click.echo("Nothing new to commit.")
    else:
        click.echo("Commit the installed files, .honed/installed.json and .gitignore (and, with a remote, "
                   ".gitmodules and .honed/workspace).")
    if done.ignored:
        click.echo(f"Not committed, as .gitignore ignores them: {', '.join(done.ignored)}.")
    if done.remote:
        click.echo(f"The workspace's remote is {done.remote}; `honed evolve apply` pushes each release there.")
    else:
        click.echo("The workspace is on this machine only, kept out of the project: give it a remote with "
                   "`honed evolve remote URL` to share it and keep its history safe.", err=True)
    if done.command is None:
        click.echo(f"Warning: `{done.setup.harness}` is not on PATH; install it to work in the project.", err=True)
    click.echo(f"Next: work in the project with {done.setup.harness}; after three sessions or more, type /evolve.")


setup_command.aliases = ["su"]  # type: ignore[attr-defined]


# --- evolve -------------------------------------------------------------------------------------------------------


@click.group(name="evolve", cls=AliasedGroup, aliases=["e"], invoke_without_command=True)
@click.option("--project", type=PROJECT, default=Path("."), show_default=True,
              help="The project (any folder in its git repository).")
@_json_option
@click.option("--recipe", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Run this recipe instead of the installed evolve skill's evolve.py.")
@click.option("--open", "open_page", is_flag=True, help="Open the round's live progress page in the browser.")
@click.option("--estimate", "estimate_only", is_flag=True,
              help="Only estimate the round: its sessions, their size, and its cost from earlier rounds. No model "
                   "calls.")
@click.pass_context
@_errors
def evolve(ctx: click.Context, project: Path, as_json: bool, recipe: Path | None, open_page: bool,
           estimate_only: bool) -> None:
    """Run a round: propose a new version of the project's configuration from its harness's recent sessions. Its
    progress is shown live at .honed/cache/progress.html."""
    ctx.obj = {"project": project}
    if ctx.invoked_subcommand is not None:
        return
    import webbrowser

    from .round import progress_page, run, write_waiting_page
    from .setup import project_harness

    project_harness(project)  # Set up for evolution, or say how.
    page = progress_page(project)
    click.echo(f"Progress: {page.as_uri()}", err=True)
    if open_page:
        write_waiting_page(page, "Preparing the round…")
        webbrowser.open(page.as_uri())
    proposal = run(project, recipe=recipe, estimate_only=estimate_only)
    if as_json:
        click.echo(json.dumps(proposal.to_dict(), indent=2))
        return
    for drifted in proposal.drift:
        click.echo(f"Hand edit kept: {drifted}")
    if estimate_only:
        click.echo(f"Estimated: {proposal.estimate}" if proposal.estimate else f"No round: {proposal.reason}.")
        return
    if proposal.estimate is not None:
        click.echo(f"Estimated: {proposal.estimate}")
    if proposal.version is None:
        click.echo(f"No proposal: {proposal.reason}.")
        return
    click.echo(f"Proposed {proposal.version} (installed: {proposal.base}) from {proposal.sessions} session(s):")
    for change in proposal.changes:
        click.echo(f"  {change.kind} {change.target}: {change.reason}")
    click.echo(f"Report: {proposal.report}")
    click.echo(f"Progress: {proposal.progress}")
    click.echo("See the diff with `honed evolve diff`; then `honed evolve apply` or `honed evolve discard`.")


@evolve.command("diff", aliases=["d"])
@_json_option
@click.pass_obj
@_errors
def diff_command(where: dict[str, Any], as_json: bool) -> None:
    """Show the open proposal against the installed version."""
    from .round import diff

    proposal = diff(where["project"])
    if as_json:
        click.echo(json.dumps(proposal.to_dict(), indent=2))
        return
    for change in proposal.changes:
        click.echo(f"# {change.kind} {change.target} — {change.author}: {change.reason}")
    click.echo(proposal.diff)


@evolve.command("amend", aliases=["am"])
@click.option("-m", "--message", required=True, help="Why: the reason for the amendment.")
@click.pass_obj
@_errors
def amend_command(where: dict[str, Any], message: str) -> None:
    """Commit the edits made in the workspace to the open proposal."""
    from .round import amend

    for change in amend(where["project"], message):
        click.echo(change)


@evolve.command("apply", aliases=["ap"])
@_json_option
@click.option("--push/--no-push", default=None,
              help="Push the workspace's main and the release to its remote (default: when it has one).")
@click.pass_obj
@_errors
def apply_command(where: dict[str, Any], as_json: bool, push: bool | None) -> None:
    """Release the open proposal and install it."""
    from .round import apply

    applied = apply(where["project"], push=push)
    if as_json:
        click.echo(json.dumps(applied.to_dict(), indent=2))
        return
    click.echo(f"Installed {applied.version} for {', '.join(applied.harnesses)}; it takes effect in new sessions.")
    click.echo(f"Pushed the workspace to {applied.pushed}." if applied.pushed else
               "The workspace was not pushed: it has no remote (`honed evolve remote URL` gives it one)."
               if applied.pushed is None and push is None else "The workspace was not pushed.")
    from .setup import is_submodule

    click.echo("Commit the installed files and .honed/workspace (the submodule) together."
               if is_submodule(where["project"]) else "Commit the installed files.")


@evolve.command("discard", aliases=["dc"])
@click.pass_obj
@_errors
def discard_command(where: dict[str, Any]) -> None:
    """Drop the open proposal."""
    from .round import discard

    click.echo(f"Discarded {discard(where['project'])}.")


@evolve.command("ignore", aliases=["ig"])
@click.argument("patterns", nargs=-1, required=True)
@click.option("--remove", is_flag=True, help="Stop ignoring them: adopt them again from the installation.")
@click.pass_obj
@_errors
def ignore_command(where: dict[str, Any], patterns: tuple[str, ...], remove: bool) -> None:
    """Leave targets out of evolution: PATTERNS such as skill:vendor-* or "context:CLAUDE.md#Private notes"."""
    from .round import ignore

    agent = ignore(where["project"], list(patterns), remove=remove)
    click.echo(f"Ignored: {', '.join(agent.ignore) or 'nothing'}.")


@evolve.command("rollback", aliases=["rb"])
@click.argument("version")
@click.pass_obj
@_errors
def rollback_command(where: dict[str, Any], version: str) -> None:
    """Install release VERSION again, as a new version."""
    from .round import rollback

    applied = rollback(where["project"], version)
    click.echo(f"Installed {version}'s configuration as {applied.version} for {', '.join(applied.harnesses)}.")


@evolve.command("status", aliases=["st"])
@_json_option
@click.pass_obj
@_errors
def status_command(where: dict[str, Any], as_json: bool) -> None:
    """Show the installed version, any open proposal, and what is evolved and ignored."""
    from .round import status

    state = status(where["project"])
    if as_json:
        click.echo(json.dumps(state, indent=2))
        return
    for key, value in state.items():
        click.echo(f"{key}: {', '.join(value) if isinstance(value, list) else value}")


@evolve.command("init", aliases=["in"])
@click.pass_obj
@_errors
def init_command(where: dict[str, Any]) -> None:
    """Check out the workspace submodule (after cloning a project set up for evolution)."""
    from .setup import init_from_remote

    agent = init_from_remote(where["project"])
    click.echo(f"Checked out {agent.name} {agent.version}.")


@evolve.command("remote", aliases=["rm"])
@click.argument("url")
@click.pass_obj
@_errors
def remote_command(where: dict[str, Any], url: str) -> None:
    """Point the workspace at URL and push it there; the project then holds it as a submodule with that URL."""
    from .setup import set_remote

    set_remote(where["project"], url)
    click.echo(f"Pushed the workspace to {url}; the project holds it as a submodule. Commit .gitmodules, "
               ".honed/workspace and .gitignore; `honed evolve apply` pushes each release there.")


# --- reflect ------------------------------------------------------------------------------------------------------


@click.group(name="reflect", cls=AliasedGroup, aliases=["r"])
def reflect() -> None:
    """In-session reflection, for the reflect skill: the session's own agent judges the project's configuration."""


@reflect.command("begin", aliases=["b"])
@click.option("--project", type=PROJECT, default=Path("."), show_default=True)
@click.option("--harness", type=click.Choice(REFLECT_HARNESSES),
              help="The harness this session runs in (default: the one the project evolves, or the only one).")
@click.option("--trigger", help="Why now: the milestone, e.g. 'tests green after the retry fix'.")
@_json_option
@_errors
def reflect_begin_command(project: Path, harness: str | None, trigger: str | None, as_json: bool) -> None:
    """Start a reflection: print its marker and the question (the recipe's prompts/reflect.md)."""
    from .live import begin

    begun = begin(project, harness=harness, trigger=trigger)
    if as_json:
        click.echo(json.dumps({"marker": begun.marker, "revision": begun.revision, "question": begun.question,
                               "schema": begun.schema, "files": str(begun.files)}))
    else:
        click.echo(str(begun))


@reflect.command("submit", aliases=["s"])
@click.argument("marker")
@click.option("--project", type=PROJECT, default=Path("."), show_default=True)
@click.option("--file", "path", type=click.Path(dir_okay=False, allow_dash=True), default="-", show_default=True,
              help="The reflection as JSON; - reads it from stdin.")
@_errors
def reflect_submit_command(marker: str, project: Path, path: str) -> None:
    """Check the reflection begun with MARKER and keep it; on problems, list them and exit 1."""
    from .live import submit

    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except ValueError as error:
        raise click.ClickException(f"Nothing was accepted: the reflection is not JSON ({error})") from None
    accepted = submit(project, marker, raw)
    click.echo(f"{accepted} Kept in {accepted.index}" + ("." if accepted.bound
                                                         else "; it joins its session on the next sync."))
