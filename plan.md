# E-commerce OpenClaw Agent Platform — Implementation Plan

## 0. Project Context

This project currently contains:

- `mock-api/app.py`: Flask-based e-commerce Mock API with approximately 20 read-only endpoints.
- `skills/platform/`: Four OpenClaw Skills (`sales-query`, `product-ranking`, `inventory-alert`, `customer-insight`).
- Mock business data for orders, products, inventory, customers, and sales analytics.

The current architecture is:

`OpenClaw Skills → curl → Flask Mock API`

Our target architecture is:

`OpenClaw → Controlled Tool Dispatcher → Flask Business API → SQLite`

The main objective is to implement **reliable and secure AI Agent tool execution**, including structured validation, error recovery, multi-tenant authorization, human approval, idempotency, and automated evaluation.

Do not rewrite the project from scratch. Reuse existing code and implement changes incrementally.

---

## 1. Architecture and Design Principles

### 1.1 Components

**OpenClaw Agent**
- Understands user requests.
- Selects appropriate tools.
- Receives structured observations.
- May correct invalid parameters and retry recoverable requests.
- Cannot directly execute sensitive business APIs.

**Tool Dispatcher / Gateway**
- Central entry point for all business tools.
- Maintains a Tool Registry.
- Validates input JSON Schema.
- Authenticates and authorizes operations.
- Enforces risk policies and approval requirements.
- Invokes Flask Backend APIs.
- Validates API responses.
- Returns structured observations.

**Flask Business Backend**
- Preserves existing read-only APIs.
- Adds refund and product price modification endpoints.
- Uses SQLite for persistent business state.
- Independently enforces tenant isolation and authorization.
- Does not trust authorization decisions made by the LLM.

**Approval Service**
- Handles high-risk operations.
- Persists pending approval requests.
- Verifies approved operation parameters.
- Supports approve/reject/expire states.
- Ensures each approved operation is executed at most once.

**Evaluation Framework**
- Tests agent behavior, validation, authorization, approval and recovery.
- Stores failed cases.
- Supports baseline and improved configurations.

### 1.2 Non-negotiable Principles

1. Never trust LLM-generated parameters or identity claims.
2. Tenant identity must come from authenticated execution context, not tool arguments.
3. Backend authorization cannot be bypassed by calling APIs directly.
4. Approval decisions must be made by authorized humans, not by the Agent.
5. Once an operation has been approved, its parameters cannot be modified.
6. Retries must not duplicate business side effects.
7. Business failures must be distinguished from infrastructure failures.
8. Existing API compatibility should be maintained where practical.
9. Do not claim security or success-rate improvements without passing tests.

---

## Phase 1 — Refactor Mock API and Add Persistent Business Operations

### Tasks

1. Refactor `mock-api/app.py` into smaller modules if necessary, while preserving existing GET routes.
2. Introduce SQLite with SQLAlchemy.
3. Define persistent models:
   - Tenant
   - User
   - Product
   - Order
   - Refund
   - ApprovalRequest
   - IdempotencyRecord
   - AuditLog
4. Seed at least two tenants with distinct orders/products.
5. Add two write operations:
   - `POST /v1/orders/{order_id}/refund`
   - `PATCH /v1/products/{sku}/price`
6. Add business validation:
   - Order must exist and belong to the tenant.
   - Refund amount must be positive and not exceed the remaining refundable amount.
   - Order status must permit refunds.
   - Product must exist and belong to the tenant.
   - New price must satisfy business limits.
7. Ensure write operations use database transactions.

Existing read APIs should remain functional. Business records used by sensitive operations must share the same persistent source of truth.

### Acceptance Criteria

- Existing read APIs still work.
- Refund updates the correct order/refund records.
- Price updates persist in SQLite.
- Invalid business operations return meaningful error codes.
- Tenant A cannot read or modify Tenant B's records.
- Restarting Flask does not reset persisted changes.

---

## Phase 2 — Implement Controlled Tool Dispatcher

This is the main component of the project.

### 2.1 Tool Registry

Create a centralized registry that defines each tool with:

- Tool name
- Description
- Input schema
- Output schema
- Required permissions
- Risk level
- HTTP method / Backend endpoint

