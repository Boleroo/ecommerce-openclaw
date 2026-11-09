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

## 2026-11-08 — Independent Phase 1 / Phase 2 bug audit

### Scope, independence, and change controls

Audited baseline commit `008f3df`. Read `plan.md`, this worklog, the backend
factory/authentication, SQLAlchemy models and initialization/seeding, read and
write routes, registry, Pydantic contracts, dispatcher, HTTP client/gateway,
response validator, error mapping, retry policy, audit store, and all existing
tests. This was a fresh review of the implementation's assumptions, not an
acceptance of the previous 286-pass result.

Only `tests/test_audit_independent.py` and this worklog were added/changed by the
audit. Production code, dependencies, and existing tests were not modified.
No bugs were fixed, no assertions were weakened, and no failures were marked
xfail or skipped. Phase 3 and later work was not started. No commits or pushes
were made. Pre-existing untracked `.gitignore` and `.python-version` were left
alone.

### Verification results

| Run | Passed | Failed | Total |
| --- | ---: | ---: | ---: |
| Original suite before new audit tests | 286 | 0 | 286 |
| Initial independent audit run | 47 | 9 | 56 |
| Final new audit cases, within the full run | 65 | 9 | 74 |
| Final complete suite | **351** | **9** | **360** |

The final full run took 10.38 seconds. All original 286 tests still pass. Nine
failing cases reproduce eight confirmed issues (A03 has two parametrized
reproductions). No Critical issue was confirmed: one High, six Medium, and one
Low issue were confirmed within the tested scope. This is not proof that other
vulnerabilities are absent.

Commands:

```sh
mock-api/venv/bin/python -m pytest -q
mock-api/venv/bin/python -m pytest tests/test_audit_independent.py -q --tb=short
mock-api/venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/ecommerce-phase12-audit.xml
mock-api/venv/bin/python -m compileall -q tests/test_audit_independent.py
git diff --check
git diff --exit-code -- mock-api dispatcher requirements.txt requirements-dev.txt README.md
```

The complete-suite command exits 1 because the audit regressions remain failing.
Compilation, whitespace checks, and the production-file diff check pass.
JUnit results are at `/tmp/ecommerce-phase12-audit.xml` (temporary, not committed).
No statement/branch coverage percentage was measured.

### Independent integration and fault-injection method

The new fixture creates its own real on-disk business and audit SQLite databases,
uses strict backend authentication, and inserts deterministic 1,000-cent orders
for both tenants. It does not reuse the original paid-order response as its
expected business state. It invokes the actual gateway, dispatcher, HTTPX client,
Flask authentication/routes, SQLAlchemy transactions, and SQLite storage.

A transport wrapper delegates normally to HTTPX's WSGI transport and injects
only transport/response faults. No business operation is replaced with a mock.
Independent `sqlite3` connections inspect stored rows; rejection tests compare
full snapshots of tenants, users, products, orders, and refunds.

Verified passing scenarios:

- A 375-cent refund changes both the order balance and refund ledger, with the
  correct tenant/user, and survives application restart without reseeding.
- Excessive refunds are rejected through both direct backend and dispatcher
  paths without any business-row mutation.
- A real SQLite trigger raises ABORT on refund insertion. A SQLAlchemy event
  observer confirms the order UPDATE happened first; both the update and ledger
  insertion roll back. Backend returns `INTERNAL_ERROR`; dispatcher conservatively
  returns `WRITE_OUTCOME_UNKNOWN` without retrying. Stored state is unchanged.
- Four simultaneous partial refunds (600, 600, 400, 400 cents), synchronized at
  the backend request boundary, never exceed a 1,000-cent balance. Backend-only,
  dispatcher-only, and mixed paths all preserve the order/ledger totals and the
  other tenant's state. A valid result may total 800 or 1,000 depending on order
  of acquisition; accepted response amounts must exactly equal persisted totals.
- Cross-tenant order reads, refunds, and price writes fail in both directions
  through both entry points. Full business snapshots remain unchanged. List
  results are compared with tenant ownership queried directly from SQLite.
- Missing/invalid authentication and forged identity headers cannot perform
  refunds. Both interfaces reject booleans, fractional numbers, numeric strings,
  nulls, collections, zero, and negative cents for refunds and price updates.
- Controlled HTTP 500 (canonical `INTERNAL_ERROR`), HTTP 429 (plain-text body),
  connection/read timeouts, malformed JSON, and unexpected response structures
  have the tested retry/error behavior. Transient read attempts stop at three
  total requests; non-recoverable read errors stop at one.
- Refund and price writes are never automatically replayed under injected
  failures. Faults before dispatch leave all business rows unchanged. Faults
  after real commits leave exactly the expected single business effect and
  produce an unknown-outcome observation. Connect timeouts are injected only
  before execution; read timeouts also simulate lost post-commit responses.

### Confirmed findings

Run any reproduction below with:

```sh
mock-api/venv/bin/python -m pytest tests/test_audit_independent.py -q -k TEST_NAME --tb=short
```

All line references below refer to the audited, unchanged production source.

