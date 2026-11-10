# E-commerce OpenClaw Agent Platform: Phases 1–6A

The existing Flask Mock API now stores business data in SQLite using SQLAlchemy.
All 20 original GET routes, their response fields, filtering, and pagination remain
available. Phases 1–5 and the offline Phase 6A framework are implemented. OpenClaw uses a native typed
Tool Plugin that forwards authenticated calls to the provider-independent
Dispatcher; the four Skills no longer call Flask routes directly.

## Install and run

Python 3.10 or later is required; the repository's `.python-version` selects 3.10.14.

```sh
python3 -m venv mock-api/venv
mock-api/venv/bin/python -m pip install -r requirements-dev.txt
mock-api/venv/bin/python mock-api/app.py
```

The API listens on port 8080. `GET /healthz` remains public. For runtime-only
installation, use `requirements.txt`. After building/installing the plugin, copy
`.env.example` to `.env` and run `./start.sh` to launch the backend, Dispatcher,
and OpenClaw. The launcher gives each child only its required credentials; human
approval credentials are explicitly removed from the OpenClaw environment.

The default database is `mock-api/business.sqlite3`, resolved relative to the
application file regardless of the working directory. To choose another file:

```sh
export DATABASE_URL="sqlite:////absolute/path/business.sqlite3"
```

An application factory, `create_app(config)`, supports isolated test databases.
SQLite foreign keys are enabled. The eight tables requested in Phase 1 are
created automatically: Tenant, User, Product, Order, Refund, ApprovalRequest,
IdempotencyRecord, and AuditLog. The last three remain backend schema placeholders.
The dispatcher uses a separate execution audit database so rejected requests
without a valid tenant can also be recorded.

## Data and compatibility

On first initialization, two tenants receive distinct products, inventory,
customers, and 90 days of orders ending on the initialization date:

| Tenant | User | Default role | Product/order identifiers |
| --- | --- | --- | --- |
| `merchant-12345` | `user-a` | Operator | Original SKUs; `JDYYYYMMDDnnnn` orders |
| `merchant-67890` | `user-b` | Operator | `B-` prefixed SKUs and orders |

Seeding runs transactionally and skips tenants that already exist. Restarting
does not overwrite prices, refunds, orders, or customer snapshots. Reads query
stored records and never regenerate orders. Dates outside the seeded range
return empty results; advancing the calendar does not create more mock orders.
Unlike the original generator, order IDs are stable across date-range queries.

Order responses retain the original fields and add `refunded_amount` and
`remaining_refundable_amount`. Order items keep purchase-time prices when the
catalog price changes. Inventory is persisted on Product; customer fixtures and
aggregate snapshots are persisted on Tenant. Rankings, product sales counts,
and category sales counts remain synthetic demo calculations using the tenant's
persisted products. Order/revenue analytics use stored orders; revenue keeps
its existing gross-order-total semantics. Refund analytics include partial
refunds and actual recorded refund reasons, grouped by the order's creation date.

## Tenant context

Without configured tokens, the original demo tenant's GET APIs remain accessible
to existing Skills. Writes always require a bearer token. Configure a local
token-to-user map on the server to enable authenticated use:

```sh
export API_TOKENS='{"replace-with-a-secret-for-a":"user-a","replace-with-a-secret-for-b":"user-b"}'
mock-api/venv/bin/python mock-api/app.py
```

Use fresh, private token values. The map is server configuration, not a request
parameter. The backend resolves the user's tenant from the persisted User row;
inactive or missing users are rejected. Every business query is tenant-scoped,
including aggregates, inventory, and customer data. Foreign-tenant resources
return the same 404 as missing resources. Composite resource keys and a composite
refund-to-order foreign key preserve ownership even when tenants reuse an ID.
Body/query identity overrides are rejected; identity-like headers confer no access.

