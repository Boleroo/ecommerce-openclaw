import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest
from sqlalchemy import select

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import HTTPBackendClient
from dispatcher.dispatcher import Dispatcher
from dispatcher.gateway import create_app as create_gateway
from models import ApprovalRequest, AuditLog, Order, Product, User
from test_audit_independent import FaultTransport


@pytest.fixture
def flow(app_module, tmp_path):
    database_path = tmp_path / "phase4.sqlite3"
    app = app_module.create_app({
        "TESTING": True, "DATABASE_URL": f"sqlite:///{database_path}",
        "SEED_DAYS": 0, "LEGACY_READS": False,
        "API_TOKENS": {"agent": "user-a", "manager-api": "manager", "admin-api": "admin", "foreign": "user-b", "other": "other"},
        "HUMAN_APPROVAL_TOKENS": {"human-manager": "manager", "human-admin": "admin", "human-other": "other", "human-self": "user-a", "human-foreign": "user-b"},
    })
    with app.extensions["session_factory"].begin() as session:
        for identifier, role in (("manager", "manager"), ("admin", "admin"), ("other", "operator")):
            session.add(User(id=identifier, tenant_id="merchant-12345", name=identifier, role=role))
        session.get(User, "user-b").role = "admin"
        for tenant_id, identifier in (("merchant-12345", "ORDER"), ("merchant-67890", "FOREIGN")):
            session.add(Order(
                tenant_id=tenant_id, order_id=identifier, status="paid",
                created_at=f"{date.today()}T12:00:00+00:00", customer_name=identifier,
                total_amount=100000, refunded_amount=0, logistics=None,
                items=[{"sku": "SKU", "name": "Item", "quantity": 1, "price": 100000}],
            ))
        session.add(Product(
            tenant_id="merchant-12345", sku="SKU", name="Item", category="Test",
            price=10000, status="on_sale", listed_at=date.today().isoformat(),
            stock=10, threshold=2, daily_avg=1,
        ))
    transport = FaultTransport(app)
    backend = HTTPBackendClient("http://backend", transport=transport)
    audit = SQLiteAuditStore(tmp_path / "dispatcher.sqlite3")
    gateway = create_gateway({"TESTING": True}, backend=backend, audit=audit)
    sleeps = []
    gateway.extensions["dispatcher"].sleep = sleeps.append

    class Flow:
        def call(self, method, path, token="agent", body=None):
            with app.test_client() as client:
                return client.open(path, method=method, headers={"Authorization": f"Bearer {token}", "X-Request-ID": "phase4-trace"}, json=body)

        def submit(self, operation="refund", value=20000, key="request-1", token="agent"):
            body = {"amount": value, "reason": "return"} if operation == "refund" else {"price": value}
            if key is not None:
                body["idempotency_key"] = key
            return self.call("POST" if operation == "refund" else "PATCH", "/v1/orders/ORDER/refund" if operation == "refund" else "/v1/products/SKU/price", token, body)

        def action(self, identifier, action, token=None, body=None):
            token = token or ("human-manager" if action in ("approve", "reject") else "agent")
            return self.call("POST", f"/v1/approvals/{identifier}/{action}", token, {} if body is None else body)

        def get(self, identifier, token="agent"):
            return self.call("GET", f"/v1/approvals/{identifier}", token)

        def dispatch(self, tool, arguments, token="agent"):
            with gateway.test_client() as client:
                return client.post("/tools/execute", headers={"Authorization": f"Bearer {token}"}, json={"tool": tool, "arguments": arguments})

        def rows(self, table):
            with closing(sqlite3.connect(database_path)) as connection:
                return connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()

        def refunded(self):
            with closing(sqlite3.connect(database_path)) as connection:
                return connection.execute("SELECT refunded_amount FROM orders WHERE tenant_id='merchant-12345' AND order_id='ORDER'").fetchone()[0]

    fixture = Flow()
    fixture.app, fixture.backend, fixture.audit = app, backend, audit
    fixture.transport, fixture.sleeps, fixture.database_path = transport, sleeps, database_path
    yield fixture
    backend.close()
    app.extensions["database"].dispose()


def pending(response):
    assert response.status_code == 403
    body = response.get_json()
    assert body["code"] == "APPROVAL_REQUIRED"
    assert body["approval"]["status"] == "PENDING"
    return body["approval"]