#### A01 — High: ambiguous committed write becomes a recoverable argument error

- **Location:** `dispatcher/validator.py:16`, `dispatcher/validator.py:22`,
  `dispatcher/dispatcher.py:153`; recovery guidance at `dispatcher/observation.py:41`.
- **Root cause:** Recognized JSON error codes override the HTTP status before
  its meaning is checked. The dispatcher marks writes unknown only when the
  resulting error category is INFRASTRUCTURE or RESPONSE. A 500 response with a
  validation/business code therefore bypasses unknown-outcome handling.
- **Reproduction:** Execute a real 100-cent refund through the gateway. After
  the backend commits and returns 201, replace the response with HTTP 500 and
  `{"code":"INVALID_ARGUMENT"}`. Do not replace the business operation.
- **Expected:** `WRITE_OUTCOME_UNKNOWN`, `recoverable: false`, and instructions
  to inspect state rather than resubmit the financial operation.
- **Actual:** Gateway returns `INVALID_ARGUMENT`, category VALIDATION,
  `recoverable: true`, and guidance to submit corrected arguments. SQLite proves
  that the order and a single refund row already contain the 100-cent refund.
  There is no automatic second POST; the confirmed hazard is incorrect guidance
  permitting a caller/Agent to repeat a financially effective operation.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_500_after_commit_is_not_recoverable_argument_error`
  (`tests/test_audit_independent.py:351`).
- **Suggested fix:** Define an explicit status/body precedence matrix. Treat
  contradictory 5xx write responses as uncertain regardless of a recoverable
  code in the body. Preserve no-write-retry behavior and distinguish trustworthy
  business rejections from ambiguous infrastructure results.

#### A02 — Medium: JSON rate-limit responses lose transient classification

- **Location:** `dispatcher/validator.py:22`, `dispatcher/validator.py:24`,
  `dispatcher/validator.py:26`.
- **Root cause:** An unrecognized `code` field triggers response-contract failure
  before the transient HTTP status is considered.
- **Reproduction:** Return HTTP 429 with
  `{"code":"RATE_LIMITED","message":"wait"}` for a product-list read,
  while authentication still runs against the real backend.
- **Expected:** A transient infrastructure observation and the bounded read
  retry policy, just as with a plain-text 429 response.
- **Actual:** `INVALID_BACKEND_RESPONSE`, `recoverable: false`; no automatic
  retry. The behavior depends on whether an otherwise equivalent rate-limit
  response contains an unknown JSON code. Business state remains unchanged.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_json_rate_limit_uses_transient_retry_policy`
  (`tests/test_audit_independent.py:364`).
- **Suggested fix:** Use HTTP transient classification when no recognized,
  authoritative non-recoverable error supersedes it. Keep explicit authorization
  and business denials non-retriable. Consider bounded handling of Retry-After
  separately; do not make writes retryable.

#### A03 — Medium: malformed refund receipts are reported as validated success

- **Location:** `dispatcher/schemas.py:399` and `dispatcher/validator.py:64`.
- **Root cause:** Refund receipt ID and timestamp are unconstrained strings.
  Postcondition validation checks money and target identity, but not these
  receipt fields needed to identify/reconcile the operation.
- **Reproduction:** Commit a real refund, then replace either the returned
  refund ID with an empty string or its `created_at` with `not-a-timestamp`.
- **Expected:** Response-contract rejection; since the write has been sent,
  return non-recoverable `WRITE_OUTCOME_UNKNOWN` and do not replay it.
- **Actual:** Both malformed receipts produce `success: true`, `error: null`,
  and are marked successful in the execution audit. SQLite contains the real
  valid refund, but the caller receives an unusable receipt as validated data.
- **Automated reproduction:** Yes, two failing cases of
  `test_audit_bug_malformed_refund_receipt_is_not_success`
  (`tests/test_audit_independent.py:378`).
- **Suggested fix:** Validate nonempty receipt identifiers in the supported
  identifier format and parse timestamp fields as valid timezone-aware ISO
  timestamps. Retain conservative unknown-write handling on validation failure.

#### A04 — Medium: returned order status can contradict the requested filter

- **Location:** `dispatcher/validator.py:49`; related input contract at
  `dispatcher/schemas.py:58`.
- **Root cause:** List postconditions validate pagination but do not verify
  requested filters against returned records.
- **Reproduction:** Call `list_orders` with `status: paid`. Let the real backend
  produce a valid order, then change just the response status to `pending` while
  keeping all amounts and remaining fields valid.
- **Expected:** `INVALID_BACKEND_RESPONSE`, non-recoverable, without altering
  database state.
- **Actual:** Dispatcher returns successful validated data containing an order
  that does not satisfy the user's requested status. SQLite remains unchanged.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_returned_orders_must_match_requested_status`
  (`tests/test_audit_independent.py:391`).
- **Suggested fix:** Check supported status/date/category/keyword constraints
  and ordering where those can be verified from returned data. Do not claim
  aggregate completeness can be proved from a single page. Start with the
  demonstrated order-status violation and add similarly focused tests.