When tokens are configured, authentication is required for business reads by
default. `LEGACY_READS=false` explicitly enforces that behavior. Setting
`LEGACY_READS=true` explicitly exposes the original demo tenant's reads without
credentials for compatibility with unchanged Skills. That mode is suitable only
for public demo data; use strict mode for tenant isolation of private data.
Invalid supplied credentials never fall back to anonymous access.

This remains a local authentication adapter rather than production identity
infrastructure. RBAC, risk policies, approvals, and financial idempotency are
enforced in the trusted backend.

## Write APIs

All money is in integer cents; booleans, fractional values, numeric strings,
missing fields, and unexpected fields are rejected. Errors return
`{"code": "ERROR_CODE", "message": "..."}` with an appropriate HTTP status.
Existing GET not-found codes (`40401`, `40402`, `40403`) remain unchanged.

### Refund an order

```sh
export API_TOKEN='replace-with-a-secret-for-a'
curl -s -H "Authorization: Bearer $API_TOKEN" \
  'http://localhost:8080/v1/orders?status=paid'
export ORDER_ID='copy-an-order-id-from-the-response'
curl -s -X POST -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"amount":100,"reason":"Partial return"}' \
  "http://localhost:8080/v1/orders/$ORDER_ID/refund"
```

`amount` is required; `reason` is optional and limited to 500 characters.
Eligible statuses are `paid`, `shipped`, and `delivered`. The amount must be
positive and no greater than the remaining refundable balance. Partial refunds
retain the fulfillment status; a full refund sets status to `refunded`.
Success returns HTTP 201 with `refund` and updated `order` objects.

### Change a product price

```sh
curl -s -X PATCH -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"price":30000}' \
  http://localhost:8080/v1/products/SHOE-WH-42/price
```

`price` must be between 1 and 100,000,000 cents, inclusive. This is a fixed
Phase 1 business limit, also enforced by a database constraint. Success returns
HTTP 200 with `product` and `previous_price`.

| HTTP status | Write error codes |
| --- | --- |
| 400 | `INVALID_ARGUMENT`, `PRICE_OUT_OF_RANGE` |
| 401 | `UNAUTHENTICATED` |
| 403 | `PERMISSION_DENIED`, `APPROVAL_REQUIRED` |
| 404 | `RESOURCE_NOT_FOUND` |
| 409 | `INVALID_BUSINESS_STATE`, `REFUND_AMOUNT_EXCEEDED` |
| 409 | `IDEMPOTENCY_CONFLICT`, `APPROVAL_INVALID`, `APPROVAL_FAILED` |
| 500 | `INTERNAL_ERROR` |
| 503 | `DATABASE_BUSY` |

Writes use SQLite `BEGIN IMMEDIATE` transactions: balance checks, order updates,
and refund inserts commit together, and concurrent refunds cannot overdraw the
balance. Errors roll back changes. Add a stable `idempotency_key` JSON field or
`Idempotency-Key` header to financial submissions to deduplicate them. Unkeyed
legacy calls still represent new operations on every explicit invocation.
These endpoints record local business state only; they do not call a payment
provider.

## Phase 2: Controlled Tool Dispatcher

The execution path is:

```text
Caller → Dispatcher → authenticated Flask API → business SQLite
             ↓
       execution audit SQLite
```

`dispatcher/dispatcher.py` is a Python library independent of any LLM provider.
`dispatcher/gateway.py` adds a small Flask HTTP adapter. There is no Agent
framework or model SDK. The backend owns approval and idempotency enforcement.

### Start the gateway

Keep the backend running on port 8080 with `API_TOKENS` configured. In a second
terminal, from the repository root:

```sh
mock-api/venv/bin/python -m pip install -r requirements-dev.txt
mock-api/venv/bin/python -m dispatcher
```

The gateway listens on `127.0.0.1:8081`. Optional environment settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISPATCHER_BACKEND_URL` | `http://127.0.0.1:8080` | Fixed trusted backend origin |
| `DISPATCHER_AUDIT_DB` | `dispatcher/audit.sqlite3` | Persistent dispatcher audit file |

