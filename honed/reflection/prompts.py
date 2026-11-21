"""What a reflection was asked, as an identity: the prompt's name and version for people, its hash for exactness.

A prompt file may start with frontmatter naming it::

    ---
    id: reflect
    version: 1
    ---
    Review this project's agent configuration …

``Prompt.load`` reads it; the reflector is given only the body, and the body is what is hashed, so the frontmatter
never changes what a reflection was asked. Two reflections with the same ``hash`` (and the same schema hash) answered
the same question; ``name@version`` says which question that was, as its author numbered it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from honed.resources.markdown import split_frontmatter
from honed.resources.reflection import ReflectionSchema

_STAMP = re.compile(r"\A<!-- [\w.-]+ [\w.+-]+ -->\n")  # ``<!-- honed-evolve 0.1.0 -->``, as installs stamp.


def normalized(text: str) -> str:
    """``text`` as it is hashed: line endings unified, trailing whitespace and surrounding blank lines dropped."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def prompt_hash(text: str) -> str:
    """A prompt body's identity: ``p_`` and 16 hex digits of the SHA-256 of its normalized text."""
    return "p_" + hashlib.sha256(normalized(text).encode()).hexdigest()[:16]


def schema_hash(schema: ReflectionSchema) -> str:
    """A reflection schema's identity: ``s_`` and 16 hex digits of the SHA-256 of its JSON schema."""
    text = json.dumps(schema.json_schema(), sort_keys=True, default=str)
    return "s_" + hashlib.sha256(text.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Prompt:
    """A reflection prompt: its body (what the reflector reads), and its name and version (from frontmatter)."""

    text: str
    name: str | None = None
    version: str | None = None

    @property
    def hash(self) -> str:
        return prompt_hash(self.text)

    @property
    def label(self) -> str:
        """``name@version``, ``name``, or the hash for a prompt without a name."""
        if self.name is None:
            return self.hash
        return f"{self.name}@{self.version}" if self.version else self.name

    def identity(self) -> dict[str, Any]:
        """``{"name", "version", "hash"}``, as a reflection records it."""
        return {"name": self.name, "version": self.version, "hash": self.hash}

    def __str__(self) -> str:
        return self.text

    @classmethod
    def parse(cls, text: str, *, name: str | None = None) -> Prompt:
        """A prompt from its file's text; ``name`` is used when the frontmatter names none (e.g. the file's stem)."""
        frontmatter, body, _ = split_frontmatter(text)
        body = _STAMP.sub("", body, count=1)  # An installed prompt's stamp is not part of the question.
        meta = dict(frontmatter or {})
        named, version = meta.get("id") or meta.get("name") or name, meta.get("version")
        return cls(text=body,  # As the file had it before frontmatter.
                   name=str(named) if named else None, version=None if version is None else str(version))

    @classmethod
    def load(cls, path: str | Path) -> Prompt:
        """The prompt in the file at ``path``; without frontmatter, it is named after the file."""
        path = Path(path)
        return cls.parse(path.read_text(encoding="utf-8"), name=path.stem)


def prompt_text(prompt: str | Prompt) -> str:
    """What the reflector is given: a prompt's body, or the text itself."""
    return prompt.text if isinstance(prompt, Prompt) else prompt


def prompt_identity(prompt: str | Prompt) -> dict[str, Any]:
    """The identity a reflection records for ``prompt``: a plain string is unnamed, known by its hash."""
    return prompt.identity() if isinstance(prompt, Prompt) else {"name": None, "version": None,
                                                                 "hash": prompt_hash(prompt)}