@pytest.mark.parametrize("operation,value,reviewer,level", [
    ("refund", 10001, "human-manager", "manager"),
    ("refund", 50000, "human-manager", "manager"),
    ("refund", 50001, "human-admin", "admin"),
    ("price", 10501, "human-manager", "manager"),
    ("price", 12000, "human-manager", "manager"),
    ("price", 12001, "human-admin", "admin"),
    ("price", 7999, "human-admin", "admin"),
])
def test_complete_approval_flow_and_atomic_audit(flow, operation, value, reviewer, level):
    before = flow.rows("products"), flow.rows("orders"), flow.rows("refunds")
    approval = pending(flow.submit(operation, value))
    assert approval["approval_level"] == level
    assert before == (flow.rows("products"), flow.rows("orders"), flow.rows("refunds"))
    decision = flow.action(approval["id"], "approve", reviewer)
    assert decision.status_code == 200
    assert decision.get_json()["status"] == "APPROVED"
    assert before == (flow.rows("products"), flow.rows("orders"), flow.rows("refunds"))
    executed = flow.dispatch("execute_approval", {"approval_id": approval["id"]})
    assert executed.status_code == 200, executed.get_json()
    result = executed.get_json()["data"]
    assert result["status"] == "COMPLETED"
    if operation == "refund":
        assert flow.refunded() == value
        assert len(flow.rows("refunds")) == 1
        assert result["result"]["refund"]["amount"] == value
    else:
        assert result["result"]["product"]["price"] == value
        assert flow.refunded() == 0
    again = flow.action(approval["id"], "execute")
    assert again.get_json() == result
    with flow.app.extensions["session_factory"]() as session:
        events = session.scalars(select(AuditLog).where(AuditLog.resource_id == approval["id"]).order_by(AuditLog.id)).all()
        assert [event.operation for event in events] == ["approval.created", "approval.approved", "approval.executing", "approval.completed", "approval.replayed"]
        assert all(event.details["operation_id"] == approval["operation_id"] for event in events)
        assert all(event.details["trace_id"] for event in events)
    assert flow.sleeps == []


@pytest.mark.parametrize("token", ["manager-api", "admin-api", "agent", "human-other", "human-self", "human-foreign"])
@pytest.mark.parametrize("action", ["approve", "reject"])
def test_untrusted_or_unauthorized_approval_cannot_change_state(flow, token, action):
    approval = pending(flow.submit())
    with flow.app.extensions["session_factory"].begin() as session:
        session.get(User, "user-a").role = "admin"
    response = flow.action(approval["id"], action, token)
    assert response.status_code == (404 if token == "human-foreign" else 403)
    assert flow.get(approval["id"]).get_json()["status"] == "PENDING"
    assert flow.refunded() == 0 and flow.rows("refunds") == []


def test_admin_level_cannot_be_approved_by_manager(flow):
    approval = pending(flow.submit(value=50001))
    assert flow.action(approval["id"], "approve").get_json()["code"] == "PERMISSION_DENIED"
    assert flow.action(approval["id"], "approve", "human-admin").status_code == 200


def test_rejected_and_unapproved_operations_never_execute(flow):
    approval = pending(flow.submit())
    assert flow.action(approval["id"], "execute").get_json()["code"] == "APPROVAL_REQUIRED"
    assert flow.action(approval["id"], "reject").get_json()["status"] == "REJECTED"
    for action in ("approve", "execute", "reject"):
        assert flow.action(approval["id"], action).get_json()["code"] == "APPROVAL_REJECTED"
    assert flow.refunded() == 0 and flow.rows("refunds") == []


@pytest.mark.parametrize("approved", [False, True])
def test_expiry_persists_and_blocks_execution(flow, monkeypatch, approved):
    import approvals

    approval = pending(flow.submit())
    if approved:
        assert flow.action(approval["id"], "approve").status_code == 200

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=2)

    monkeypatch.setattr(approvals, "datetime", FutureClock)
    assert flow.get(approval["id"]).get_json()["status"] == "EXPIRED"
    assert flow.action(approval["id"], "execute").get_json()["code"] == "APPROVAL_EXPIRED"
    assert flow.refunded() == 0
    with flow.app.extensions["session_factory"]() as session:
        assert session.get(ApprovalRequest, approval["id"]).status == "EXPIRED"