#### A05 — Medium: output date schemas accept impossible calendar dates

- **Location:** `dispatcher/schemas.py:12`, `dispatcher/schemas.py:197`.
- **Root cause:** The shared output `Day` type checks only a digit/hyphen regex.
  Calendar parsing exists only on input `DateRange`, not on output dates.
- **Reproduction:** Replace a product-list response's `listed_at` with
  `2026-99-99`; all other response fields remain real backend values.
- **Expected:** Non-recoverable `INVALID_BACKEND_RESPONSE`.
- **Actual:** The dispatcher returns `success: true` with the impossible date.
  This is an input/output validation inconsistency, not a database mutation.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_impossible_calendar_dates_are_rejected`
  (`tests/test_audit_independent.py:406`).
- **Suggested fix:** Use a reusable calendar-date validator for both input and
  output contracts and publish appropriate JSON Schema date formats. Apply the
  same review to cohort months and timestamp fields without changing money types.

#### A06 — Medium: nested JSON escapes structured errors and execution auditing

- **Location:** `dispatcher/gateway.py:58` and `dispatcher/gateway.py:60`.
- **Root cause:** JSON decoding runs before dispatcher entry/audit creation.
  Flask's silent JSON option does not catch parser recursion errors; the gateway
  handles body-size errors but not this bounded-size parsing failure.
- **Reproduction:** POST a JSON array nested 1,200 levels to `/tools/execute`:
  `'[' * 1200 + '0' + ']' * 1200`. The body is 2,401 bytes, below 16 KiB, and
  requires no credentials to reach the decoder.
- **Expected:** HTTP 400 with a structured `INVALID_ARGUMENT` observation,
  generated trace ID, and one audit record; no business effects.
- **Actual:** Uncaught `RecursionError` reaches Flask's generic handler, yielding
  HTML HTTP 500 with no observation/trace and zero audit rows. Business data is
  unchanged. This is an error/audit-boundary failure, not an authorization bypass.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_deep_json_is_observed_and_audited`
  (`tests/test_audit_independent.py:420`).
- **Suggested fix:** Bound structural parsing depth and handle decoding failures
  at the gateway boundary through the same observation/audit path, without
  logging raw untrusted bodies or credentials.

#### A07 — Medium: unexpected exceptions lose diagnostic and attempt outcomes

- **Location:** `dispatcher/dispatcher.py:85`, `dispatcher/dispatcher.py:139`,
  `dispatcher/dispatcher.py:148`; similar logging gaps at lines 54, 89, and 135.
- **Root cause:** Broad exception handlers replace unexpected failures with
  generic observations without trace-correlated internal diagnostics. The
  per-request handler only finalizes expected exception classes, leaving other
  attempts without an outcome before the outer handler completes the audit.
- **Reproduction:** Inject `RuntimeError` at the transport boundary for the
  business read after successful real authentication; capture logs and inspect
  the persisted execution audit.
- **Expected:** A sanitized `INTERNAL_ERROR` observation plus a classified failed
  request attempt and protected, trace-correlated internal diagnostics.