The backend origin is server configuration and cannot be supplied in tool
arguments. Redirects and environment proxy settings are disabled for backend
requests. Requests time out after five seconds by default. `create_app(config)`
also supports `BACKEND_TIMEOUT` (up to 60 seconds) and `MAX_RETRIES` (0–2).
Use HTTPS when the backend is not on a trusted local connection.

`GET /tools` exposes descriptions, required permissions, risk metadata, HTTP
methods/endpoints, and Pydantic-generated input/output JSON Schemas.
`GET /healthz` is a public liveness check. Execute a tool with:

```sh
curl -s http://127.0.0.1:8081/tools | jq '.tools[] | {name, required_permissions, risk_level}'
curl -s -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"tool":"list_orders","arguments":{"status":"paid","page_size":5}}' \
  http://127.0.0.1:8081/tools/execute
```

The credential is separate from the tool arguments. The dispatcher always
requires authentication, even if the backend allows legacy anonymous reads.
It obtains user ID, tenant ID, and capabilities from the new authenticated
`GET /v1/auth/context` backend route, then forwards the same bearer token on
every precheck and business request. There is no privileged fallback token and
no dispatcher access to the business database.

The context endpoint reports the persisted role and its server-derived
permissions; the dispatcher checks each registry entry's required permissions.
The backend independently rechecks authentication, role permissions, tenant
ownership, business rules, and risk policy on every request, including changes
after dispatcher prechecks.

### Registered tools

The registry contains 24 tools. Human approve/reject endpoints are intentionally
not Agent tools; health and authentication context are infrastructure endpoints.

| Area | Tool names |
| --- | --- |
| Orders | `list_orders`, `get_order`, `order_stats`, `refund_order` |
| Products | `list_products`, `product_ranking`, `product_stats`, `change_price` |
| Inventory | `inventory_summary`, `inventory_alerts`, `inventory_sku`, `restock_suggestions` |
| Customers | `customers_summary`, `customers_retention`, `customers_geo`, `list_customers` |
| Analytics | `analytics_revenue`, `analytics_trend`, `analytics_refunds`, `analytics_category`, `analytics_aov` |
| Administration | `change_user_role` |
| Approvals | `get_approval`, `execute_approval` |

Read tools require `business:read` and have LOW risk metadata. Refunds require
`orders:refund`; price updates require `products:price`; both also require read
permission for prechecks and have DYNAMIC risk metadata. Role changes require
`roles:manage` and have HIGH risk metadata.

### Phase 3 RBAC and risk policy

Viewer is read-only. Operator can submit refunds and price changes. Manager adds
manager-level approval authority, and Admin adds admin-level approval authority
plus tenant-scoped role management. Approval authority never proves that an
operation was approved. Admin cannot access another
tenant's users or business resources.

Refunds through ¥100 (10,000 cents) execute directly for Operator, Manager, or
Admin. Refunds over ¥100 through ¥500 require Manager/Admin approval; larger
refunds require Admin approval. Absolute price changes through 5% execute
directly; changes over 5% through 20% require Manager/Admin approval; larger
changes require Admin approval. Price percentages use exact integer comparisons.

Every approval-required submission returns `APPROVAL_REQUIRED` with a persisted
PENDING receipt, including submissions initiated by Manager or Admin. Business
state and amount constraints are checked first. A separate authorized human must
approve before the initiator can execute; the backend does not accept
client-supplied approval claims on ordinary refund/price endpoints.

### Validation and observations

1. Strict Pydantic models reject missing fields, unexpected arguments, type
   coercion, invalid enum values, dates, IDs, and numeric bounds. The dispatcher
   does not clamp prices, lower refund amounts, or guess corrected arguments.
2. Authenticated backend context establishes permissions. Tenant-scoped GET
   prechecks verify write targets, refund status and remaining balance. These
   checks are advisory; the backend transaction remains authoritative.
