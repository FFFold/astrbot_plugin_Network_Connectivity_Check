# AGENTS.md

## Purpose

This repository contains an AstrBot plugin that monitors network connectivity on a schedule.
Use this file as the working guide for coding agents operating in this repo.

The codebase is small and centered on `main.py`. Prefer minimal, local changes over broad refactors.

## Repository Shape

- `main.py`: plugin entrypoint and almost all runtime logic.
- `_conf_schema.json`: WebUI config schema.
- `metadata.yaml`: plugin metadata.
- `requirements.txt`: runtime dependencies.
- `README.md`: user-facing documentation.
- `zhdocs/`: AstrBot reference docs, useful for framework behavior.

## Project Facts

- Language: Python.
- Framework: AstrBot plugin system.
- Runtime style: asyncio-first.
- Main dependency: `aiohttp>=3.8.0`.
- Plugin class: `NetworkConnectivityPlugin` in `main.py`.
- Supported checks today: HTTP, Ping, TCP.
- Persistent state is stored via `StarTools.get_data_dir(...)`.

## Rule Files Present

No repository-local Cursor rules were found:

- `.cursor/rules/`: not present
- `.cursorrules`: not present

No repository-local Copilot instructions were found:

- `.github/copilot-instructions.md`: not present

If any of those files are added later, treat them as additional constraints and update this file.

## Setup Commands

There is no dedicated build system in this repo. Typical setup is:

```bash
pip install -r requirements.txt
pip install ruff pytest
```

If validating inside a larger AstrBot checkout, also install AstrBot's dependencies there.

## Build, Lint, And Test Commands

### Format

```bash
ruff format .
ruff format main.py
```

### Lint

No local Ruff config file is present, but Ruff is the preferred linter.

```bash
ruff check .
ruff check main.py
ruff check . --fix
```

### Tests

Important: this repo currently has no `tests/` directory and no discovered automated test suite.
Do not claim tests passed unless you actually add and run them.

If tests are added later, use `pytest`:

```bash
pytest
pytest tests/test_network_connectivity.py
pytest tests/test_network_connectivity.py::test_http_check_success
pytest -k http
```

Preferred single-test pattern for reports:

```bash
pytest path/to/test_file.py::test_name
```

### Lightweight Validation

When there are no tests, use:

```bash
python -m py_compile main.py
```

Manual AstrBot validation can exercise `/net check`, `/net status`, `/net history`, and `/net addme`.

## Verification Order

For a normal change, prefer:

1. `ruff format .`
2. `ruff check .`
3. `python -m py_compile main.py`
4. Manual plugin validation in AstrBot if relevant

If tests are introduced, run the smallest relevant `pytest` command first.

## Code Style Guidelines

### General

- Keep changes minimal and local.
- Preserve the single-file structure unless the user explicitly wants refactoring.
- Prefer straightforward control flow over new abstractions.
- Follow existing AstrBot plugin patterns already present in `main.py`.
- Add comments only when logic is genuinely non-obvious.

### Imports

- Keep imports at module top unless a local import avoids unnecessary dependency cost or circularity.
- Prefer standard library imports first, then third-party imports, then AstrBot imports.
- Match the surrounding style when editing an existing block.
- Avoid unused imports.
- Local `urllib.parse` imports are acceptable for one-method URL parsing helpers.

### Formatting And Types

- Use Ruff formatting defaults.
- Keep lines readable; do not compress complex logic into one-liners.
- Preserve blank lines between logical sections.
- Use type hints on new or modified functions.
- Match the repo's mixed typing style: `Dict`, `List`, `Any`, built-in generics, and `|` unions all appear.
- Do not churn the whole file just to modernize typing syntax.
- Preserve tuple return contracts for check helpers like `(success, error_message)`.

### Naming

- Class names: `PascalCase`.
- Functions and methods: `snake_case`.
- Internal helpers: leading underscore, e.g. `_check_http`.
- Use short but descriptive locals such as `target_name`, `notify_targets`, and `error_msg`.
- Keep config keys aligned with `_conf_schema.json`.

### Async And Runtime

- Keep network and subprocess work async.
- Use `async def` for lifecycle methods and handlers.
- Reuse `aiohttp.ClientSession` where possible.
- Await cleanup of tasks and sessions during shutdown.
- Do not introduce blocking libraries like `requests`.

### AstrBot Conventions

- The plugin entry file must remain `main.py`.
- The plugin entry class must inherit from `Star`.
- Command handlers should remain methods on the plugin class.
- Use decorators from `astrbot.api.event.filter`.
- Use `yield event.plain_result(...)` for normal command replies.
- Use `self.context.send_message(...)` for proactive notifications.
- Use `astrbot.api.logger`, not Python's `logging` module.

### Config And Data

- Treat config as user-controlled and potentially incomplete.
- Access nested config with `.get(..., default)`.
- If you add config fields, update `_conf_schema.json`, README examples, and runtime defaults together.
- When mutating config, preserve the existing `hasattr(self.config, "save_config")` guard pattern.
- Store plugin data in AstrBot-managed data paths, not in the source tree.
- Use `pathlib.Path` for file operations.
- Keep persisted JSON human-readable with indentation.
- Limit history growth consistently with `advanced_settings.max_history`.

### Error Handling And Logging

- Fail gracefully; one bad target should not crash the plugin.
- Wrap I/O, network, and subprocess operations in `try/except`.
- Prefer specific exceptions when practical; use broad `except Exception` only at defensive boundaries.
- Return useful error strings, but avoid excessively long exception text.
- Preserve `asyncio.CancelledError` behavior.
- Use `logger.debug` for noisy diagnostics, `logger.info` for lifecycle/high-level operations, `logger.warning` for recoverable problems, and `logger.error` for real failures.

### Behavior-Specific Guidance

- Preserve the `/net` command group structure.
- Keep chat output concise and readable.
- Preserve notification semantics: recovery notifications are immediate; failure state-change notifications honor the configured consecutive-failure threshold; per-success and per-failure notifications are independent toggles.
- Ping behavior must remain cross-platform.
- Use safe subprocess argument lists, never shell-built ping commands.
- Keep host and port validation strong enough to avoid malformed parsing or command injection.

## Documentation Expectations

- If behavior changes, update `README.md`.
- If metadata changes, update `metadata.yaml`.
- If config behavior changes, update `_conf_schema.json` and README examples together.
- Keep this `AGENTS.md` in sync with actual repo practice.

## Practical Guidance For Agents

- Read `main.py` before making assumptions; nearly all behavior lives there.
- Search for an existing helper before adding a new one.
- Prefer modifying existing check, notification, or command flow instead of creating parallel paths.
- Do not invent a test suite or CI setup in descriptions unless you actually add it.
- If you add tests, mention the exact single-test invocation in your final response.

## Current Gaps

- No local automated tests are present today.
- No local Ruff config file is present today.
- No local type-checker config is present today.

## Preferred Final Checklist

```bash
ruff format .
ruff check .
python -m py_compile main.py
```

If any command cannot be run, say so explicitly and explain why.
