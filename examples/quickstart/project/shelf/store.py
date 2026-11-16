"""The shelf's books, kept in a JSON file."""

import json
from pathlib import Path

DEFAULT = Path("shelf.json")


def load(path=DEFAULT):
    """The books in ``path``: a list of dicts with ``title``, ``author`` and ``read``."""
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []


def save(books, path=DEFAULT):
    """Write ``books`` to ``path``."""
    Path(path).write_text(json.dumps(books, indent=2) + "\n")


def add(books, title, author):
    """``books`` with a new, unread book."""
    return [*books, {"title": title, "author": author, "read": False}]


def mark_read(books, title):
    """``books`` with ``title`` marked read; a KeyError if there is no such book."""
    if not any(book["title"] == title for book in books):
        raise KeyError(title)
    return [{**book, "read": True} if book["title"] == title else book for book in books]