@pytest.mark.parametrize("field,value", [
    ("parameters", {"amount": 20001, "reason": "return"}),
    ("resource_id", "OTHER"), ("operation_id", "changed"),
    ("initiator_id", "other"), ("approval_level", "admin"),
    ("request_digest", "0" * 64),
])
def test_persisted_binding_tampering_fails_closed(flow, field, value):
    approval = pending(flow.submit())
    with flow.app.extensions["session_factory"].begin() as session:
        setattr(session.get(ApprovalRequest, approval["id"]), field, value)
    response = flow.action(approval["id"], "approve", "human-admin")
    assert response.get_json()["code"] == "APPROVAL_INVALID"
    assert flow.refunded() == 0
    with flow.app.extensions["session_factory"]() as session:
        assert session.get(ApprovalRequest, approval["id"]).status == "FAILED"


def test_canonical_digest_binds_full_request(flow):
    approval = pending(flow.submit())
    document = {field: approval[field] for field in (
        "operation_id", "tenant_id", "initiator_id", "operation", "resource_id",
        "parameters", "approval_level", "expires_at",
    )}
    expected = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert approval["request_digest"] == expected


@pytest.mark.parametrize("action", ["approve", "execute"])
@pytest.mark.parametrize("body", [{"amount": 1}, {"approved": True}, {"request_digest": "forged"}, {"tenant_id": "merchant-67890"}])
def test_lifecycle_cannot_replace_approved_parameters(flow, action, body):
    approval = pending(flow.submit())
    response = flow.action(approval["id"], action, body=body)
    assert response.get_json()["code"] == "INVALID_ARGUMENT"
    assert flow.get(approval["id"]).get_json()["status"] == "PENDING"
    assert flow.refunded() == 0


@pytest.mark.parametrize("mutation", ["cancel", "balance", "initiator_role", "approver_role", "approver_inactive", "price"])
def test_current_business_state_and_permissions_rechecked_at_execution(flow, mutation):
    approval = pending(flow.submit("price", 11000) if mutation == "price" else flow.submit())
    assert flow.action(approval["id"], "approve").status_code == 200
    with flow.app.extensions["session_factory"].begin() as session:
        if mutation == "cancel":
            session.get(Order, ("merchant-12345", "ORDER")).status = "cancelled"
        elif mutation == "balance":
            session.get(Order, ("merchant-12345", "ORDER")).refunded_amount = 90000
        elif mutation == "price":
            session.get(Product, ("merchant-12345", "SKU")).price = 10001
        elif mutation == "initiator_role":
            session.get(User, "user-a").role = "viewer"
        elif mutation == "approver_role":
            session.get(User, "manager").role = "operator"
        else:
            session.get(User, "manager").active = False
    before = flow.rows("orders"), flow.rows("products"), flow.rows("refunds")
    response = flow.action(approval["id"], "execute")
    assert response.status_code in (403, 409)
    assert flow.get(approval["id"]).get_json()["status"] == "FAILED"
    assert before == (flow.rows("orders"), flow.rows("products"), flow.rows("refunds"))


def test_state_changes_before_approval_fail_validation(flow):
    approval = pending(flow.submit())
    with flow.app.extensions["session_factory"].begin() as session:
        session.get(Order, ("merchant-12345", "ORDER")).status = "cancelled"
    assert flow.action(approval["id"], "approve").get_json()["code"] == "INVALID_BUSINESS_STATE"
    assert flow.get(approval["id"]).get_json()["status"] == "FAILED"


@pytest.mark.parametrize("operation,value", [("refund", 100), ("price", 10500), ("refund", 20000), ("price", 12000)])
def test_same_key_replays_original_submission_without_duplicate_effects(flow, operation, value):
    first = flow.submit(operation, value)
    after = flow.rows("orders"), flow.rows("products"), flow.rows("refunds"), flow.rows("approval_requests")
    second = flow.submit(operation, value)
    assert (second.status_code, second.get_json()) == (first.status_code, first.get_json())
    assert after == (flow.rows("orders"), flow.rows("products"), flow.rows("refunds"), flow.rows("approval_requests"))
    assert len(flow.rows("idempotency_records")) == 1