3. Response validation checks HTTP status, JSON content, explicit error codes
   (including errors returned with HTTP 200), nested output schemas, resource
   IDs, pagination, requested periods, money balances, and write results.
   Refund and price results must match the requested amounts. A refund result
   may include concurrent refunds beyond its precheck snapshot; its own refund
   amount must still match exactly.

Every execution returns the same observation shape:

```json
{
  "success": false,
  "tool": "refund_order",
  "error": {
    "code": "INVALID_ARGUMENT",
    "category": "VALIDATION",
    "message": "Arguments do not match the tool schema",
    "recoverable": true,
    "retry_hint": "Check the tool input_schema and submit corrected arguments; amounts are integer cents."
  },
  "data": null,
  "trace_id": "generated-uuid"
}
```

Successful observations contain validated `data` and `error: null`. A fresh UUID
is generated for each call, returned in `X-Request-ID`, and forwarded to every
backend request. Client-supplied trace IDs and identity claims are not trusted.
Legacy backend numeric not-found codes normalize to `RESOURCE_NOT_FOUND`.
Foreign-tenant resources remain indistinguishable from missing resources.

### Retry and audit behavior

The default retry budget is **two additional requests per execution**, shared
across authentication, prechecks, and the business read. Only GET requests with
an explicit transient timeout/unavailability/database-busy error are retried,
using 0.1- and 0.2-second backoff. Generic internal errors, malformed successful
responses, authorization failures, and business state failures are not retried.
`recoverable: true` can mean that a caller may submit corrected arguments; it
does not by itself trigger automatic retries. No parameter repair is automatic.

POST/PATCH requests are sent at most once per execution. A lost response,
infrastructure error, invalid write response, or failed postcondition returns
non-recoverable `WRITE_OUTCOME_UNKNOWN`. The caller must inspect persisted state
and resolve the outcome manually before another write. Known business rejections
retain their specific codes. Approval-required results are non-recoverable and
include the validated pending receipt in `error.approval`.

`dispatcher/audit.py` records one durable `tool_executions` row per invocation,
including validation/authentication failures. It stores the trace, registered
tool, trusted identity when known, timestamps, request attempts, and final result
code. Tokens, raw arguments, customer response bodies, and raw upstream errors
are excluded. An initial row is required before any backend access; a durable
identity/target checkpoint is required before a write. If the audit store fails,
execution fails closed; if completion recording fails after a write, the initial
row/checkpoint remains pending and the response does not encourage retrying.
The audit is not a deduplication mechanism or an atomic transaction spanning
both services. Pending rows require manual investigation.

### Operational limitations

- Existing direct Flask routes remain accessible to host processes and remain
  independently authenticated/authorized. Phase 5 supplies a restrictive
  OpenClaw tool-policy example, but local process isolation is not a host firewall.
- Production identity infrastructure, an approval UI, automatic reconciliation,
  and LLM-specific integration are not implemented.
- Unknown writes are never automatically retried. Inspect a persisted approval
  with `get_approval`; explicitly resubmit keyed submissions only with the
  original key and parameters. Unkeyed financial calls cannot be deduplicated.
- Schemas intentionally constrain supported tool inputs more strictly than
  legacy Flask query parsing (for example, page sizes are capped at 100 and the
  existing daily revenue/monthly retention calculations expose only those periods).
- Business and audit databases are separate. An unavailable audit store prevents
  new execution; an unavailable completion write can leave a pending audit row.

## Phase 4: Approvals and idempotency

HIGH refunds and price changes return HTTP 403 with `code: APPROVAL_REQUIRED`
and an `approval` object. The Dispatcher exposes the same object under
`error.approval`. It includes the approval ID, operation ID, exact normalized
parameters, required approval level, digest, and expiry. Submission never applies
the financial change. The original HTTP 403 code remains compatible with Phase 3.

