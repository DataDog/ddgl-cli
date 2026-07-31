# Agent guidelines for ddgl

## Working style

- Make small, individually-committable changes. After each logical unit of work, prompt the user for review and an eventual commit.
- When implementing, design the high-level interfaces and code structure first; only then worry about implementation details or refactoring usages.
- When writing a plan, a TODO, or deferring something the user suggests, put it in an appropriately-named Markdown file in `plans/`.

## Tooling

- Fix lint issues with `uv run ruff check --fix` rather than editing manually.

## Architecture

See `DEVELOPER.md` for full details. Key rules to enforce when writing code:

- **Layer separation**: `core/` must not import from `cli/`, `tui/`, or `render/`. CLI and TUI are thin wrappers that call `core/` functions directly.
- **Cache-awareness in `core/`, not `client.py`**: structured object caching (pipelines, jobs, logs) belongs in `core/`. The client only caches raw API responses.
- **Only cache terminal objects durably**: do not write a pipeline or job to the long-lived object cache unless it is in a terminal state (`SUCCESS`, `FAILED`, `CANCELED`, `SKIPPED`).
- **Domain types are `msgspec.Struct`**: new domain types should follow the same pattern — immutable struct with a `from_api(data: dict)` classmethod.

## Testing

- Mirror the source tree: `tests/foo/test_bar.py` tests `src/ddgl/foo/bar.py`. One test file per source module.
- Keep tests hermetic: define local stubs/fakes for types used only as scaffolding instead of importing production types. A test must not break because an unrelated production definition changed.
