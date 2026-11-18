# 02 - Plan

A plan exists to find problems while they are still cheap. Keep it as short as
the task allows.

## How much planning

| Task | Plan |
|---|---|
| One obvious edit | None. Do it. |
| A few related steps | A short list in your head or a todo list |
| Many files, unclear design, or irreversible steps | A written plan with checks, shown to the person before you start |

## Building the plan

1. **Write the end state first**, as checks you can run.
2. **Work backwards** to the steps that get there.
3. **Order by risk.** Do the step most likely to fail or to change the design
   first. If the uncertain part won't work, you want to know before building
   everything around it.
4. **Give every step its own check.** "Add the endpoint" is a step. "Add the
   endpoint; curl returns 200 with the expected body" is a step you can finish.
5. **Mark the points of no return.** Deletes, migrations, pushes, sends. Put a
   look-and-confirm before each one.
6. **Name your assumptions.** Each one is either verified in step one or
   written down as a risk.

## Consider at least two approaches

For any real design choice, sketch a second option before committing. Compare
them on:

- How much existing code changes
- How easy it is to test
- How easy it is to undo
- Whether it matches what the project already does

Then pick one and say why in a sentence. Do not present a survey when the
person asked for a decision.

## Things that are easy to forget

- Empty input, one item, very large input
- Missing files, no network, no permission
- Concurrency and repeated runs: is it safe to run twice?
- Existing data and existing callers
- Platform differences: paths, line endings, shell syntax
- Secrets and personal data: never in logs, URLs or commits
- Cleanup: temp files, background processes, half-finished state on failure

## When the plan meets reality

- If a step fails, find out why before trying something else.
- If what you learn breaks an assumption, go back and re-plan. Don't patch
  around it.
- If you have tried the same idea twice with small variations, the idea is
  wrong. Step back and question the approach.
- Keep the todo list current. It is how you and the person both know where
  things stand, and it survives a long context.

## Splitting work

Parallelise only work that is independent: no shared files, no step that needs
another's output. When you hand a piece to another model, see the briefing
section in `04-communicate.md`.