The lifecycle is PENDING → APPROVED → EXECUTING → COMPLETED, with REJECTED,
EXPIRED, and FAILED terminal alternatives. A separate human credential and the
current required role are necessary to approve/reject. Self-review and foreign
tenant review are forbidden. API credentials cannot approve even when their user
is an Admin. Keep human credentials outside the Agent environment.

Provision reviewer users in the same tenant using trusted local administration;
the seeded users remain Operators and are never automatically promoted. Configure
distinct token-to-user maps on the backend, for example:

```sh
export API_TOKENS='{"private-agent-token":"user-a"}'
export HUMAN_APPROVAL_TOKENS='{"private-review-token":"reviewer-user-id"}'
```

`reviewer-user-id` must be an existing active Manager/Admin in the database. Tokens
must not overlap between maps. There are no built-in human passwords or tokens.

| Endpoint | Caller | Behavior |
| --- | --- | --- |
| `GET /v1/approvals/{id}` | Initiator or same-tenant reviewer | Inspect and reconcile lifecycle/result |
| `POST /v1/approvals/{id}/approve` | Authorized human reviewer | Persist approval without executing |
| `POST /v1/approvals/{id}/reject` | Authorized human reviewer | Persist terminal rejection |
| `POST /v1/approvals/{id}/execute` | Initiator | Revalidate and execute once, or replay stored result |

POST action bodies are `{}`; they cannot replace approved parameters. Only GET
inspection and POST execution are registered Dispatcher tools. Example review:

```sh
curl -s -X POST -H "Authorization: Bearer $HUMAN_APPROVAL_TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  "http://localhost:8080/v1/approvals/$APPROVAL_ID/approve"
```

Canonical SHA-256 binding covers tenant, initiator, operation ID, operation,
resource, parameters, approval level, and expiry. Price approvals also bind the
starting price: any intervening price change requires a new approval. Current
initiator permissions and reviewer authority are checked again at execution.
Business-state changes and digest failures record FAILED with no financial effect.
Expiry defaults to one hour (`APPROVAL_TTL_SECONDS` in app configuration, 1–86400);
it is persisted on inspection or action, without a background scheduler.

Keyed financial submissions atomically persist the original response. Repeating
the same key/initiator/parameters returns that response, including the original
pending receipt. To see subsequent approval changes, inspect the approval ID.
Changed parameters or another initiator under the same tenant/key cause
`IDEMPOTENCY_CONFLICT`. Keys are retained across restarts without automatic expiry.

The approval ID is the execution idempotency key. SQLite `BEGIN IMMEDIATE`
serializes concurrent decisions/executions. Business writes, completed results,
and backend lifecycle audits commit together. EXECUTING is a transaction-local
transition; a database failure rolls the approval back to APPROVED, and an explicit
later execution can safely recover. A lost successful response leaves COMPLETED
and its result available for inspection and replay. The Dispatcher never retries
these writes automatically, including when it reports `WRITE_OUTCOME_UNKNOWN`.

SQLite startup migration preserves existing data, adds approval/idempotency
columns, and marks legacy unbound placeholder approvals FAILED. The exactly-once
guarantee covers local SQLite financial effects; a future external payment
provider will require its own idempotency/reconciliation protocol.

## Phase 5: OpenClaw Tool Adapter

The local execution path is:

```text
User → OpenClaw Agent → TypeScript Tool Plugin → Dispatcher :8081
     → authenticated Flask Backend :8080 → business SQLite
```

OpenClaw 2026.7.1 supports native typed Tool Plugins, so no CLI fallback or new
Agent framework is used. `openclaw-plugin/` uses `defineToolPlugin`; a generator
loads `dispatcher.registry.REGISTRY` and produces TypeScript metadata and input
JSON Schemas. The generated file is not a second manually maintained registry.
Run the generator whenever registry metadata changes:

```sh
mock-api/venv/bin/python scripts/generate_openclaw_tools.py
```

