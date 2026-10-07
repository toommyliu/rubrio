# Rubrio

Rubrio grades paper assignments on your own machine, following Gradescope's flow. `docs/design.md` is the spec. Read the parts your change touches before starting, and update it in the same change when behavior changes.

`docs/glossary.md` defines the words to use. Use them everywhere: names of tables, columns, modules, types, routes and CLI options, UI text, messages and docs. Don't use the words it lists as wrong. If something new needs a name, add it to the glossary in the same change.

## Priorities

Make grading easier and faster. Keep routine actions fast, show progress clearly, and produce results graders can trust.

The web app is for graders of any technical level. Common grading work there must not need a terminal, configuration files, or knowledge of the implementation. The CLI and TUI are for technical users working alone.

### Performance

Consider the performance impact of every change, especially when processing a full class of submissions. Avoid repeating expensive work and keep interactive workflows responsive while processing runs.

### Reliability

Protect student submissions and grading work. Make failures visible and give graders enough information to recover. When changing what a step reads or writes, check every step that uses it.

### One source of truth

- All state lives in the home: `rubrio.db` and the `files/` it points to. Don't add a second place for a grade, rubric, roster or outline to live.
- Grades change only through `grading.py`. The web app, TUI and CLI all go through it.
- Scores are computed when read, never stored.
- The web client never computes a score and never parses the assignment file. The server sends scores, and errors with line numbers.
- Each CLI command calls the same function as the matching web action.
- Scans and uploaded templates are never modified.
- Every step is safe to rerun. The same inputs give the same result.

### The model

- Everything works with no model configured.
- Model output is a draft until a person confirms it. Never save a model result as a confirmed grade.
- Names never use the model.

### Deployment

Local mode listens on 127.0.0.1 with no sign-in. Hosted mode requires Google sign-in. Don't give the web app features that expose the grading machine, such as a shell or a file browser, in either mode. A raw port forward (for example, a TCP tunnel) makes remote requests look local, so local mode doesn't make them safe.

## Execution and verification

- Carry authorized work through implementation, integration, and verification. Make reasonable assumptions for routine, reversible local work and continue without unnecessary confirmation.
- When code changes, run the narrowest relevant tests or checks that establish correctness. Never run the full test suite. Do not add tests for trivial changes if they would only mirror the implementation. If no code changed, tests are not required.
- Use browsers or computer use for verification only when the user explicitly requests or agrees to it.
- Keep temporary working material, including implementation plans and research notes, outside the worktree. Do not commit it.

## Testing policy

- Never write unit tests after you write code.
- Highly prefer E2E tests as the sole testing mechanism. Use them to verify complex features work. At the end of E2E tests, produce a verifiable and repeatable artifact in `artifacts/`.
- `samples/cs101-quiz5` is the main fixture. Its `ground-truth.json` has the expected pages, students and grades.
- If you must test a system in isolation, first write down all the ways it could fail, then write the code.