Register existing business APIs as tools.

Prefer Pydantic models and automatically generated JSON Schema.

### 2.2 Layered Validation

Implement three layers:

**Layer 1: Input Schema Validation**
- Required fields
- Data types
- Allowed enum values
- Numeric constraints
- Invalid/unexpected parameters

**Layer 2: Business and Authorization Validation**
- Tenant ownership
- User permissions
- Entity existence
- Business state constraints
- Risk rules
- Approval requirements

**Layer 3: Response Validation**
- HTTP response status
- JSON structure
- Required result fields
- Business success/failure code
- Expected post-operation state where applicable

Do not assume an HTTP 200 response always indicates business success.

### 2.3 Structured Observation

Standardize tool execution responses:

```json
{
  "success": false,
  "tool": "get_order",
  "error": {
    "code": "INVALID_ARGUMENT",
    "category": "VALIDATION",
    "message": "Invalid order_id format",
    "recoverable": true,
    "retry_hint": "Use a valid order ID"
  },
  "data": null,
  "trace_id": "example-trace-id"
}
```

Possible error codes:

- INVALID_ARGUMENT
- RESOURCE_NOT_FOUND
- INVALID_BUSINESS_STATE
- PERMISSION_DENIED
- TENANT_ACCESS_DENIED
- APPROVAL_REQUIRED
- APPROVAL_REJECTED
- APPROVAL_EXPIRED
- UPSTREAM_TIMEOUT
- INTERNAL_ERROR

### 2.4 Error Recovery

Implement bounded retry policies:

- Recoverable parameter errors may be returned to the Agent for correction.
- Transient infrastructure errors may use limited backoff/retry.
- Permission denial and approval rejection must not trigger automatic retries.
- Unknown outcomes of write operations must be resolved using the idempotency record before retrying.
- Set an explicit maximum retry count, e.g. 2.

The Dispatcher should not silently change user-requested financial amounts.

### Acceptance Criteria

- All registered tools enforce input and output schemas.
- Invalid arguments generate structured errors.
- Recoverable failures can be corrected by the Agent.
- Non-recoverable failures terminate correctly.
- Every tool execution generates a trace ID and audit entry.

---

## Phase 3 — Authentication, Authorization and Risk Policy

### 3.1 Authentication Context

Represent each request with a trusted execution context containing:

- user_id
- tenant_id
- role
- request_id

For local development, use a mock authentication provider or signed local tokens.

Do not accept `tenant_id` supplied by the Agent as authoritative.

### 3.2 Authorization

Implement RBAC and parameter-level permission checks using this formal matrix:

| Role | `business:read` | `orders:refund` | `products:price` | `approvals:manager` | `approvals:admin` | `roles:manage` |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Viewer | Yes | No | No | No | No | No |
| Operator | Yes | Yes | Yes | No | No | No |
| Manager | Yes | Yes | Yes | Yes | No | No |
| Admin | Yes | Yes | Yes | Yes | Yes | Yes |

`orders:refund` and `products:price` authorize submitting the operation. They do
not bypass risk classification or prove that an approval exists. Manager and
Admin approval permissions describe who may approve in Phase 4; they have no
approval effect in Phase 3. Role administration is tenant-scoped and reserved
to Admin. It changes role assignment only; it cannot create or imply an
operation approval.

Every authenticated role may use tenant-scoped reads. Viewer is read-only.
Operator, Manager, and Admin may directly execute only operations classified as
MEDIUM. Cross-tenant access is always forbidden, including for Admin.

### 3.3 Risk Policy Engine

Classify operations:

- LOW: read-only operations
- MEDIUM: limited business changes
- HIGH: refunds, significant price modifications

A risk policy determines whether an operation can execute immediately or must wait for approval.

Money is represented as integer cents. The policy thresholds are inclusive and
must be implemented without binary floating-point comparisons.