@pytest.mark.parametrize("changed", ["amount", "actor", "operation"])
def test_key_conflicts_do_not_mutate_business_state(flow, changed):
    flow.submit(value=100)
    before = flow.rows("orders"), flow.rows("products"), flow.rows("refunds")
    response = flow.submit(value=101) if changed == "amount" else (
        flow.submit(value=100, token="other") if changed == "actor" else flow.submit("price", 10000)
    )
    assert response.get_json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert before == (flow.rows("orders"), flow.rows("products"), flow.rows("refunds"))


def test_full_refund_replay_bypasses_stale_dispatcher_preflight(flow):
    with flow.app.extensions["session_factory"].begin() as session:
        order = session.get(Order, ("merchant-12345", "ORDER"))
        order.total_amount = 10000
        order.items = [{"sku": "SKU", "name": "Item", "quantity": 1, "price": 10000}]
    arguments = {"order_id": "ORDER", "amount": 10000, "idempotency_key": "full-refund"}
    first = flow.dispatch("refund_order", arguments)
    second = flow.dispatch("refund_order", arguments)
    assert first.get_json()["success"] is second.get_json()["success"] is True
    assert first.get_json()["data"] == second.get_json()["data"]
    assert len(flow.rows("refunds")) == 1


@pytest.mark.parametrize("stage", ["submission", "approval", "execution"])
def test_concurrent_requests_have_one_financial_effect(flow, stage):
    approval = None
    if stage != "submission":
        approval = pending(flow.submit())
    if stage == "execution":
        flow.action(approval["id"], "approve")
    barrier = Barrier(4)

    def run(unused_index):
        barrier.wait(timeout=10)
        if stage == "submission":
            return flow.submit(value=100)
        return flow.action(approval["id"], "approve" if stage == "approval" else "execute")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(run, range(4)))
    assert all(response.status_code in (200, 201) for response in results)
    assert all(response.get_json() == results[0].get_json() for response in results)
    expected = 0 if stage == "approval" else (100 if stage == "submission" else 20000)
    assert flow.refunded() == expected
    assert len(flow.rows("refunds")) == (0 if stage == "approval" else 1)


def test_pending_submissions_are_deduplicated_concurrently(flow):
    barrier = Barrier(4)

    def run(unused_index):
        barrier.wait(timeout=10)
        return pending(flow.submit())

    with ThreadPoolExecutor(max_workers=4) as executor:
        approvals = list(executor.map(run, range(4)))
    assert len({approval["id"] for approval in approvals}) == 1
    assert len(flow.rows("approval_requests")) == len(flow.rows("idempotency_records")) == 1
    assert flow.refunded() == 0


def test_lost_execution_response_is_unknown_then_reconciled_without_retry(flow):
    approval = pending(flow.submit())
    flow.action(approval["id"], "approve")

    def lose_response(outgoing, response):
        if outgoing.url.path.endswith("/execute"):
            assert response.status_code == 200
            raise httpx.ReadTimeout("lost response")

    flow.transport.after = lose_response
    failed = flow.dispatch("execute_approval", {"approval_id": approval["id"]}).get_json()
    assert failed["error"]["code"] == "WRITE_OUTCOME_UNKNOWN"
    assert failed["error"]["recoverable"] is False
    assert flow.sleeps == []
    assert flow.transport.calls.count(("POST", f"/v1/approvals/{approval['id']}/execute")) == 1
    flow.transport.after = None
    reconciled = flow.dispatch("get_approval", {"approval_id": approval["id"]}).get_json()
    assert reconciled["data"]["status"] == "COMPLETED"
    replay = flow.dispatch("execute_approval", {"approval_id": approval["id"]}).get_json()
    assert replay["data"] == reconciled["data"]
    assert flow.refunded() == 20000 and len(flow.rows("refunds")) == 1


def test_database_failure_rolls_back_execution_and_allows_explicit_recovery(flow):
    approval = pending(flow.submit())
    flow.action(approval["id"], "approve")
    with closing(sqlite3.connect(flow.database_path)) as connection:
        connection.execute("CREATE TRIGGER fail_refund BEFORE INSERT ON refunds BEGIN SELECT RAISE(ABORT, 'failure'); END")
        connection.commit()
    response = flow.dispatch("execute_approval", {"approval_id": approval["id"]}).get_json()
    assert response["error"]["code"] == "WRITE_OUTCOME_UNKNOWN"
    assert flow.get(approval["id"]).get_json()["status"] == "APPROVED"
    assert flow.refunded() == 0 and flow.rows("refunds") == []
    with closing(sqlite3.connect(flow.database_path)) as connection:
        connection.execute("DROP TRIGGER fail_refund")
        connection.commit()
    assert flow.action(approval["id"], "execute").get_json()["status"] == "COMPLETED"
    assert flow.refunded() == 20000 and len(flow.rows("refunds")) == 1
    with flow.app.extensions["session_factory"]() as session:
        events = session.scalars(select(AuditLog.operation)).all()
        assert "approval.infrastructure_failed" in events
        assert events.count("approval.executing") == events.count("approval.completed") == 1