The plugin registers the current 24 Dispatcher tools: the 22 Phase 3 business
tools plus Phase 4 `get_approval` and `execute_approval`. Human `approve` and
`reject` endpoints are deliberately absent. POST/PATCH tools are optional in
OpenClaw and require explicit operator allowlisting. Agent financial schemas
strengthen the backward-compatible backend contract by requiring
`idempotency_key`; this closes the Phase 4 audit's unkeyed Agent retry gap.

### Build, install, and verify without an LLM

Node 22.22.3+ is required by OpenClaw; the tested local version is Node 26.4.0.

```sh
cd openclaw-plugin
npm install
npm run plugin:validate
npm test
cd ..
openclaw plugins install ./openclaw-plugin
openclaw plugins inspect ecommerce-dispatcher --runtime --json
```

Installation changes the selected OpenClaw profile, so perform it explicitly.
For an isolated profile, prefix both OpenClaw commands with
`openclaw --profile ecommerce-agent`. This implementation did not modify
`~/.openclaw/openclaw.json`; runtime loading was verified using a temporary
state/config directory.

### Start the local stack

Use separate terminals. Credentials are runtime configuration and must never be
put in a prompt, Skill, tool argument, source file, or log.

```sh
# Terminal 1: trusted backend process
export API_TOKENS='{"replace-agent-token":"user-a"}'
export HUMAN_APPROVAL_TOKENS='{"replace-human-token":"existing-manager-user"}'
export LEGACY_READS=false
mock-api/venv/bin/python mock-api/app.py

# Terminal 2: Dispatcher (has no fallback or privileged token)
export DISPATCHER_BACKEND_URL='http://127.0.0.1:8080'
mock-api/venv/bin/python -m dispatcher

# Terminal 3: OpenClaw runtime; only the Agent credential belongs here
export OPENCLAW_ECOMMERCE_API_TOKEN='replace-agent-token'
export OPENCLAW_ECOMMERCE_DISPATCHER_URL='http://127.0.0.1:8081'
export OPENCLAW_ECOMMERCE_TIMEOUT_MS=7000
openclaw gateway run
```

The adapter accepts only a credential-free loopback HTTP(S) Dispatcher origin.
It makes one request per tool invocation, disables redirects, observes a bounded
timeout, validates the structured Observation, and preserves Dispatcher error
codes, recoverability, retry hints, approval receipts, and trace IDs. It never
retries writes or reads; only the existing Dispatcher may perform its bounded
backend retry policy for GET requests. `WRITE_OUTCOME_UNKNOWN` must be reconciled
using its original idempotency key and trace ID, never blindly replayed.

Example Agent requests after a model becomes available:

- “Look up order PHASE5-ORDER.” → `get_order {"order_id":"PHASE5-ORDER"}`
- “Show low-stock products.” → `inventory_alerts {"type":"low_stock"}`
- “Refund 200 yuan from order PHASE5-ORDER for a return.” → first inspect the
  order, then call `refund_order` with 20000 cents and a stable idempotency key.

### Security boundary and tool policy

The runtime token maps to exactly one persisted backend user/tenant. Identity is
not in any tool schema, and the backend remains the final RBAC, tenant, risk,
approval, and idempotency boundary. Human approval tokens are configured only in
the backend and are never available to OpenClaw. `execute_approval` can execute
only an independently approved request as its original initiator; it cannot make
the human decision.

Apply the settings in `config/openclaw-secure-agent.example.json` to the selected
Agent/profile before production-style use. The example enables the plugin while
denying runtime/exec, filesystem, web, browser/UI, node, and Gateway tools and
enables sandboxing. OpenClaw configuration validation passes for this file. Tool
instructions alone are not treated as a security boundary.

