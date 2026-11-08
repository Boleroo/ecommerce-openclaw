# Implementation worklog

## 2026-11-08 — Phase 2: Controlled Tool Dispatcher

### Scope and completion report

Implemented Phase 2 of `plan.md` on top of the completed persistent Flask backend.
No approval workflow, idempotency mechanism, Agent framework, LLM-provider
integration, Phase 3 role management, or later-phase functionality was added.
This entry records the completion report after it was presented in the session.
No changes were committed or pushed.

### Files created

- `dispatcher/__init__.py`: provider-independent package.
- `dispatcher/__main__.py`: local gateway entry point on port 8081.
- `dispatcher/schemas.py`: strict Pydantic input/output contracts and cross-field checks.
- `dispatcher/registry.py`: centralized metadata and schemas for all 21 business tools.
- `dispatcher/observation.py`: consistent observations, error taxonomy, recoverability, and hints.
- `dispatcher/backend_client.py`: HTTPX transport with fixed origin, timeouts, bearer forwarding, and redirects disabled.
- `dispatcher/validator.py`: backend status, error-envelope, schema, and postcondition validation.
- `dispatcher/dispatcher.py`: layered execution, prechecks, generated trace IDs, bounded retries, and audit orchestration.
- `dispatcher/audit.py`: durable SQLite execution audit, including rejected/unauthenticated calls.
- `dispatcher/gateway.py`: Flask discovery and execution HTTP adapter.
- `tests/test_dispatcher.py`: 169 unit/integration cases.
- `worklog.md`: this implementation record.

### Files modified

- `mock-api/app.py`: added authenticated `GET /v1/auth/context`; retained existing routes and authentication behavior.
- `requirements.txt`: added Pydantic and HTTPX.
- `requirements-dev.txt`: added JSON Schema validation for contract tests.
- `README.md`: architecture, startup, registry, authentication, observations, retries, audit behavior, and limitations.

Pre-existing changes to the implementation plan, Skills, startup script, and
Phase 1 files were preserved. They were not reverted or committed.

### Architecture decisions

1. Keep the execution library independent of Flask adapters and any LLM provider.
   A separate local Flask gateway exposes `GET /tools` and `POST /tools/execute`.
2. Register all 19 existing business GET routes plus refund and price writes.
   Health and authentication context are infrastructure endpoints, not tools.
3. Derive identity and existing capabilities from the backend's authenticated
   context endpoint. Forward the caller's token on every backend request; never
   accept identity/permissions from tool arguments or use a privileged fallback.
4. Validate strict inputs, backend-derived permissions and write preconditions,
   HTTP/business error results, nested output schemas, and expected write state.
   The backend rechecks authorization and business constraints transactionally.
5. Generate a new trace UUID per execution. Normalize legacy error codes and
   return stable observations without raw upstream errors or input values.
6. Share a maximum of two additional requests across the entire execution.
   Retry only transient GET failures, with bounded exponential backoff. Explicit
   permission, business-state, and approval rejection errors never auto-retry.
7. Never retry POST/PATCH automatically. A timeout or invalid response after
   dispatch yields non-recoverable `WRITE_OUTCOME_UNKNOWN`; financial parameters
   are never silently changed.
8. Keep dispatcher audit storage separate from tenant business storage so that
   unauthenticated and malformed requests can also be audited. Persist a start
   record before backend access and identity/target checkpoint before writes.
   Failed audit persistence blocks execution or leaves a pending record after an
   uncertain write. Tokens, argument bodies, and customer responses are excluded.

### Verification

- Original Phase 1 suite after backend context integration: **117 passed**.
- Initial dispatcher suite: **160 passed**.
- Expanded dispatcher suite: **169 passed**.
- Final full suite, `mock-api/venv/bin/python -m pytest -q`: **286 passed in 9.12 seconds**, no pytest warnings.
- `python -m compileall -q` for the dispatcher, dispatcher tests, and modified backend module: passed.
- `python -m pip check`: no broken requirements.
- `git diff --check`: passed.

Integration tests use HTTPX's WSGI transport against the real Flask routes and
temporary SQLite databases. All registered tools execute for both tenants.
Tests independently validate generated JSON Schema, reject malformed input and
responses (including HTTP-200 failures), exercise permission/tenant denials,
verify retry limits, and check audit persistence/failures. Additional cases
confirm that a committed refund whose response is lost is not replayed, backend
revocation/state changes after preflight still prevent execution, price response
mismatches are not retried, and concurrent callers retain distinct identities.

### Errors, bugs, and implementation notes

- Initial dependency download failed due to sandbox DNS/network restrictions.
  Retried through the approved elevated dependency-install command; installation
  of Pydantic, HTTPX, and the test-only JSON Schema validator succeeded.
- No failing assertions were observed in the completed Phase 2 test runs.
- Review accounted for SQLite connection lifecycle: audit connections explicitly
  close after each transaction rather than relying on garbage collection.
- Review added durable pre-write identity/target checkpoints so a completion-log
  failure leaves enough context for investigation. Regression tests cover this
  path and confirm checkpoint failure prevents the write.

### Remaining limitations

- No approval gates, idempotency keys, automatic reconciliation, role-management
  API, risk-policy engine, or Agent/Skill migration is implemented. HIGH risk is
  registry metadata only. Existing active-user backend capabilities are retained.
- Direct backend endpoints remain available and independently tenant-authorized.
  Network isolation of an Agent environment belongs to later integration work.
- Audit persistence and business writes do not share a distributed transaction.
  Pending audits and unknown write outcomes require manual investigation; do not
  blindly retry partial refunds.
- An unavailable audit database cannot record new failures; it blocks backend
  execution. A failed completion update can leave a durable pending record.
- Tool contracts are intentionally stricter than legacy Flask query parsing;
  existing direct API behavior remains unchanged.
- Integration validation covers real Flask/SQLite execution through WSGI and
  HTTP transport fault simulation, not deployment/load tests or Agent evaluation.