def test_restart_preserves_pending_completed_and_idempotency(flow, app_module):
    approval = pending(flow.submit())
    restarted = app_module.create_app(dict(flow.app.config))
    try:
        client = restarted.test_client()
        assert client.post(f"/v1/approvals/{approval['id']}/approve", headers={"Authorization": "Bearer human-manager"}, json={}).status_code == 200
        first = client.post(f"/v1/approvals/{approval['id']}/execute", headers={"Authorization": "Bearer agent"}, json={})
        assert first.get_json()["status"] == "COMPLETED"
    finally:
        restarted.extensions["database"].dispose()
    assert flow.action(approval["id"], "execute").get_json() == first.get_json()
    assert pending(flow.submit())["id"] == approval["id"]
    assert flow.refunded() == 20000 and len(flow.rows("refunds")) == 1


def test_agent_cannot_invoke_human_decision_tools_or_forge_approval(flow):
    approval = pending(flow.submit())
    for name in ("approve", "approve_approval", "reject_approval"):
        response = flow.dispatch(name, {"approval_id": approval["id"]}, "admin-api")
        assert response.get_json()["error"]["code"] == "UNKNOWN_TOOL"
    response = flow.call("POST", "/v1/orders/ORDER/refund", body={"amount": 20000, "approval_id": approval["id"], "approved": True})
    assert response.get_json()["code"] == "INVALID_ARGUMENT"
    assert flow.refunded() == 0


@pytest.mark.parametrize("action", ["get", "execute", "approve", "reject"])
def test_cross_tenant_lifecycle_access_does_not_change_business_state(flow, action):
    approval = pending(flow.submit())
    before = flow.rows("approval_requests"), flow.rows("orders"), flow.rows("refunds")
    response = flow.get(approval["id"], "foreign") if action == "get" else flow.action(approval["id"], action, "human-foreign")
    assert (response.status_code, response.get_json()["code"]) == (404, "RESOURCE_NOT_FOUND")
    assert before == (flow.rows("approval_requests"), flow.rows("orders"), flow.rows("refunds"))


def test_pending_observation_exposes_validated_receipt_and_trace(flow):
    response = flow.dispatch("refund_order", {"order_id": "ORDER", "amount": 20000, "idempotency_key": "pending"})
    observation = response.get_json()
    assert observation["error"]["code"] == "APPROVAL_REQUIRED"
    assert observation["error"]["recoverable"] is False
    approval = observation["error"]["approval"]
    assert approval["status"] == "PENDING"
    assert approval["parameters"] == {"amount": 20000, "reason": ""}
    record = flow.audit.get(observation["trace_id"])
    assert record["state"] == "completed" and record["error_code"] == "APPROVAL_REQUIRED"
    with flow.app.extensions["session_factory"]() as session:
        event = session.scalar(select(AuditLog).where(AuditLog.resource_id == approval["id"]))
        assert event.details["trace_id"] == observation["trace_id"]


@pytest.mark.parametrize("value", [100, 20000])
def test_lost_submission_response_can_be_explicitly_replayed_with_same_key(flow, value):
    def lose_response(outgoing, response):
        if outgoing.method == "POST":
            assert response.status_code == (201 if value == 100 else 403)
            raise httpx.ReadTimeout("lost submission")

    arguments = {"order_id": "ORDER", "amount": value, "idempotency_key": "lost"}
    flow.transport.after = lose_response
    first = flow.dispatch("refund_order", arguments).get_json()
    assert first["error"]["code"] == "WRITE_OUTCOME_UNKNOWN"
    assert first["error"]["recoverable"] is False
    assert flow.transport.calls.count(("POST", "/v1/orders/ORDER/refund")) == 1
    assert flow.sleeps == []
    flow.transport.after = None
    second = flow.dispatch("refund_order", arguments).get_json()
    if value == 100:
        assert second["success"] is True
        assert flow.refunded() == 100 and len(flow.rows("refunds")) == 1
    else:
        assert second["error"]["code"] == "APPROVAL_REQUIRED"
        assert len(flow.rows("approval_requests")) == 1 and flow.refunded() == 0