| Operation | Boundary | Risk | Required permission | Approval requirement in Phase 4 | Phase 3 behavior |
|---|---|---|---|---|---|
| Refund | `1..10000` cents (up to and including ¥100) | MEDIUM | `orders:refund` | None | Execute after all business checks pass |
| Refund | `10001..50000` cents (over ¥100 through ¥500) | HIGH | `orders:refund` | Manager or Admin | Fail closed with `APPROVAL_REQUIRED` |
| Refund | More than `50000` cents (over ¥500) | HIGH | `orders:refund` | Admin only | Fail closed with `APPROVAL_REQUIRED` |
| Price change | Absolute change at most 5%, inclusive | MEDIUM | `products:price` | None | Execute after all business checks pass |
| Price change | Absolute change over 5% through 20%, inclusive | HIGH | `products:price` | Manager or Admin | Fail closed with `APPROVAL_REQUIRED` |
| Price change | Absolute change over 20% | HIGH | `products:price` | Admin only | Fail closed with `APPROVAL_REQUIRED` |
| Role change | Valid same-tenant user and role | HIGH | `roles:manage` | None specified | Admin may execute directly |

For a current price `old` and requested price `new`, compare
`abs(new - old) * 100` with `old * 5` and `old * 20`. This preserves exact
integer boundary behavior for both increases and decreases.

### 3.4 Policy Precedence and Security Boundaries

The trusted backend applies checks in this order:

1. Authenticate the bearer token and load the active database user.
2. Derive `user_id`, `tenant_id`, role, and permissions from that user; ignore
   identity or role claims in headers and reject such claims in arguments.
3. Validate request structure and integer-cent parameter limits.
4. Verify the role permission required to submit the operation.
5. Resolve the resource using the authenticated tenant key. A resource in a
   different tenant is unavailable and cannot be selected by an Admin.
6. Recheck current business state and limits, including refundable balance,
   product price constraints, and eligible order status.
7. Classify risk using the exact boundaries above.
8. Execute MEDIUM operations directly. For HIGH operations, require the exact
   approval level from the table and fail closed with `APPROVAL_REQUIRED` until
   Phase 4 supplies a valid, parameter-bound approval.

Business-invalid operations take precedence over approval requirements once the
resource is authorized and loaded. Approval authority never implies approval,
and a requester will not be allowed to approve their own sensitive operation in
Phase 4. Approval fields supplied early by clients are invalid arguments. No
write operation is automatically retried, including approval-required requests
and unknown write outcomes.

Risk classification and permission checks must happen in trusted backend code, never solely in prompts.

### Acceptance Criteria

- Unauthorized tool calls are rejected.
- Tenant isolation is enforced across reads and writes.
- Exact refund and price-change thresholds are covered by automated boundary tests.
- High-risk actions fail closed and cannot execute without approval.
- Manager/Admin authority alone cannot satisfy an approval requirement.
- Business-invalid actions remain rejected before risk/approval decisions.
- Direct backend requests cannot bypass policy enforcement.
- Existing no-automatic-write-retry behavior remains intact.

---

## Phase 4 — Human-in-the-Loop Approval Flow

Phase 4 supersedes the Phase 3 fail-closed placeholder: the same HIGH thresholds
now persist a PENDING approval while continuing to prohibit immediate execution.
The role matrix and integer-cent/percentage boundaries above remain unchanged.

### 4.1 Approval Workflow

Expected flow:

1. Agent requests a high-risk tool operation.
2. Dispatcher validates authentication, permissions, schema and business conditions.
3. Risk engine determines approval is required.
4. System creates a pending approval request.
5. Agent receives `APPROVAL_REQUIRED` with an approval ID.
6. Authorized human reviews and approves/rejects.
7. Backend verifies the approval and its original parameters.
8. Backend rechecks current permissions and business state.
9. Operation executes exactly once from the approved request record.
10. Audit log records the outcome.

### 4.2 Request Digest

Create a canonical representation of the operation:

- tenant_id
- initiator user_id
- tool name
- resource ID
- validated parameters
- operation ID

Generate a SHA-256 request digest.

The approval must bind to this exact operation. Changing the refund amount, product ID, tenant, or other protected parameters invalidates the approval.

### 4.3 Idempotency

Implement idempotency keys for sensitive write operations.

Requirements:

- Unique operation identity.
- Atomic state transition from pending to executing/completed.
- Duplicate requests return the original recorded outcome.
- Same idempotency key with different parameters is rejected.
- Concurrent duplicate requests cannot execute twice.
- Failed or timed-out requests must not cause duplicate refunds.

