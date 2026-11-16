"""Redaction: secrets and personal paths taken out of a session before another agent reads it.

A reflector reads a copy of the session it reviews, and its answer goes into the journal, which may be pushed. The
copy is redacted first: every string in the exported records is scanned, and what matches is replaced with a label
such as ``[REDACTED:github-token]``. Fields the harness needs to open the copy (``keep``, e.g. Claude's ``cwd``, where
the fork runs) are left as they are.

| Kind | What |
| --- | --- |
| secrets | well-known token shapes (cloud, Git hosting, model APIs, chat), private keys, JWTs, and the value of
  ``password=``, ``token: `` and the like |
| environment | the values of this process's environment variables whose names say they are secrets |
| home | the user's home directory (as ``~``), and other users' home directories (as ``/Users/user``) |
| literals, patterns | your own strings and regular expressions |

Redaction is best effort: a secret in a shape it does not know passes through. Review what you reflect on.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# (kind, pattern): the whole match is replaced.
SECRETS: tuple[tuple[str, str], ...] = (
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ("gitlab-token", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai-key", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"),
    ("google-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("stripe-key", r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("url-credentials", r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),
)
# ``name = value`` / ``name: value`` where the name says secret: only the value is replaced.
ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
    r"[A-Za-z0-9_.-]*)(\"?\s*[:=]\s*[\"']?)([^\s\"'`,;}{()\[\]\\]{8,})(?=[\s\"'`,;})\]\\]|$)"
)
# Values that are code or placeholders, not secrets: identifiers (``self.token``), numbers, references.
NOT_A_VALUE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|[0-9.]+|[$<%].*|\[REDACTED.*")
SECRET_NAME = re.compile(r"(?i)(password|passwd|secret|token|api_?key|access_?key|private_?key|credential)")
HOMES = re.compile(r"(?<![\w.-])(/Users/|/home/)(?!(?:user|Shared)\b)[^/\s\"'\\:]+")


@dataclass(frozen=True)
class Redaction:
    """What to take out of a session; ``Redaction()`` is the default, ``Redaction.off()`` takes out nothing."""

    secrets: bool = True
    environment: bool = True
    home: bool = True
    literals: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    keep: frozenset[str] = frozenset({"cwd"})  # Keys whose values are never changed.
    _rules: list[tuple[str, re.Pattern[str]]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        rules: list[tuple[str, re.Pattern[str]]] = []
        if self.secrets:
            rules += [(kind, re.compile(pattern, re.DOTALL)) for kind, pattern in SECRETS]
        values = set(self.literals)
        if self.environment:
            values |= {value for name, value in os.environ.items() if SECRET_NAME.search(name) and len(value) >= 8}
        rules += [("literal", re.compile(re.escape(value))) for value in sorted(values, key=len, reverse=True)]
        rules += [("pattern", re.compile(pattern)) for pattern in self.patterns]
        object.__setattr__(self, "_rules", rules)

    @classmethod
    def off(cls) -> Redaction:
        return cls(secrets=False, environment=False, home=False)

    def text(self, text: str, counts: Counter[str] | None = None) -> str:
        """``text`` redacted; what was replaced is counted by kind into ``counts``."""
        counts = Counter() if counts is None else counts
        for kind, rule in self._rules:
            text, n = rule.subn(f"[REDACTED:{kind}]", text)
            if n:
                counts[kind] += n
        if self.secrets:
            def value(match: re.Match[str]) -> str:
                if NOT_A_VALUE.fullmatch(match.group(3)):
                    return match.group(0)
                counts["assignment"] += 1
                return f"{match.group(1)}{match.group(2)}[REDACTED:assignment]"

            text = ASSIGNMENT.sub(value, text)
        if self.home:
            home = str(Path.home())
            if len(home) > 1 and home in text:
                counts["home"] += text.count(home)
                text = text.replace(home, "~")
            text, n = HOMES.subn(r"\1user", text)
            if n:
                counts["home"] += n
        return text

    def value(self, value: Any, counts: Counter[str]) -> Any:
        """A JSON value with every string in it redacted, except under ``keep`` keys."""
        if isinstance(value, str):
            return self.text(value, counts)
        if isinstance(value, list):
            return [self.value(item, counts) for item in value]
        if isinstance(value, dict):
            return {key: item if key in self.keep else self.value(item, counts) for key, item in value.items()}
        return value

    def export(self, text: str) -> tuple[str, Counter[str]]:
        """An exported session (JSON Lines, or one JSON document) redacted record by record, and what was replaced."""
        counts: Counter[str] = Counter()
        try:
            document = json.loads(text)
        except ValueError:
            # Records end at "\n" only: splitlines() would also cut at U+2028 and the like, which JSON strings may hold.
            lines = [json.dumps(self.value(json.loads(line), counts), ensure_ascii=False) if line.strip() else line
                     for line in text.removesuffix("\n").split("\n")]
            redacted = "\n".join(lines)
        else:
            redacted = json.dumps(self.value(document, counts), ensure_ascii=False)
        return redacted + ("\n" if text.endswith("\n") else ""), +counts

    def file(self, path: str | Path) -> Counter[str]:
        """Redact an installed session file in place; returns what was replaced."""
        path = Path(path)
        redacted, counts = self.export(path.read_text(encoding="utf-8"))
        if counts:
            path.write_text(redacted, encoding="utf-8")
        return counts


def summary(counts: Mapping[str, int] | Iterable[tuple[str, int]]) -> str:
    """``3 home, 1 github-token``: what a redaction replaced."""
    items = counts.items() if isinstance(counts, Mapping) else counts
    return ", ".join(f"{n} {kind}" for kind, n in sorted(items, key=lambda item: (-item[1], item[0])) if n)
