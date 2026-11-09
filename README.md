# E-commerce OpenClaw: Phases 1 and 2

The existing Flask Mock API now stores business data in SQLite using SQLAlchemy.
All 20 original GET routes, their response fields, filtering, and pagination remain
available. The four OpenClaw Skills still use `curl "$JD_API_ENDPOINT/..." | jq`.
Phases 1 and 2 of `plan.md` are implemented. The provider-independent dispatcher
adds a controlled execution path without changing the existing Skills.

## Install and run

Python 3.10 or later is required; the repository's `.python-version` selects 3.10.14.

```sh
python3 -m venv mock-api/venv
mock-api/venv/bin/python -m pip install -r requirements-dev.txt
mock-api/venv/bin/python mock-api/app.py
```

The API listens on port 8080. `GET /healthz` remains public. For runtime-only
installation, use `requirements.txt`. The existing `start.sh` can launch the API
and OpenClaw after dependencies are installed; it also requires OpenClaw and the
existing `.env` configuration (`JD_API_ENDPOINT`, `JD_SHOP_NAME`).

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
infrastructure. Phase 3 RBAC and risk policies are enforced in the trusted
backend. Approval execution and idempotency remain deferred.

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
  -H 'Content-Type: application/json' -d '{"price":27900}' \
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
| 500 | `INTERNAL_ERROR` |
| 503 | `DATABASE_BUSY` |

Writes use SQLite `BEGIN IMMEDIATE` transactions: balance checks, order updates,
and refund inserts commit together, and concurrent refunds cannot overdraw the
balance. Errors roll back changes. This does **not** provide request idempotency:
retrying a successful partial refund can create another refund. Approval and
idempotency workflows are deferred to later phases, along with Skill migration
and the evaluation framework. These endpoints record local business
state only; they do not call a payment provider.

## Phase 2: Controlled Tool Dispatcher

The execution path is:

```text
Caller → Dispatcher → authenticated Flask API → business SQLite
             ↓
       execution audit SQLite
```

`dispatcher/dispatcher.py` is a Python library independent of any LLM provider.
`dispatcher/gateway.py` adds a small Flask HTTP adapter. There is no Agent
framework, model SDK, approval workflow, or idempotency implementation.

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

All 22 business endpoints are registered; health and authentication
context are infrastructure endpoints, not business tools.

| Area | Tool names |
| --- | --- |
| Orders | `list_orders`, `get_order`, `order_stats`, `refund_order` |
| Products | `list_products`, `product_ranking`, `product_stats`, `change_price` |
| Inventory | `inventory_summary`, `inventory_alerts`, `inventory_sku`, `restock_suggestions` |
| Customers | `customers_summary`, `customers_retention`, `customers_geo`, `list_customers` |
| Analytics | `analytics_revenue`, `analytics_trend`, `analytics_refunds`, `analytics_category`, `analytics_aov` |
| Administration | `change_user_role` |

Read tools require `business:read` and have LOW risk metadata. Refunds require
`orders:refund`; price updates require `products:price`; both also require read
permission for prechecks and have DYNAMIC risk metadata. Role changes require
`roles:manage` and have HIGH risk metadata.

### Phase 3 RBAC and risk policy

Viewer is read-only. Operator can submit refunds and price changes. Manager adds
manager-level approval authority, and Admin adds admin-level approval authority
plus tenant-scoped role management. Approval authority is metadata for Phase 4;
it never proves that an operation was approved. Admin cannot access another
tenant's users or business resources.

Refunds through ¥100 (10,000 cents) execute directly for Operator, Manager, or
Admin. Refunds over ¥100 through ¥500 require Manager/Admin approval; larger
refunds require Admin approval. Absolute price changes through 5% execute
directly; changes over 5% through 20% require Manager/Admin approval; larger
changes require Admin approval. Price percentages use exact integer comparisons.

Because approval execution is not implemented yet, every approval-required
operation fails closed with `APPROVAL_REQUIRED`, including requests initiated by
Manager or Admin. Business state and amount constraints are checked first, so an
approval requirement cannot make an invalid operation valid. The backend does
not accept client-supplied approval claims.

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
do not implement approval processing.

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

### Phase 2 limitations

- Existing direct Flask routes remain accessible and independently authorized;
  Agent network restriction and Skill migration belong to later phases.
- No approval execution, requester/approver workflow, idempotency, automatic
  reconciliation, production identity provider, or LLM-specific integration is
  implemented. Approval-required operations therefore fail closed.
- Unknown write outcomes cannot be resolved automatically without the later
  idempotency work. Never blindly retry a partial refund.
- Schemas intentionally constrain supported tool inputs more strictly than
  legacy Flask query parsing (for example, page sizes are capped at 100 and the
  existing daily revenue/monthly retention calculations expose only those periods).
- Business and audit databases are separate. An unavailable audit store prevents
  new execution; an unavailable completion write can leave a pending audit row.

## Tests

```sh
mock-api/venv/bin/python -m pytest -q
```

Tests use temporary SQLite files and cover legacy GET compatibility, business
validation, authenticated reads/writes, tenant isolation (including reused IDs),
database constraints, partial/full refunds, current catalog prices versus
historical order prices, restart persistence, rollback on database failures,
and concurrent refunds. These are backend unit/integration tests, not the
later-phase Agent evaluation framework.

Dispatcher tests additionally cover all registered tools for both tenants via
HTTPX's WSGI transport into the real Flask backend, generated JSON Schemas using
an independent JSON Schema validator, malformed input/output, tenant and
permission denials, explicit HTTP-200 business failures, bounded retries, lost
write responses, backend rechecks after preflight, concurrent caller isolation,
durable audits, audit failures, and gateway/HTTP transport behavior.