Implementation contract:

- Financial submission accepts an optional `idempotency_key` argument or
  `Idempotency-Key` header. Keys are tenant-scoped and bound to initiator,
  operation, resource, normalized parameters, and server operation ID. Conflicting
  reuse returns `IDEMPOTENCY_CONFLICT`. Original unkeyed MEDIUM calls remain
  compatible; callers must supply a stable key for submission deduplication.
- A keyed submission atomically stores its original HTTP response, including a
  pending approval receipt. Replays return that receipt; `get_approval` provides
  the current lifecycle state and execution result.
- Approval execution uses the approval ID itself as the stable execution key.
  It accepts no replacement financial parameters. COMPLETED results are stored
  and replayed verbatim even when current business state subsequently changes.
- `BEGIN IMMEDIATE` serializes decisions/execution. Business effects, EXECUTING
  and COMPLETED transitions, results, and lifecycle audit events share one
  transaction. EXECUTING is not separately committed, so a process/database
  failure cannot strand a partially committed financial operation.
- Business, permission, digest, or policy invalidation produces FAILED with no
  financial effect. Database transaction failures roll back to APPROVED, allowing
  explicit recovery after state inspection. No write is automatically retried.
- Requests expire after a server-configured lifetime (default one hour, maximum
  one day). Expiration is checked and persisted on review, inspection, and
  execution. Approving or repeating a request does not extend its lifetime.
- Price approvals bind the original price as well as the target price. Any
  intervening price change invalidates the approval and requires a new review.

### 4.4 Standalone Human Review Boundary

The standalone backend provides GET `/v1/approvals/{id}` and POST actions
`/approve`, `/reject`, and `/execute`. Decisions require separate server-configured
human credentials (`HUMAN_APPROVAL_TOKENS`), the appropriate current Manager/Admin
role, the same tenant, and a reviewer different from the initiator. API/Agent
credentials cannot make approval decisions even if their database role is Admin.

Only `get_approval` and `execute_approval` are exposed in the Agent Tool Registry.
The initiator executes after review. The backend rechecks initiator permission,
reviewer authority, digest, expiry, and current business constraints before any
effect. Approved status and approval IDs supplied to ordinary financial routes
have no authorization effect.

Approval results and failures use the existing structured Observation format;
`APPROVAL_REQUIRED` carries a validated `error.approval` receipt. Backend lifecycle
audit entries include actor, tenant, operation ID, state, and trace correlation.
OpenClaw integration and deployment credential separation belong to Phase 5.

### Acceptance Criteria

Automated tests must confirm:

- Refund without approval is rejected.
- Rejected approval cannot execute.
- Modified parameters invalidate approval.
- Expired approval cannot execute.
- Duplicate submissions do not double-refund.
- Unauthorized users cannot approve.
- Concurrent approval/execution attempts do not duplicate the operation.

---

## Phase 5 — Integrate Existing OpenClaw Skills

Preserve the current four Skills as much as possible.

Replace direct business API access with calls to the controlled Dispatcher.

Review whether the best integration is:

1. An OpenClaw plugin exposing typed tools; or
2. A restricted CLI/HTTP Gateway invoked by existing Skills.

Prefer typed tools when the supported OpenClaw interface permits them.

Regardless of the integration approach:

- Remove direct raw business API access from the Agent execution environment.
- Restrict backend network access to trusted gateway/backend services.
- Ensure credentials are never exposed in Skill instructions or LLM context.
- Use the same Dispatcher validation and authorization path for every tool.

Add end-to-end demonstrations for:

1. Order query.
2. Invalid parameter correction.
3. Refund requiring approval.
4. Price modification requiring approval.
5. Cross-tenant access denied.

### Acceptance Criteria

OpenClaw can invoke the Dispatcher successfully, receive structured observations, and complete supported business tasks without bypassing authorization.

---

## Phase 6 — Automated Evaluation Framework

**Implementation status (2026-11-10):** Phase 6A offline framework is complete.
Phase 6B real OpenClaw/model evaluation is designed but intentionally unexecuted;
no real-model measurements exist yet.

Implement a JSON-driven test framework using pytest.

