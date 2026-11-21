---
name: reflect
description: Record what helped or hurt in this project's opencode instructions and skills, from this session. Use right after a milestone (a commit or pull request, tests going from failing to passing, a finished multi-step task), after recovering from a mistake or a wrong turn, before the context is compacted, when the user says the work is done, or when the user types /reflect. At most once per milestone; not for trivial sessions.
thearc: 0.1.0
version: 0.1.0
---

# Reflect <!-- thearc-reflect 0.1.0 -->

thearc improves this project's opencode configuration (its instructions and skills) from evidence of how it helped or
hurt real work. You are in the best place to give that evidence: you have this session in context. A reflection
takes one short turn; it is kept with the session it judges, and `/evolve` (where the project uses it) curates the
configuration from such reflections — so keep it honest and grounded in what happened.

## Steps

1. Run `thearc reflect begin --harness opencode --trigger "<why now, in a few words>"` in the project's root folder.
   It prints a **marker**, the **question**, the answer's JSON schema, and how to cite lines.
2. Answer the question about this session — since its start, or since your last reflection in it. Cite the lines of
   the instructions and skills you actually followed or that should have guided you, by the paths you have them
   under (`CLAUDE.md#L12`, `.claude/skills/<name>/SKILL.md#L3`), or the copies the command names. Rate a line only
   when the session shows its effect; an empty rating is better than a guessed one.
3. Write the JSON to a file and run `thearc reflect submit <marker> --file <path>`. If it lists problems, fix them
   and submit the complete reflection again, until it is accepted.
4. Tell the user in one line that you reflected, and on what; then go on with their work.

Do not edit the configuration while reflecting, and do not continue the task in the same turn. If `thearc` is
missing, or `thearc --version` differs from the `thearc` version in this skill's frontmatter, say so and stop.