- **Actual:** The overall audit records `INTERNAL_ERROR`, but its final request
  contains only method/endpoint, with no outcome. Captured error logs are empty;
  the exception class/cause is discarded. No retry or business mutation occurs.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_unexpected_exception_retains_diagnostic_outcome`
  (`tests/test_audit_independent.py:432`).
- **Suggested fix:** Finalize attempt outcomes in all exception paths and emit
  safe diagnostic events with trace and exception classification. Keep sensitive
  exception text, headers, and argument values out of public observations and
  unprotected logs. Retain unknown-outcome classification after writes.

#### A08 — Low: overstock summary and alerts disagree at the exact boundary

- **Location:** `mock-api/read_api.py:227` and `mock-api/read_api.py:258`.
- **Root cause:** Summary counts stock strictly greater than five times the
  threshold, while alerts include stock equal to five times the threshold.
- **Reproduction:** Use a valid product with stock 10 and threshold 2. Request
  `inventory_summary` and `inventory_alerts` with `type: overstock` through the
  dispatcher, using unchanged real backend calculations.
- **Expected:** The summary count equals the number of default-threshold
  overstock alerts, under one documented boundary rule.
- **Actual:** The fixture yields summary count 2 but three overstock alerts.
  This is an inherited mock-business inconsistency, not a new dispatcher write bug.
- **Automated reproduction:** Yes, failing
  `test_audit_bug_overstock_summary_agrees_with_alerts`
  (`tests/test_audit_independent.py:447`).
- **Suggested fix:** Decide whether equality qualifies, share that predicate
  between endpoints, and preserve an exact-boundary regression test.

### Existing test coverage audit

The original tests contain useful real integration, persistence, SQLAlchemy
failure injection, and concurrency checks. They are not all over-mocked. The
following gaps explain why 286 passing cases did not establish correctness:

| Location | Coverage weakness | Audit response |
| --- | --- | --- |
| `tests/test_business.py:59` | Eligible refund-state cases assert 201 only; invalid-state cases do not inspect the ledger. | Added independent SQL ledger/snapshot checks on successful/rejected operations. |
| `tests/test_reads_and_isolation.py:164` | Missing/invalid-auth write tests assert 401 without verifying all stored business state. | Added full before/after snapshots for direct and gateway calls. |
| `tests/test_reads_and_isolation.py:104` | Different tenant responses and a few substrings do not prove row ownership; searching raw JSON for `王*明` is ineffective when Flask escapes Unicode. | Compare parsed returned IDs with tenant-qualified SQLite queries. |
| `tests/test_dispatcher.py:205` | Cross-tenant dispatcher tests check errors and GET-only audit attempts but not actual stored state. | Bidirectional backend/gateway attacks now compare complete business snapshots. |
| `tests/test_dispatcher.py:267` | SequenceBackend retry tests supply already-parsed responses; 429 is tested only without a JSON code. | Inject faults beneath the real HTTP client and expose A02. |
| `tests/test_dispatcher.py:308` | Most write fault cases never execute an actual business write; one separate lost-response test does. | Expanded before/after-commit fault matrices for both writes with direct SQL verification. |
| `tests/test_dispatcher.py:253` | Tests encode body-code precedence for selected non-recoverable errors but omit contradictory recoverable codes after committed writes. | A01 tests precisely that ambiguity without permitting automatic replay. |
| `tests/test_dispatcher.py:88` | Positive JSON Schema checks validate schemas against the implementation's own normal outputs; they cannot expose permissive field declarations. | Malformed receipt/calendar/filter responses expose A03–A05. |
| `tests/test_dispatcher.py:489` | JSON gateway tests cover invalid syntax, null, text, and excessive byte size, not depth-induced parser errors. | A06 sends a small deeply nested payload through the actual decoder. |
| `tests/test_business.py:197` | Only two full refunds race; the dispatcher concurrency case performs reads. | Four partial refunds now race through backend, dispatcher, and mixed paths. |
| `tests/test_reads_and_isolation.py:228` | A randomly chosen order is reset to paid/zero refunded without deleting any existing seeded refunds. Depending on seed date, the setup can introduce inconsistent ledger state. | Independent fixture uses new deterministic orders with empty ledgers. No production issue is inferred from this test setup risk. |

The original same-resource-ID tenant test and persistence/rollback tests remain
valuable and were retained unchanged. New tests do not infer business success
from an HTTP status or from a response schema alone.

### Potential risks and coverage gaps — not confirmed public-API bugs

- **SQLite model integrity:** `mock-api/models.py:38`, `mock-api/models.py:61`,
  and `mock-api/models.py:95` constrain ranges, but do not explicitly enforce
  SQLite `typeof(...)=integer`. `Refund.user_id` at line 101 also lacks a composite
  tenant/user foreign key. Trusted direct database writers could potentially
  introduce fractional money or inconsistent actor attribution. No current HTTP
  bypass was reproduced: both public write paths validate cents and derive the
  actor from authentication. These are schema-hardening risks, not additional
  confirmed endpoint vulnerabilities.
- **Configuration exposure:** Explicit legacy anonymous-read mode intentionally
  exposes tenant A's demo reads. Strict mode was used for the new isolation tests.
  This documented compatibility mode is not counted as an authentication bypass.
- **Operational concurrency:** Tests use multiple threads, independent SQLite
  connections, and real transactions, but not multiple OS workers, simultaneous
  first-time schema creation, process termination, disk-full conditions, or
  sustained load. In-memory StaticPool behavior is not covered by the file-backed
  concurrency assertions.
- **Transport coverage:** Real HTTPX encoding/decoding and Flask WSGI paths are
  exercised, but no live TCP/TLS/proxy deployment is tested. Timeout classes are
  injected at the transport boundary rather than caused by physical network loss.
  Retry-After handling, slow streaming, response size limits, and deadline behavior
  remain unverified/unimplemented operational protections.
- **Semantic output coverage:** Additional aggregate arithmetic, date ordering,
  sort order, pagination completeness, duplicate IDs, unusual Unicode, and
  other filter combinations are not exhaustively tested. A04/A05 establish
  specific gaps, not validation completeness for every output field.
- **Audit/reconciliation limits:** Audit and business state are separate
  transactions. Pending executions require investigation. Business refund rows
  do not contain dispatcher trace IDs, so a trace alone is not a definitive
  operation-to-refund association under concurrent activity. Audit records omit
  raw arguments by design. No automatic recovery is claimed.
- **No metric fabrication:** No numeric coverage percentage, security success
  rate, production-readiness claim, or absence-of-vulnerabilities claim is inferred
  from these results.

### Missing features deliberately excluded from bug counts

Approval enforcement, idempotency/deduplication, RBAC administration, risk-policy
decisions, Agent network restriction, Skill migration, and LLM evaluation remain
later-phase work. Repeating a successful partial refund as a new explicit call
can create another refund; this documented limitation is distinct from A01's
incorrect recoverability advice. No such feature was implemented during the audit.

### Prioritized bug-fix plan — awaiting user instruction

1. **First: A01, financial outcome classification.** Define and test an explicit
   HTTP-status/error-code decision matrix. Ambiguous writes must remain unknown
   and non-recoverable. Preserve the one-write-attempt invariant and tenant checks.
2. **Next: A06 and A02, boundary/error handling.** Route decoding failures through
   structured observation/audit creation; distinguish transient HTTP errors from
   unknown JSON business codes without retrying known denials or any writes.
3. **Then: A03, A05, A04, response contracts.** Strengthen receipt/date semantics
   and directly verifiable result-filter postconditions. Keep integer-cent types
   strict and do not silently repair malformed backend results.
4. **Then: A07 and A08, diagnostics/business consistency.** Record sanitized
   trace-linked failure diagnostics and complete request-attempt outcomes; make
   inventory boundary calculations consistent.
5. **Verification gate before Phase 3:** Run all 360 tests, including the nine
   currently failing regressions, without xfail/skip or weaker assertions. Add
   focused cases for the chosen fixes and document remaining operational risks.

Production fixes are intentionally not applied. Await the user's next instruction.

## 2026-11-08 — A01–A08 focused bug-fix pass

### Scope and verification

Applied only the confirmed Phase 1/Phase 2 audit fixes. Phase 3 approval,
idempotency, expanded RBAC, and Agent integration remain untouched. Existing
Flask routes, bearer-token tenant authentication, and the 21-tool registry stay
backward compatible. No tests were deleted, skipped, marked xfail, or weakened.

Verification results:

| Run | Passed | Failed | Total |
| --- | ---: | ---: | ---: |
| Previously existing tests | 360 | 0 | 360 |
| New focused regression cases | 14 | 0 | 14 |
| Final complete suite | **374** | **0** | **374** |

Commands completed successfully:

```sh
mock-api/venv/bin/python -m pytest tests/test_audit_independent.py -q -k 'audit_bug' --tb=short
mock-api/venv/bin/python -m pytest tests/test_dispatcher.py tests/test_audit_independent.py -q --tb=short
mock-api/venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/ecommerce-phase12-fixes.xml
mock-api/venv/bin/python -m compileall -q dispatcher mock-api tests
git diff --check
```

The nine formerly failing independent audit reproductions all pass. The final
JUnit report is `/tmp/ecommerce-phase12-fixes.xml` and the full suite completed
with 374 passes in 10.32 seconds.

### Confirmed issue corrections

- **A01 (High):** Response validation previously trusted a recognized JSON code
  before evaluating an ambiguous write status. Write validation now uses an
  explicit status/code policy: only expected success and authoritative 4xx
  rejection classes can be treated as known outcomes. 429, 5xx, redirects,
  unexpected success statuses, malformed success bodies, transport exceptions,
  and post-write validation exceptions produce non-recoverable
  `WRITE_OUTCOME_UNKNOWN`. Writes still have no automatic retry path. Regression
  coverage executes both a real refund and a real price change before replacing
  their responses with contradictory HTTP 500 validation errors.
- **A02 (Medium):** Unknown JSON error fields previously masked HTTP 429 before
  transient classification. HTTP 429 now enters `UPSTREAM_UNAVAILABLE` read
  handling and consumes at most the configured two retries. Explicit
  non-recoverable authorization/business errors remain non-retriable, and writes
  remain non-retriable.
- **A03 (Medium):** Refund receipt IDs and timestamps were unconstrained strings.
  Receipt IDs now use the bounded identifier contract; receipt reasons retain
  the request length bound; timestamps require valid ISO 8601 calendar/time
  values with an explicit timezone. Invalid write receipts conservatively return
  `WRITE_OUTCOME_UNKNOWN` because the backend may already have committed.
- **A04 (Medium):** Pagination checks did not prove that returned list entries
  matched requested filters or ordering. Postconditions now verify order status,
  date, keyword and sort semantics; product status, category and keyword
  semantics; customer sorting; and ranking order/rank sequence where each is
  directly verifiable from the returned page.
- **A05 (Medium):** The shared day type enforced only a textual shape. Shared
  day, timestamp, and month validators now parse real calendar values. Date and
  timestamp contracts publish JSON Schema `format` values in addition to strict
  Pydantic validation.
- **A06 (Medium):** Gateway JSON decoding could raise before dispatcher audit
  creation. Malformed media types, parser failures, and recursion-depth failures
  now flow through the normal structured `INVALID_ARGUMENT` observation path,
  producing a trace ID and durable audit record without exposing the request
  body. Valid identifiable requests continue to retain their registered tool in
  the audit.
- **A07 (Medium):** Unexpected exceptions bypassed per-attempt outcomes and lost
  diagnostics. Every unexpected backend attempt now records `INTERNAL_ERROR` or
  `WRITE_OUTCOME_UNKNOWN`; dispatcher and audit boundary failures emit an error
  log containing only trace ID, stage, and exception type. Exception messages,
  arguments, tokens, headers, and bodies are excluded from client responses,
  logs, and audit request records.
- **A08 (Low):** Summary and alert endpoints used different exact-boundary
  comparisons. Both now share one `stock >= threshold * 5` overstock predicate.

### Files changed

- `dispatcher/validator.py`: status/code precedence, read retry classification,
  and request/response consistency checks.
- `dispatcher/dispatcher.py`: write-aware response validation, safe diagnostics,
  and complete attempt outcomes.
- `dispatcher/schemas.py`: reusable strict date/time/month and receipt contracts.
- `dispatcher/gateway.py`: structured handling for malformed and deeply nested
  JSON requests.
- `mock-api/read_api.py`: shared overstock boundary predicate.
- `tests/test_dispatcher.py`: 14 focused regression cases covering the corrected
  policy and contract gaps.
- `worklog.md`: this implementation and verification record.

### Remaining risks and intentional limits

- Audit and business databases are still separate transactions. An ambiguous
  write requires manual state reconciliation; idempotency is intentionally
  deferred to a later phase, so the dispatcher correctly says not to retry.
- `Retry-After`, response-size limits, end-to-end request deadlines, process-level
  SQLite concurrency, disk-full behavior, and live TCP/TLS failure modes remain
  operational coverage gaps.
- Response postconditions validate fields and relationships visible in a page;
  they cannot independently prove aggregate completeness, omitted rows, or the
  backend's full-dataset calculations.
- The existing documented legacy anonymous-read compatibility mode still exposes
  demo tenant reads when explicitly enabled. Strict authentication remains the
  tested production-style configuration.
- No statement or branch coverage percentage was generated. The 374-pass result
  verifies the exercised behavior but is not a claim that additional defects are
  absent.

No commit or push was performed.

## 2026-11-08 — Phase 3 RBAC and trusted risk policy

### Plan and policy decisions

Updated `plan.md` before implementation with the binding business policy:

- Viewer has `business:read` only.
- Operator adds `orders:refund` and `products:price`.
- Manager adds `approvals:manager`.
- Admin adds `approvals:admin` and tenant-scoped `roles:manage`.
- Approval permissions describe authority for the later approval workflow; they
  do not constitute an approval and do not bypass a risk decision.
- Refunds through 10,000 cents are MEDIUM/direct. Refunds from 10,001 through
  50,000 cents are HIGH and require Manager/Admin approval. Larger refunds are
  HIGH and require Admin approval.
- Price changes compare `abs(new - old) * 100` with `old * 5` and `old * 20`.
  The inclusive 5% boundary is MEDIUM/direct, over 5% through 20% requires
  Manager/Admin approval, and over 20% requires Admin approval. No floating
  point comparison is used.
- Trusted-backend precedence is authentication, server-derived identity,
  request schema, role permission, tenant-scoped lookup, current business
  constraints, risk classification, then approval enforcement/execution.
- Business-invalid operations are rejected before an approval requirement can
  affect the result. Client-supplied approval claims remain invalid arguments.
- Phase 3 has no approval execution path. Every approval-required refund or
  price change fails closed with non-recoverable `APPROVAL_REQUIRED`, including
  requests made by Manager or Admin.

### Implementation

- Added `mock-api/policy.py` as the single backend RBAC/risk policy source. Role
  permissions are immutable; refund and price decisions return explicit risk
  and required approval levels.
- Added a constrained `User.role` column. New demo users default to Operator.
  Existing SQLite databases receive a backward-compatible `operator` role
  column migration during initialization.
- Authentication now derives role and permissions from the active persisted
  user. `GET /v1/auth/context` returns user, tenant, role, and sorted permissions.
  Identity claims in query/body parameters remain rejected; role-like headers
  remain non-authoritative.
- Refund and price routes independently enforce submission permission, tenant
  ownership, current business validity, integer thresholds, and fail-closed
  approval requirements inside their existing serialized transactions.
- Added `PATCH /v1/users/{user_id}/role`. Only Admin may assign one of the four
  roles, and only to a user in the authenticated Admin's tenant. Role assignment
  cannot create or imply an approval.
- Extended the provider-independent Dispatcher identity schema with the role,
  registered `change_user_role`, and marked refund/price tools as DYNAMIC risk.
  Structured 403 approval responses preserve `APPROVAL_REQUIRED` rather than
  collapsing to `PERMISSION_DENIED`.
- Existing no-automatic-write-retry behavior is unchanged. Approval-required
  requests are attempted once at most; ambiguous refund, price, and role writes
  still produce `WRITE_OUTCOME_UNKNOWN`.
- Updated prior successful-write tests to use exact MEDIUM-risk values. Their
  persistence, rollback, concurrency, tenant-isolation, and unknown-outcome
  assertions remain intact; HIGH-risk behavior is covered separately rather
  than weakening those tests.
- Updated `README.md` with roles, dynamic risk behavior, the 22-tool registry,
  role administration, and the Phase 3 fail-closed limitation.

### Verification

Added `tests/test_phase3_policy.py` with 39 focused cases. Expanding the existing
all-tools test for the new role tool adds two more integration cases.

Coverage includes:

- Exact permission sets and authenticated context for Viewer, Operator, Manager,
  and Admin.
- Viewer read-only behavior and MEDIUM execution by Operator/Manager/Admin.
- Refund boundaries at 10,000, 10,001, 50,000, and 50,001 cents.
- Price increases and decreases at exactly 5%, one cent beyond 5%, exactly 20%,
  and one cent beyond 20%, using actual Flask/SQLite execution.
- Manager/Admin fail-closed behavior despite their approval authority, with no
  `ApprovalRequest` rows created.
- Business-state and refundable-balance errors taking precedence over approval.
- Rejection of forged early approval parameters.
- Admin tenant isolation for business resources and user-role administration at
  both direct backend and Dispatcher entry points.
- Dispatcher permission denial, approval propagation, audit outcomes, bounded
  write attempts, and response validation for role changes.
- Automatic migration of a pre-Phase-3 SQLite `users` table.

Final commands:

```sh
mock-api/venv/bin/python -m pytest tests/test_phase3_policy.py tests/test_dispatcher.py -q --tb=short
mock-api/venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/ecommerce-phase3.xml
mock-api/venv/bin/python -m compileall -q dispatcher mock-api tests
git diff --check
```

Results: **415 passed, 0 failed** in the complete suite (12.95 seconds). This
includes all prior 374 regressions plus 41 Phase 3/new-tool cases. Compilation
and whitespace checks pass. JUnit output is `/tmp/ecommerce-phase3.xml`.

### Remaining limitations

- Phase 4 is not implemented: there is no approval request creation, approve,
  reject, expire, parameter binding, or execution flow. Consequently self-
  approval is impossible today rather than merely checked at an approval API;
  Phase 4 must explicitly enforce initiator and approver inequality.
- Idempotency and automatic reconciliation remain absent. Unknown write outcomes
  require manual state inspection and must not be retried automatically.
- The role-management API is intentionally minimal. It has no last-Admin
  safeguard, delegated administration, bulk changes, or role-change history in
  the business `AuditLog`; Dispatcher invocations are still recorded in the
  separate execution audit.
- Demo bearer-token mapping remains local server configuration, not a production
  identity provider. Explicit legacy anonymous-read mode still exposes only the
  documented demo tenant's reads.
- Business and Dispatcher audit databases remain separate transactions, and no
  statement/branch coverage percentage was generated.

No Phase 4 approval execution or idempotency code was added. No commit or push
was performed.

## Phase 4 — Human approval and persistent idempotency

### Scope and architecture

Implemented Phase 4 only, reusing the existing Flask authentication, RBAC,
integer-cent risk policies, SQLAlchemy models, Dispatcher, and SQLite database.
The preceding Phase 3 limitations are historical; approval and idempotency are
now implemented as described here. No Phase 5/OpenClaw integration was added.

- HIGH-risk refund and price submissions persist a PENDING approval instead of
  executing. The existing HTTP 403 `APPROVAL_REQUIRED` response now contains a
  validated approval descriptor. MEDIUM-risk operations continue to execute
  directly for authorized users, with unchanged thresholds and business limits.
- Added persistent PENDING, APPROVED, REJECTED, EXPIRED, EXECUTING, COMPLETED,
  and FAILED states. Human approve/reject endpoints are deliberately absent
  from the Tool Registry; only `get_approval` and `execute_approval` were added
  to the Agent-facing tools (24 tools total).
- Human decisions require separately configured `HUMAN_APPROVAL_TOKENS`,
  disjoint from ordinary API credentials. Current Manager/Admin authority,
  tenant membership, active user status, and initiator/reviewer inequality are
  enforced by the backend, not caller-supplied flags or Dispatcher claims.
  Ordinary API credentials cannot approve even when their user is an Admin.
- A canonical SHA-256 digest binds operation ID, tenant, initiator, operation,
  resource, normalized parameters, approval level, and expiration. Price
  approvals additionally bind the original price. Action bodies cannot replace
  parameters. Tampered bindings fail closed.
- Only the initiator can execute; execution revalidates their permission,
  reviewer authority, risk policy, and current business state. Business or
  permission invalidation records FAILED without financial effects. A changed
  product price requires a new request rather than silently approving a
  different percentage change.
- SQLite `BEGIN IMMEDIATE` serializes lifecycle decisions and financial writes.
  EXECUTING, the business mutation, COMPLETED/result, and business audit events
  share one transaction. EXECUTING is not independently committed: a database
  failure rolls back to APPROVED, allowing explicit recovery without a partial
  refund. Failure diagnostics retain the existing sanitized backend errors;
  an infrastructure-failure audit event is attempted after rollback.
- Optional body `idempotency_key` or `Idempotency-Key` header binds a tenant-wide
  key to the authenticated initiator and exact normalized submission. A unique
  record stores the original response/status atomically with its effect or
  pending approval. Reuse for a different request returns IDEMPOTENCY_CONFLICT.
  Completed approval IDs independently provide execution deduplication.
- Submission replays deliberately return the original receipt, including an
  original PENDING/403 receipt; GET approval returns current lifecycle state.
  Keyed Dispatcher submissions skip advisory business preflight so replaying a
  completed full refund is not rejected merely because its order is now fully
  refunded. Backend authorization and validation remain authoritative.
- No automatic write retries were introduced. Lost or malformed write responses
  remain nonrecoverable WRITE_OUTCOME_UNKNOWN. Callers can inspect approval
  state or explicitly repeat the identical keyed submission/execution safely.
- Lifecycle events and successful financial effects include authenticated
  actor/tenant, operation IDs, state/digest where applicable, and trace IDs in
  the business audit. Invalid lifecycle bodies and denied access are audited
  without raw credentials. Dispatcher invocation auditing remains separate.
- Existing SQLite databases receive additive approval/idempotency columns and
  the operation-ID unique index. Legacy placeholder approvals lacking a bound
  digest become FAILED rather than being executable.

### Files

Created `mock-api/approvals.py` and `tests/test_phase4_approvals.py`.

Modified `mock-api/app.py`, `mock-api/business.py`, `mock-api/database.py`,
`mock-api/models.py`, `dispatcher/dispatcher.py`, `dispatcher/gateway.py`,
`dispatcher/observation.py`, `dispatcher/registry.py`, `dispatcher/schemas.py`,
`dispatcher/validator.py`, `tests/test_dispatcher.py`,
`tests/test_phase3_policy.py`, `README.md`, `plan.md`, and this worklog.

The implementation plan now specifies the Phase 4 transaction, digest, human
credential, expiry, and replay contracts; OpenClaw wiring remains Phase 5.
Pre-existing untracked `.gitignore` and `.python-version` were left untouched.

### Verification and encountered integration mismatches

The initial full run reported 398 passed / 21 failed: existing registry tests
expected 22 tools and lacked arguments/setup for the two new tools, while
Phase 3 tests expected approval-required responses with no persistent approval.
Updated these obsolete Phase 3 expectations to the Phase 4 contract without
removing security or financial-state assertions: tests still verify 403,
APPROVAL_REQUIRED, unchanged business state, and exact threshold decisions,
and now additionally verify the PENDING descriptor and matching database row.
Human-only decision routes are explicitly excluded from Agent tool coverage;
new-tool integration tests use actual human approval through the backend.

Added 80 focused Phase 4 tests, plus four cases in the existing all-tools
integration matrix. Real temporary SQLite databases and Flask/Dispatcher paths
cover:

- Refund/price threshold approvals, independent reviewers, successful execution,
  terminal replay, and ordered lifecycle audit events with operation/trace IDs.
- Reject and expiry paths, self-approval, insufficient authority, ordinary API
  credentials, cross-tenant attempts, and unrelated-user access with database
  state checked after denials.
- Digest tampering, modified operation bodies, canonical digest calculation,
  and malformed or conflicting idempotency keys.
- Business changes before approval/execution, exhausted balances, changed
  prices, revoked initiator/reviewer permissions, and inactive reviewers.
- Concurrent submissions, pending deduplication, approvals, competing
  approve/reject decisions, multiple reviewers, and execution; one financial
  effect is asserted, not merely HTTP success.
- Lost submission/execution responses after real backend commits, safe explicit
  replay/reconciliation, malformed pending receipts, and no write auto-retries.
- Injected SQLite transaction/audit failures proving business and receipt
  rollback, subsequent explicit recovery, process recreation persistence, and
  legacy placeholder migration.
- Existing low-risk operations and all prior regressions, including the
  independent audit tests and Phase 3 tenant/RBAC/threshold checks.

Final verification:

```sh
mock-api/venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/ecommerce-phase4.xml
mock-api/venv/bin/python -m compileall -q dispatcher mock-api tests
git diff --check
```

Result: **499 passed, 0 failed, 0 errors, 0 skipped** (16.31 seconds): the
previous 415 cases plus 84 new cases. Compilation and whitespace checks pass.
JUnit output: `/tmp/ecommerce-phase4.xml`. No tests were deleted, skipped,
marked expected-failure, or relaxed to ignore financial/security failures.

### Remaining limitations and operational requirements

- Reviewers must be provisioned and separate human secrets kept out of Agent
  access. This mock uses configured bearer credentials, not a production human
  identity provider or approval UI. Seeded users are not automatically promoted.
- Idempotency is guaranteed for keyed submissions and approved execution IDs.
  Legacy unkeyed direct operations remain independent requests; callers must
  retain a stable key to safely replay an ambiguous direct write.
- Exactly-once effects cover this local SQLite transaction only. A future
  external payment provider needs its own idempotency/reconciliation protocol.
- Expiration is lazy on lifecycle access/action, with a configurable 1–86,400
  second TTL (default one hour); no background expiry worker was added.
- Original submission receipts do not change after approval completion; clients
  must query approval status. No automatic write retry or reconciliation worker
  was added.
- Database unavailability can also prevent writing a failure audit event;
  internal exception logs remain the fallback. Business and Dispatcher audit
  stores are separate transactions. No statement/branch coverage percentage
  was generated.
- Existing minimal role administration and its lack of a last-Admin safeguard
  are unchanged. Phase 5 integration remains intentionally unimplemented.

No commit or push was performed.
