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
@click.option("--project", type=PROJECT, default=Path("."), show_default=True,
              help="The project (any folder in its git repository).")
@click.option("--harness", "harnesses", required=True, multiple=True, type=click.Choice(REFLECT_HARNESSES),
              help="A harness to install /reflect for; repeat it for several.")
@_errors
def install_reflect_skill_command(project: Path, harnesses: tuple[str, ...]) -> None:
    """Install /reflect on its own for each HARNESS: the session's agent reflects on the configuration installed for
    its harness, at a milestone, in the session. Reflections are kept in the session index, beside their
    sessions (`honed sessions query 'select:reflection'`)."""
    from .setup import install_reflect_skill

    done = install_reflect_skill(project, list(harnesses))  # type: ignore[arg-type]
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
@click.option("--project", type=PROJECT, default=Path("."), show_default=True)
@click.option("--harness", "harnesses", multiple=True, type=click.Choice(REFLECT_HARNESSES),
              help="A harness to remove /reflect from (default: every one).")
@_errors
def uninstall_reflect_skill_command(project: Path, harnesses: tuple[str, ...]) -> None:
    """Remove /reflect installed on its own; the reflections stay."""
    from .setup import uninstall_reflect_skill

    removed = uninstall_reflect_skill(project, list(harnesses) or None)  # type: ignore[arg-type]
    click.echo(f"Removed /reflect for {', '.join(removed)}." if removed else "/reflect was not installed on its own.")


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
@click.option("--push", is_flag=True, help="Push the workspace's main and the release to its remote.")
@click.pass_obj
@_errors
def apply_command(where: dict[str, Any], as_json: bool, push: bool) -> None:
    """Release the open proposal and install it."""
    from .round import apply

    applied = apply(where["project"], push=push)
    if as_json:
        click.echo(json.dumps(applied.to_dict(), indent=2))
        return
    click.echo(f"Installed {applied.version} for {', '.join(applied.harnesses)}; it takes effect in new sessions.")
    click.echo(f"Pushed the workspace to {applied.pushed}." if applied.pushed else
               "The workspace was not pushed (`honed evolve apply --push` does).")
    click.echo("Commit the installed files and .honed/workspace (the submodule) together.")


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
    """Point the workspace at URL (its origin, and the project's submodule)."""
    from .setup import set_remote

    set_remote(where["project"], url)
    click.echo(f"The workspace's remote is {url}; commit .gitmodules, and `honed evolve apply --push` pushes to it.")


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