Complete network isolation cannot be guaranteed in this local single-host demo:
the Flask port is still reachable by other host processes, native plugins share
the Gateway process, and an operator can weaken tool policy or install other
plugins. The tested Agent configuration has no supported exec/web/browser tool
surface for directly reaching Flask; use container/network firewall rules in a
deployment that needs a hard egress boundary. The backend's independent checks
remain mandatory even with network isolation.

### Phase 5 verification status

Verified locally without any LLM call: schema generation, all 24 registrations,
OpenClaw manifest validation, actual OpenClaw runtime plugin loading, adapter
timeouts/malformed responses, read-only proof-of-concept calls, structured error
and trace propagation, real Dispatcher/Flask/SQLite execution, trusted token
propagation, RBAC and cross-tenant denial, approval separation, keyed replay,
single execution, and backend unavailability.

Not yet verified because the configured DashScope quota returns HTTP 403: model
tool selection, natural-language parameter extraction, Agent reasoning, and
Observation-driven self-correction. No DashScope/OpenAI API was called, billing
was not changed, and no model fallback was configured.

## Phase 6A: automated offline evaluation

`evaluations/` implements the JSON-driven evaluation pipeline:

```text
72-case dataset → runner → offline replay adapter → Dispatcher/backend/SQLite
                                        ↓
                              graders → metrics / Bad Cases
```

There are 12 independently reproducible cases in each of six categories: tool
selection, argument extraction, context isolation, prompt injection defense,
approval bypass defense, and cross-tenant defense. Every case validates against
the generated formal JSON Schema. Each case receives a fresh temporary business
database and Dispatcher audit database. Trusted identity aliases are resolved by
the runner; dataset or Agent parameters cannot supply credentials or override a
tenant.

Run the offline Baseline and Improved configurations against the identical
dataset:

```sh
mock-api/venv/bin/python -m evaluations.runner \
  --mode offline --configuration both --report-dir evaluation-results
```

Use `--category`, `--case`, or `--rerun-failed` for focused runs. Reports are
machine-readable JSON plus Markdown, with sanitized JSONL Bad Cases. Offline
decisions are deterministic replay fixtures. Their results validate evaluation
infrastructure and backend enforcement; they are never reported as real Agent
performance. Real-model cases remain `REQUIRES_LLM` until Phase 6B is explicitly
authorized and run.

The future adapter is double-gated by `--allow-real-agent` and
`OPENCLAW_EVAL_ENABLE_REAL_AGENT=1`, requires an explicit model, and fails if
OpenClaw cannot provide reliable tool-call traces. See
[`evaluations/README.md`](evaluations/README.md) for dataset details, report
semantics, cost/turn controls, and the unexecuted Phase 6B workflow.

## Tests

```sh
mock-api/venv/bin/python -m pytest -q
mock-api/venv/bin/python -m pytest -q tests/test_phase6_evaluations.py

cd openclaw-plugin
npm run plugin:check
npm test
```

Tests use temporary SQLite files and cover legacy GET compatibility, business
validation, authenticated reads/writes, tenant isolation (including reused IDs),
database constraints, partial/full refunds, current catalog prices versus
historical order prices, restart persistence, rollback on database failures,
and concurrent refunds. Phase 6 tests additionally cover dataset validation,
isolated database resets, graders, negative traces, security assertions, Bad
Case replay, configuration separation, metrics, and real-mode gating.

Dispatcher tests additionally cover all registered tools for both tenants via
HTTPX's WSGI transport into the real Flask backend, generated JSON Schemas using
an independent JSON Schema validator, malformed input/output, tenant and
permission denials, explicit HTTP-200 business failures, bounded retries, lost
write responses, backend rechecks after preflight, concurrent caller isolation,
durable audits, audit failures, and gateway/HTTP transport behavior.

`tests/test_phase5_openclaw.py` compiles the adapter and uses local Node direct
tool invocation against real loopback Dispatcher and backend WSGI servers. It
does not invoke an LLM. In restricted sandboxes, the test command needs permission
to bind ephemeral `127.0.0.1` ports.