def test_concurrent_conflicting_decisions_never_overwrite_winner(flow):
    approval = pending(flow.submit())
    barrier = Barrier(2)

    def decide(action):
        barrier.wait(timeout=10)
        return action, flow.action(approval["id"], action)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, ["approve", "reject"]))
    winners = [action for action, response in results if response.status_code == 200]
    assert len(winners) == 1
    assert flow.get(approval["id"]).get_json()["status"] == ("APPROVED" if winners[0] == "approve" else "REJECTED")
    assert flow.refunded() == 0


def test_two_reviewers_do_not_invalidate_winning_approval(flow):
    approval = pending(flow.submit())
    assert flow.action(approval["id"], "approve", "human-manager").status_code == 200
    assert flow.action(approval["id"], "approve", "human-admin").status_code == 409
    assert flow.get(approval["id"]).get_json()["status"] == "APPROVED"
    assert flow.action(approval["id"], "execute").get_json()["status"] == "COMPLETED"


@pytest.mark.parametrize("key", [None, "", "bad key", 42, True, "x" * 129])
def test_malformed_idempotency_keys_rejected_without_mutation(flow, key):
    response = flow.call("POST", "/v1/orders/ORDER/refund", body={"amount": 100, "idempotency_key": key})
    assert response.status_code == 400 and response.get_json()["code"] == "INVALID_ARGUMENT"
    assert flow.refunded() == 0 and flow.rows("idempotency_records") == []


def test_backend_audit_failure_rolls_back_financial_effect_and_receipt(flow):
    with closing(sqlite3.connect(flow.database_path)) as connection:
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_logs BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        connection.commit()
    response = flow.submit(value=100)
    assert response.status_code == 500
    assert flow.refunded() == 0 and flow.rows("refunds") == []
    assert flow.rows("idempotency_records") == []


def test_modified_pending_response_is_unknown_and_never_retried(flow):
    def corrupt(outgoing, response):
        if outgoing.method == "POST":
            body = response.json()
            body["approval"]["parameters"]["amount"] = 20001
            return httpx.Response(403, json=body)

    flow.transport.after = corrupt
    response = flow.dispatch("refund_order", {"order_id": "ORDER", "amount": 20000}).get_json()
    assert response["error"]["code"] == "WRITE_OUTCOME_UNKNOWN"
    assert flow.sleeps == [] and flow.refunded() == 0
    assert flow.transport.calls.count(("POST", "/v1/orders/ORDER/refund")) == 1


def test_read_and_execute_require_initiator_or_authorized_review_context(flow):
    approval = pending(flow.submit())
    assert flow.get(approval["id"], "other").status_code == 403
    flow.action(approval["id"], "approve")
    assert flow.action(approval["id"], "execute", "human-manager").status_code == 403
    assert flow.refunded() == 0


def test_phase3_placeholder_migration_fails_closed(app_module, tmp_path):
    path = tmp_path / "legacy.sqlite3"
    application = app_module.create_app({
        "TESTING": True, "DATABASE_URL": f"sqlite:///{path}", "SEED_DAYS": 0,
        "API_TOKENS": {"legacy": "user-a"}, "LEGACY_READS": False,
    })
    application.extensions["database"].dispose()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE approval_requests")
        connection.execute("CREATE TABLE approval_requests (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, initiator_id TEXT NOT NULL, operation TEXT NOT NULL, parameters JSON NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT)")
        connection.execute("INSERT INTO approval_requests VALUES ('legacy','merchant-12345','user-a','refund_order','{}','approved','2026-01-01T00:00:00+00:00',NULL)")
        connection.commit()
    restarted = app_module.create_app(dict(application.config))
    try:
        with restarted.extensions["session_factory"]() as session:
            approval = session.get(ApprovalRequest, "legacy")
            assert approval.status == "FAILED" and approval.failure_code == "APPROVAL_INVALID"
        response = restarted.test_client().post("/v1/approvals/legacy/execute", headers={"Authorization": "Bearer legacy"}, json={})
        assert response.get_json()["code"] == "APPROVAL_INVALID"
    finally:
        restarted.extensions["database"].dispose()
