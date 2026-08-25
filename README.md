# Formwork

Formwork is a Python runtime for building controllable CodeAct agents. It keeps the model-facing action space small and puts execution concerns in explicit components: provider adapters, action validation, workspace isolation, policy checks, context management, traces, and deterministic evaluation.

The implementation package is currently named `minicc` for compatibility. The public command is available as both `formwork` and `minicc`.

## What It Provides

- OpenAI-compatible providers with route selection, bounded retry, and optional failover.
- Strict JSON actions: `bash`, `ask`, `final`, `skill`, structured `tool_calls`, and experimental `delegate`.
- Workspace snapshots, Docker or local execution, artifact storage, and normalized observations.
- Composable command, path, network, budget, capability, and approval policies.
- Context budgeting, deterministic or semantic compaction, and prompt-cache-friendly message layout.
- Append-only traces, metrics, checkpoints, transcripts, and run reports.
- Session and chat layers for multi-turn work.
- Evaluation runners and small, reproducible fixtures for regression testing.

## Architecture

```text
Provider -> Agent Loop -> Action Protocol -> Policy Chain -> Executor
                         |                    |
                         +-> Context          +-> Workspace / Sandbox
                         +-> Trace / Metrics  +-> Artifacts / Observation
```

The main modules live under `src/minicc/`:

| Area | Responsibility |
| --- | --- |
| `core/` | loop, protocol, provider, retry, failover, context, state, sessions |
| `policy/` | command, path, network, budget, approval, capability rules |
| `sandbox/` | workspace snapshots, Docker/local execution, artifacts |
| `trace/` | event recording, metrics, replay, transcript projection |
| `evals/` | case discovery, runners, assertions, report generation |
| `memory/`, `skills/`, `meta/`, `server/` | optional higher-level subsystems |

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker only when using the Docker executor or Docker integration tests

## Install

```bash
uv sync --locked --all-groups
```

For a local editable environment, the command entry points are:

```bash
uv run formwork --help
uv run minicc --help
```

## Configuration

Copy the templates and provide a key through an environment variable:

```bash
cp .env.example .env
cp minicc.example.yaml minicc.yaml
```

`minicc.yaml` is intentionally ignored by Git. Configure one or more routes under `providers`, select one with `default_provider`, and set the corresponding `api_key_env` variable in `.env` or the shell environment.

The example configuration uses OpenAI-compatible endpoints. You can override the default route without editing YAML:

```bash
MINICC_PROVIDER=bailian uv run formwork run "inspect the repository and summarize the test layout"
```

## Basic Usage

Run a goal in an isolated workspace:

```bash
uv run formwork run "add a regression test for the parser"
```

Run against a source repository and execute locally:

```bash
uv run formwork run "fix the failing test" \
  --source-dir ./sample-project \
  --execute-local \
  --verify-command "python -m pytest -q"
```

The run writes state, trace, metrics, and artifacts under `.minicc/`. This directory is local runtime data and is excluded from version control.

Useful commands:

```bash
uv run formwork eval eval_cases/capability_suite_v1 --case C01_repo_onboarding
uv run formwork traces
uv run formwork transcript path/to/trace.jsonl
uv run formwork web --host 127.0.0.1 --port 8000
uv run formwork session new
uv run formwork chat --host 127.0.0.1 --port 8000
```

Approval and checkpoint flows are available through `approve`, `deny`, and `resume` commands. Use `--profile hybrid-v3.6` for structured filesystem tools or `--profile multi-agent-v4` for the experimental delegation layer.

## Development

Run the deterministic quality checks:

```bash
uv run ruff check src tests
uv run mypy src/minicc
uv run pytest -q
uv build
```

The test suite does not require a live provider. Docker integration tests are opt-in:

```bash
MINICC_DOCKER_INTEGRATION=1 uv run pytest tests/test_docker_runner_integration.py -q
```

## Repository Layout

```text
src/minicc/       runtime implementation
tests/            unit and integration tests
eval_cases/       reproducible evaluation fixtures
scripts/          maintenance and release checks
.github/          continuous integration workflow
```

## License

Formwork is released under the MIT License. See [LICENSE](LICENSE).

