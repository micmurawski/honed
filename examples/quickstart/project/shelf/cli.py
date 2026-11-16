"""python3 -m shelf.cli COMMAND ...: add, list and mark books as read."""

import sys

from shelf import store


def add(args):
    """add TITLE AUTHOR"""
    title, author = args
    store.save(store.add(store.load(), title, author))


def list_(args):
    """list"""
    for book in store.load():
        print(f"[{'x' if book['read'] else ' '}] {book['title']} by {book['author']}")


def read(args):
    """read TITLE"""
    (title,) = args
    store.save(store.mark_read(store.load(), title))


COMMANDS = {"add": add, "list": list_, "read": read}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print("usage: python3 -m shelf.cli {" + ",".join(COMMANDS) + "} ...", file=sys.stderr)
        return 2
    COMMANDS[argv[0]](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
