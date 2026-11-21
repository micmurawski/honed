"""Every command and subcommand has a short alias (one or two letters), unique among its siblings."""

import click
import pytest
from click.testing import CliRunner

from honed.cli import main


def commands(group: click.Group, path: tuple[str, ...] = ()):
    for name, command in group.commands.items():
        yield (*path, name), command, group
        if isinstance(command, click.Group):
            yield from commands(command, (*path, name))


@pytest.mark.parametrize("path, command, parent", list(commands(main)), ids=lambda v: " ".join(v)
                         if isinstance(v, tuple) else "")
def test_every_command_has_a_short_unique_alias(path, command, parent):
    aliases = getattr(command, "aliases", [])
    assert any(len(alias) <= 2 for alias in aliases), f"honed {' '.join(path)} needs a one- or two-letter alias"
    siblings = [name for other_name, other in parent.commands.items() if other is not command
                for name in (other_name, *getattr(other, "aliases", []))]
    assert not {path[-1], *aliases} & set(siblings), f"honed {' '.join(path)}: an alias clashes with a sibling"


@pytest.mark.parametrize("words", [["s", "q"], ["s", "g", "a"], ["e", "st"], ["sk", "i", "r"], ["sk", "u", "se"],
                                   ["r", "b"], ["a", "i"], ["rp"]])
def test_aliases_resolve(words):
    result = CliRunner().invoke(main, [*words, "--help"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Usage: ")
