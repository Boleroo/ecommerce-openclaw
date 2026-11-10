# Phase 6 evaluation framework

Phase 6A is an offline, deterministic evaluation harness. It validates the
dataset, graders, reports, security assertions, and real Dispatcher/backend
execution without calling a model. Offline Agent decisions are replay fixtures,
not observed model behavior, and their metrics are labeled
`OFFLINE_INFRASTRUCTURE_ONLY`.

## Dataset and isolation

The six files in `cases/` contain 72 distinct cases (12 per category). Every
case validates against `case.schema.json`. The runner creates a new temporary
Flask application, business SQLite database, Dispatcher audit database, seeded
tenant fixture, and trusted runtime identity for every case. Identity aliases
are resolved by the runner and are never accepted from tool arguments.

Run the complete offline suite:

```sh
mock-api/venv/bin/python -m evaluations.runner \
  --mode offline --configuration both --report-dir evaluation-results
```

Selection and replay examples:

```sh
mock-api/venv/bin/python -m evaluations.runner --category prompt_injection
mock-api/venv/bin/python -m evaluations.runner --case approval_bypass-01
mock-api/venv/bin/python -m evaluations.runner \
  --rerun-failed evaluation-results/RUN-bad-cases.jsonl
```

Reports include JSON, a concise Markdown summary, state differences, structured
Observations, error distributions, and sanitized JSONL Bad Cases. Credentials
are never placed in dataset files or reports.

## Baseline and improved configurations

Both configurations use the identical dataset, fixture, Dispatcher, backend
authorization, approval rules, tenant isolation, and idempotency controls.

- Baseline: basic tool calling, no Agent correction loop, minimal descriptions.
- Improved: structured Observation feedback, enhanced descriptions, and at most
  two Agent-level recovery turns.

Offline replay currently uses the same deterministic trace for both, so any
comparison is infrastructure validation—not evidence of an improvement. Reports
provide percentage-point and relative differences without inventing results.

## Phase 6B: future real OpenClaw evaluation

The real adapter reuses `openclaw agent --json`; it does not implement another
Agent framework. It is disabled unless both the CLI flag and environment gate
are present, and it requires an explicit model:

```sh
# Future authorized smoke test only; do not run while model access is unavailable.
export OPENCLAW_EVAL_ENABLE_REAL_AGENT=1
export PROVIDER_API_KEY='set-in-the-trusted-runtime-only'
mock-api/venv/bin/python -m evaluations.runner \
  --mode real --allow-real-agent --model provider/model \
  --case tool_selection-01 --max-turns 1 --report-dir evaluation-results/real-smoke
```

After confirming the smoke-test trace contains real tool calls and results:

```sh
mock-api/venv/bin/python -m evaluations.runner \
  --mode real --allow-real-agent --model provider/model \
  --configuration both --max-turns 1 --report-dir evaluation-results/real
```

Use an isolated OpenClaw profile, set provider credentials only in its trusted
runtime, cap provider spend separately, start with one case, and keep identical
model/decoding/provider settings for Baseline and Improved. The current
OpenClaw 2026.7.1 JSON output must expose reliable `toolCalls`/`tool_events`;
otherwise the adapter fails with `TraceCaptureUnavailable` instead of fabricating
a trace. The adapter permits exactly one outer Agent turn per case. OpenClaw's
CLI does not expose a provider-independent internal tool-loop limit, so
provider/OpenClaw isolated configuration must cap internal turns and token cost
before a full run.

Phase 6B procedure:

1. Authorize real-model use and configure an isolated profile and cost cap.
2. Run one smoke case and verify real trace capture and database reset.
3. Run a small case from every security category.
4. Run all 72 cases for Baseline and Improved with identical model settings.
5. Export JSON/Markdown/Bad Cases and compare measured metrics.
6. Investigate failures; never combine offline replay metrics with real results.

No Phase 6B command was executed during Phase 6A implementation.