Create at least 72 test cases across six dimensions:

| Dimension | Test Cases |
|---|---:|
| Tool selection accuracy | 12 |
| Argument extraction accuracy | 12 |
| Context/tenant isolation | 12 |
| Prompt injection defense | 12 |
| Approval bypass defense | 12 |
| Cross-tenant access defense | 12 |
| Total | 72 |

Each test case should contain:

- Test ID
- Category
- User request
- Authenticated execution context
- Initial database state
- Expected tool/arguments where applicable
- Expected response
- Expected final database state
- Pass/fail criteria

Use deterministic assertions for security and business logic.

For Agent-specific cases, run the actual Agent and evaluate tool selection, extracted parameters and final outcomes.

### Bad Case Collection

Automatically record failed cases with:

- Test ID
- Input
- Agent output
- Tool execution trace
- Expected result
- Actual result
- Error category

Support rerunning failed tests after fixing issues.

### Baseline Comparison

Define two reproducible configurations:

**Baseline**
- Basic tool calling.
- Minimal validation/recovery behavior.
- Mandatory backend security controls remain enabled.

**Improved**
- Layered validation.
- Structured observations.
- Bounded safe retry.
- Enhanced tool descriptions/parameter checks.

Run equivalent tasks against both configurations.

Report:

- Tool selection accuracy
- Argument extraction accuracy
- End-to-end task success rate
- Security test pass rate
- Recovery success rate

Clearly distinguish percentage-point improvement from relative percentage improvement.

Do not fabricate metrics or force a predetermined improvement.

### Acceptance Criteria

- At least 72 executable test cases.
- Reproducible test command.
- Machine-readable test report.
- Bad Case logs.
- Baseline vs Improved comparison using actual results.

---

## Phase 7 — Documentation and Demo

Create a README covering:

1. Architecture and component responsibilities.
2. Installation and local startup instructions.
3. Registered Tool list.
4. Validation and structured Observation design.
5. Authentication and tenant isolation model.
6. Approval and idempotency workflow.
7. Test execution.
8. Evaluation results and limitations.

A lightweight UI is optional. Prefer a simple working CLI demonstration over spending time on frontend styling.

Provide sample scenarios and expected results.

---

## Suggested Project Structure

```text
ecommerce-openclaw/
├── mock-api/
│   ├── app.py
│   ├── models.py
│   ├── database.py
│   └── routes/
├── dispatcher/
│   ├── registry.py
│   ├── dispatcher.py
│   ├── schemas.py
│   ├── observation.py
│   ├── validator.py
│   └── backend_client.py
├── security/
│   ├── auth.py
│   ├── policies.py
│   ├── approval.py
│   ├── idempotency.py
│   └── audit.py
├── skills/
│   └── platform/
├── evaluations/
│   ├── cases/
│   ├── runner.py
│   ├── metrics.py
│   └── bad_cases/
├── tests/
├── scripts/
├── requirements.txt
└── README.md
```

This structure is a suggestion, not a requirement. Adapt it to the existing repository rather than moving files unnecessarily.

---

## Execution Instructions for Coding Agent

**Important: Implement incrementally. Do not attempt all phases in one unreviewed change.**

For each phase:

1. Inspect the existing code and dependencies before making changes.
2. Write a short implementation checklist.
3. Implement the smallest working version.
4. Add relevant unit/integration tests.
5. Run tests and fix discovered issues.
6. Summarize modified files, design decisions and test results.
7. State what remains incomplete.

Prioritize in this order:

1. Persistent Mock Backend + Refund / Price APIs
2. Controlled Dispatcher + JSON Schema + Structured Observation
3. Tenant Authorization + Approval + Idempotency
4. OpenClaw Integration
5. Evaluation Framework
6. Documentation and optional UI

**Start with Phase 1 only.**

Before changing files, inspect the repository and report:

- Existing API routes and business data structures.
- Existing OpenClaw Skill invocation patterns.
- Dependencies and runtime requirements.
- The minimal modifications necessary to introduce persistent refunds and price changes.
- Any discovered conflicts with the proposed architecture.

Then implement Phase 1, run tests, and provide a concise completion report. Do not proceed to Phase 2 until Phase 1 is verified.
