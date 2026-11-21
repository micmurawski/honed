# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub:
[Report a vulnerability](https://github.com/micmurawski/honed/security/advisories/new).
Do not open a public issue. You'll get a reply within a week; we'll agree on a fix and a disclosure date with you.

## What to keep in mind

honed reads agent session transcripts and hands them to other agents (reflectors, curators, mergers) that run
in harnesses you already use, with your own credentials. Transcripts can contain secrets, file contents, and
personal paths. Before a reflector reads a session, honed redacts known token shapes, private keys, secret
assignments, secret-named environment variables' values, and home directories; this is best effort, and a secret in
a shape it does not know passes through. Review what you index and reflect on, and don't publish session logs.

Reflectors only read; curators and mergers may edit files inside a temporary clone and never run commands. A way
around those limits is a vulnerability: please report it.

## Supported versions

Only the latest release gets security fixes.
