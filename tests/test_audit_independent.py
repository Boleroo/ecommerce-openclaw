"""Independent audit regressions. Confirmed defects deliberately remain failing."""

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from threading import Barrier
from uuid import UUID

import httpx
import pytest
from flask import request
from sqlalchemy import event

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import HTTPBackendClient
from dispatcher.gateway import create_app as create_gateway
from models import Order, Product


class FaultTransport(httpx.BaseTransport):
    def __init__(self, app):
        self.inner = httpx.WSGITransport(app=app)
        self.calls = []
        self.before = None
        self.after = None

    def handle_request(self, outgoing):
        self.calls.append((outgoing.method, outgoing.url.path))
        if self.before is not None:
            injected = self.before(outgoing)
            if injected is not None:
                return injected
        response = self.inner.handle_request(outgoing)
        response.read()
        if self.after is not None:
            replacement = self.after(outgoing, response)
            if replacement is not None:
                response.close()
                return replacement
        return response

    def close(self):
        self.inner.close()


@dataclass
class AuditStack:
    app: object
    gateway: object
    transport: FaultTransport
    audit: SQLiteAuditStore
    database_path: object
    sleeps: list

    def execute(self, tool, arguments, token="audit-a"):
        with self.gateway.test_client() as client:
            return client.post(
                "/tools/execute", headers={"Authorization": f"Bearer {token}"},
                json={"tool": tool, "arguments": arguments},
            )

    def snapshot(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            return {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("tenants", "users", "products", "orders", "refunds")
            }

    def ledger(self, tenant_id="merchant-12345", order_id="AUDIT-A"):
        with closing(sqlite3.connect(self.database_path)) as connection:
            order = connection.execute(
                "SELECT total_amount, refunded_amount, status FROM orders WHERE tenant_id=? AND order_id=?",
                (tenant_id, order_id),
            ).fetchone()
            refunds = connection.execute(
                "SELECT amount, user_id FROM refunds WHERE tenant_id=? AND order_id=? ORDER BY rowid",
                (tenant_id, order_id),
            ).fetchall()
            return order, refunds


@pytest.fixture
def audit_stack(app_module, tmp_path):
    database_path = tmp_path / "independent-business.sqlite3"
    app = app_module.create_app({
        "TESTING": False, "PROPAGATE_EXCEPTIONS": False,
        "DATABASE_URL": f"sqlite:///{database_path}",
        "API_TOKENS": {"audit-a": "user-a", "audit-b": "user-b"},
        "LEGACY_READS": False, "SEED_DAYS": 0,
    })
    with app.extensions["session_factory"].begin() as session:
        for tenant_id, identifier in (("merchant-12345", "AUDIT-A"), ("merchant-67890", "AUDIT-B")):
            session.add(Product(
                tenant_id=tenant_id, sku=identifier, name=identifier, category="audit",
                price=1000, status="on_sale", listed_at=date.today().isoformat(),
                stock=10, threshold=2, daily_avg=1,
            ))
            session.add(Order(
                tenant_id=tenant_id, order_id=identifier, status="paid",
                created_at=f"{date.today()}T12:00:00+08:00", customer_name=identifier,
                total_amount=1000, refunded_amount=0, logistics=None,
                items=[{"sku": identifier, "name": identifier, "quantity": 1, "price": 1000}],
            ))
    transport = FaultTransport(app)
    backend = HTTPBackendClient("http://audit-backend", transport=transport)
    audit = SQLiteAuditStore(tmp_path / "independent-audit.sqlite3")
    gateway = create_gateway({"TESTING": False, "PROPAGATE_EXCEPTIONS": False}, backend=backend, audit=audit)
    sleeps = []
    gateway.extensions["dispatcher"].sleep = sleeps.append
    yield AuditStack(app, gateway, transport, audit, database_path, sleeps)
    backend.close()
    app.extensions["database"].dispose()


def assert_observation(response, code=None, recoverable=None):
    observation = response.get_json()
    assert isinstance(observation, dict), response.get_data(as_text=True)
    assert set(observation) == {"success", "tool", "error", "data", "trace_id"}
    assert UUID(observation["trace_id"]).version == 4
    assert response.headers["X-Request-ID"] == observation["trace_id"]
    if code is None:
        assert observation["success"] is True, observation
        assert observation["error"] is None
    else:
        assert observation["success"] is False, observation
        assert observation["data"] is None
        assert observation["error"]["code"] == code, observation
        if recoverable is not None:
            assert observation["error"]["recoverable"] is recoverable
    return observation


def test_audit_refund_persists_across_application_restart(audit_stack, app_module):
    response = audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": 375, "reason": "audit"})
    observation = assert_observation(response)
    assert observation["data"]["refund"]["amount"] == 375
    assert audit_stack.ledger() == ((1000, 375, "paid"), [(375, "user-a")])
    restarted = app_module.create_app(dict(audit_stack.app.config))
    try:
        with restarted.test_client() as client:
            order = client.get("/v1/orders/AUDIT-A", headers={"Authorization": "Bearer audit-a"}).get_json()
            assert order["refunded_amount"] == 375
            assert order["remaining_refundable_amount"] == 625
        assert audit_stack.ledger() == ((1000, 375, "paid"), [(375, "user-a")])
    finally:
        restarted.extensions["database"].dispose()


@pytest.mark.parametrize("entry", ["backend", "dispatcher"])
def test_audit_excessive_refund_has_no_database_effect(audit_stack, entry):
    before = audit_stack.snapshot()
    if entry == "backend":
        response = audit_stack.app.test_client().post(
            "/v1/orders/AUDIT-A/refund", headers={"Authorization": "Bearer audit-a"}, json={"amount": 1001},
        )
        assert response.status_code == 409
        assert response.get_json()["code"] == "REFUND_AMOUNT_EXCEEDED"
    else:
        assert_observation(audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": 1001}), "REFUND_AMOUNT_EXCEEDED", False)
    assert audit_stack.snapshot() == before


@pytest.mark.parametrize("entry", ["backend", "dispatcher"])
def test_audit_sqlite_failure_rolls_back_order_and_ledger(audit_stack, entry):
    with closing(sqlite3.connect(audit_stack.database_path)) as connection:
        connection.execute("CREATE TRIGGER audit_reject_refund BEFORE INSERT ON refunds BEGIN SELECT RAISE(ABORT, 'audit insert failure'); END")
        connection.commit()
    updates = []

    def observe_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE orders"):
            updates.append(statement)

    before = audit_stack.snapshot()
    engine = audit_stack.app.extensions["database"]
    event.listen(engine, "after_cursor_execute", observe_update)
    try:
        if entry == "backend":
            response = audit_stack.app.test_client().post(
                "/v1/orders/AUDIT-A/refund", headers={"Authorization": "Bearer audit-a"}, json={"amount": 300},
            )
            assert response.status_code == 500
            assert response.get_json()["code"] == "INTERNAL_ERROR"
        else:
            assert_observation(audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": 300}), "WRITE_OUTCOME_UNKNOWN", False)
            assert audit_stack.transport.calls.count(("POST", "/v1/orders/AUDIT-A/refund")) == 1
    finally:
        event.remove(engine, "after_cursor_execute", observe_update)
    assert updates, "Injection must occur after the order UPDATE, not just before any write"
    assert audit_stack.snapshot() == before


@pytest.mark.parametrize("entry", ["backend", "dispatcher", "mixed"])
def test_audit_concurrent_partial_refunds_preserve_balance(audit_stack, entry):
    barrier = Barrier(4)

    @audit_stack.app.before_request
    def synchronize_refunds():
        if request.method == "POST" and request.path.endswith("/refund"):
            barrier.wait(timeout=10)

    def refund(index_amount):
        index, amount = index_amount
        use_gateway = entry == "dispatcher" or (entry == "mixed" and index % 2 == 0)
        if use_gateway:
            response = audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": amount})
            payload = response.get_json()
            assert payload["success"] or payload["error"]["code"] in ("INVALID_BUSINESS_STATE", "REFUND_AMOUNT_EXCEEDED")
            return amount if payload["success"] else 0
        with audit_stack.app.test_client() as client:
            response = client.post("/v1/orders/AUDIT-A/refund", headers={"Authorization": "Bearer audit-a"}, json={"amount": amount})
            assert response.status_code in (201, 409)
            return amount if response.status_code == 201 else 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        accepted = list(executor.map(refund, enumerate([600, 600, 400, 400])))
    order, refunds = audit_stack.ledger()
    assert 0 < sum(accepted) == order[1] == sum(amount for amount, actor in refunds) <= 1000
    assert len(refunds) == sum(amount > 0 for amount in accepted) == 2
    assert order[2] == ("refunded" if order[1] == 1000 else "paid")
    assert audit_stack.ledger("merchant-67890", "AUDIT-B") == ((1000, 0, "paid"), [])


@pytest.mark.parametrize("actor, foreign_id", [("audit-a", "AUDIT-B"), ("audit-b", "AUDIT-A")])
@pytest.mark.parametrize("entry", ["backend", "dispatcher"])
def test_audit_foreign_access_preserves_all_business_rows(audit_stack, actor, foreign_id, entry):
    before = audit_stack.snapshot()
    if entry == "backend":
        with audit_stack.app.test_client() as client:
            headers = {"Authorization": f"Bearer {actor}", "X-Tenant-ID": "merchant-67890", "X-Role": "admin"}
            responses = [
                client.get(f"/v1/orders/{foreign_id}", headers=headers),
                client.post(f"/v1/orders/{foreign_id}/refund", headers=headers, json={"amount": 100}),
                client.patch(f"/v1/products/{foreign_id}/price", headers=headers, json={"price": 100}),
            ]
            assert all(response.status_code == 404 for response in responses)
    else:
        for tool, arguments in (
            ("get_order", {"order_id": foreign_id}),
            ("refund_order", {"order_id": foreign_id, "amount": 100}),
            ("change_price", {"sku": foreign_id, "price": 100}),
        ):
            assert_observation(audit_stack.execute(tool, arguments, actor), "RESOURCE_NOT_FOUND", True)
    assert audit_stack.snapshot() == before


@pytest.mark.parametrize("amount", [True, False, 0, -1, 1.0, 1.5, "100", None, [], {}])
@pytest.mark.parametrize("operation", ["refund", "price"])
def test_audit_integer_cents_rejected_at_both_boundaries(audit_stack, amount, operation):
    before = audit_stack.snapshot()
    endpoint = "/v1/orders/AUDIT-A/refund" if operation == "refund" else "/v1/products/AUDIT-A/price"
    field = "amount" if operation == "refund" else "price"
    tool = "refund_order" if operation == "refund" else "change_price"
    arguments = {"order_id": "AUDIT-A"} if operation == "refund" else {"sku": "AUDIT-A"}
    response = audit_stack.app.test_client().open(
        endpoint, method="POST" if operation == "refund" else "PATCH",
        headers={"Authorization": "Bearer audit-a"}, json={field: amount},
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_ARGUMENT"
    observation = assert_observation(
        audit_stack.execute(tool, {**arguments, field: amount}), "INVALID_ARGUMENT", True,
    )
    assert audit_stack.audit.get(observation["trace_id"])["requests"] == []
    assert audit_stack.snapshot() == before


FAULTS = ["http500", "http429", "connect_timeout", "read_timeout", "malformed_json", "unexpected_schema"]


def injected_response(fault):
    if fault == "connect_timeout":
        raise httpx.ConnectTimeout("audit injected connect timeout")
    if fault == "read_timeout":
        raise httpx.ReadTimeout("audit injected read timeout")
    if fault == "http500":
        return httpx.Response(500, json={"code": "INTERNAL_ERROR"})
    if fault == "http429":
        return httpx.Response(429, content=b"busy", headers={"Content-Type": "text/plain"})
    if fault == "malformed_json":
        return httpx.Response(200, content=b'{"broken":', headers={"Content-Type": "application/json"})
    return httpx.Response(200, json={"unexpected": "schema"})


@pytest.mark.parametrize("fault", FAULTS)
def test_audit_read_fault_policy_and_unchanged_state(audit_stack, fault):
    before = audit_stack.snapshot()

    def fail_read(outgoing):
        if outgoing.url.path == "/v1/products":
            return injected_response(fault)

    audit_stack.transport.before = fail_read
    response = audit_stack.execute("list_products", {})
    expected = {
        "http500": ("INTERNAL_ERROR", False, 1),
        "http429": ("UPSTREAM_UNAVAILABLE", True, 3),
        "connect_timeout": ("UPSTREAM_TIMEOUT", True, 3),
        "read_timeout": ("UPSTREAM_TIMEOUT", True, 3),
        "malformed_json": ("INVALID_BACKEND_RESPONSE", False, 1),
        "unexpected_schema": ("INVALID_BACKEND_RESPONSE", False, 1),
    }[fault]
    assert_observation(response, expected[0], expected[1])
    assert audit_stack.transport.calls.count(("GET", "/v1/products")) == expected[2]
    assert audit_stack.sleeps == ([0.1, 0.2] if expected[2] == 3 else [])
    assert audit_stack.snapshot() == before


@pytest.mark.parametrize("fault, timing", [
    (fault, timing) for fault in FAULTS for timing in ("before", "after_commit")
    if (fault, timing) != ("connect_timeout", "after_commit")
])
@pytest.mark.parametrize("operation", ["refund", "price"])
def test_audit_write_faults_never_replay(audit_stack, fault, timing, operation):
    before = audit_stack.snapshot()
    method = "POST" if operation == "refund" else "PATCH"
    endpoint = "/v1/orders/AUDIT-A/refund" if operation == "refund" else "/v1/products/AUDIT-A/price"

    def fail_before(outgoing):
        if outgoing.method == method:
            return injected_response(fault)

    def fail_after(outgoing, response):
        if outgoing.method == method:
            assert response.status_code == (201 if operation == "refund" else 200)
            return injected_response(fault)

    if timing == "before":
        audit_stack.transport.before = fail_before
    else:
        audit_stack.transport.after = fail_after
    tool = "refund_order" if operation == "refund" else "change_price"
    arguments = {"order_id": "AUDIT-A", "amount": 100} if operation == "refund" else {"sku": "AUDIT-A", "price": 950}
    observation = assert_observation(audit_stack.execute(tool, arguments), "WRITE_OUTCOME_UNKNOWN", False)
    assert audit_stack.transport.calls.count((method, endpoint)) == 1
    assert audit_stack.sleeps == []
    assert audit_stack.audit.get(observation["trace_id"])["error_code"] == "WRITE_OUTCOME_UNKNOWN"
    if timing == "before":
        assert audit_stack.snapshot() == before
    elif operation == "refund":
        assert audit_stack.ledger() == ((1000, 100, "paid"), [(100, "user-a")])
    else:
        with closing(sqlite3.connect(audit_stack.database_path)) as connection:
            assert connection.execute("SELECT price FROM products WHERE tenant_id='merchant-12345' AND sku='AUDIT-A'").fetchone() == (950,)
        assert audit_stack.ledger() == ((1000, 0, "paid"), [])


def test_audit_bug_500_after_commit_is_not_recoverable_argument_error(audit_stack):
    def corrupt_failure(outgoing, response):
        if outgoing.method == "POST":
            assert response.status_code == 201
            return httpx.Response(500, json={"code": "INVALID_ARGUMENT", "message": "internal error handler mismatch"})

    audit_stack.transport.after = corrupt_failure
    response = audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": 100})
    assert audit_stack.ledger() == ((1000, 100, "paid"), [(100, "user-a")])
    assert audit_stack.transport.calls.count(("POST", "/v1/orders/AUDIT-A/refund")) == 1
    assert_observation(response, "WRITE_OUTCOME_UNKNOWN", False)


def test_audit_bug_json_rate_limit_uses_transient_retry_policy(audit_stack):
    def rate_limit(outgoing):
        if outgoing.url.path == "/v1/products":
            return httpx.Response(429, json={"code": "RATE_LIMITED", "message": "wait"})

    audit_stack.transport.before = rate_limit
    before = audit_stack.snapshot()
    response = audit_stack.execute("list_products", {})
    assert audit_stack.snapshot() == before
    assert_observation(response, "UPSTREAM_UNAVAILABLE", True)
    assert audit_stack.transport.calls.count(("GET", "/v1/products")) == 3


@pytest.mark.parametrize("corruption", ["empty_receipt_id", "invalid_timestamp"])
def test_audit_bug_malformed_refund_receipt_is_not_success(audit_stack, corruption):
    def corrupt_receipt(outgoing, response):
        if outgoing.method == "POST":
            body = response.json()
            body["refund"]["id" if corruption == "empty_receipt_id" else "created_at"] = "" if corruption == "empty_receipt_id" else "not-a-timestamp"
            return httpx.Response(201, json=body)

    audit_stack.transport.after = corrupt_receipt
    response = audit_stack.execute("refund_order", {"order_id": "AUDIT-A", "amount": 100})
    assert audit_stack.ledger() == ((1000, 100, "paid"), [(100, "user-a")])
    assert_observation(response, "WRITE_OUTCOME_UNKNOWN", False)


def test_audit_bug_returned_orders_must_match_requested_status(audit_stack):
    def ignore_status(outgoing, response):
        if outgoing.url.path == "/v1/orders":
            body = response.json()
            assert len(body["orders"]) == 1
            body["orders"][0]["status"] = "pending"
            return httpx.Response(200, json=body)

    audit_stack.transport.after = ignore_status
    before = audit_stack.snapshot()
    response = audit_stack.execute("list_orders", {"status": "paid"})
    assert audit_stack.snapshot() == before
    assert_observation(response, "INVALID_BACKEND_RESPONSE", False)


def test_audit_bug_impossible_calendar_dates_are_rejected(audit_stack):
    def corrupt_date(outgoing, response):
        if outgoing.url.path == "/v1/products":
            body = response.json()
            body["products"][0]["listed_at"] = "2026-99-99"
            return httpx.Response(200, json=body)

    audit_stack.transport.after = corrupt_date
    before = audit_stack.snapshot()
    response = audit_stack.execute("list_products", {})
    assert audit_stack.snapshot() == before
    assert_observation(response, "INVALID_BACKEND_RESPONSE", False)


def test_audit_bug_deep_json_is_observed_and_audited(audit_stack):
    payload = '[' * 1200 + '0' + ']' * 1200
    assert len(payload) < 16 * 1024
    before = audit_stack.snapshot()
    response = audit_stack.gateway.test_client().post("/tools/execute", data=payload, content_type="application/json")
    assert audit_stack.snapshot() == before
    with closing(sqlite3.connect(audit_stack.audit.path)) as connection:
        recorded = connection.execute("SELECT count(*) FROM tool_executions").fetchone()[0]
    assert (response.status_code, recorded) == (400, 1)
    assert_observation(response, "INVALID_ARGUMENT", True)


def test_audit_bug_unexpected_exception_retains_diagnostic_outcome(audit_stack, caplog):
    def unexpected_failure(outgoing):
        if outgoing.url.path == "/v1/products":
            raise RuntimeError("audit transport failure")

    audit_stack.transport.before = unexpected_failure
    before = audit_stack.snapshot()
    with caplog.at_level(logging.ERROR):
        observation = assert_observation(audit_stack.execute("list_products", {}), "INTERNAL_ERROR", False)
    assert audit_stack.snapshot() == before
    record = audit_stack.audit.get(observation["trace_id"])
    assert record["requests"][-1].get("outcome") == "INTERNAL_ERROR", {"audit": record, "logs": caplog.text}
    assert observation["trace_id"] in caplog.text


def test_audit_bug_overstock_summary_agrees_with_alerts(audit_stack):
    with audit_stack.app.extensions["session_factory"].begin() as session:
        product = session.get(Product, ("merchant-12345", "AUDIT-A"))
        product.stock = product.threshold * 5
    summary = assert_observation(audit_stack.execute("inventory_summary", {}))["data"]
    alerts = assert_observation(audit_stack.execute("inventory_alerts", {"type": "overstock"}))["data"]["alerts"]
    assert summary["overstock_count"] == len(alerts)


@pytest.mark.parametrize("actor, tenant_id", [("audit-a", "merchant-12345"), ("audit-b", "merchant-67890")])
def test_audit_lists_match_database_tenant_ownership(audit_stack, actor, tenant_id):
    before = audit_stack.snapshot()
    for tool, table, field in (("list_orders", "orders", "order_id"), ("list_products", "products", "sku")):
        observation = assert_observation(audit_stack.execute(tool, {"page_size": 100}, actor))
        with closing(sqlite3.connect(audit_stack.database_path)) as connection:
            expected = {row[0] for row in connection.execute(f"SELECT {field} FROM {table} WHERE tenant_id=?", (tenant_id,))}
        assert {record[field] for record in observation["data"][table]} == expected
        assert observation["data"]["total"] == len(expected)
    assert audit_stack.snapshot() == before


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic audit-a"])
def test_audit_unauthenticated_writes_do_not_mutate_state(audit_stack, authorization):
    before = audit_stack.snapshot()
    headers = {"X-Tenant-ID": "merchant-12345", "X-User-ID": "user-a", "X-Role": "admin"}
    if authorization is not None:
        headers["Authorization"] = authorization
    backend_response = audit_stack.app.test_client().post(
        "/v1/orders/AUDIT-A/refund", headers=headers, json={"amount": 100},
    )
    assert backend_response.status_code == 401
    assert backend_response.get_json()["code"] == "UNAUTHENTICATED"
    gateway_response = audit_stack.gateway.test_client().post(
        "/tools/execute", headers=headers,
        json={"tool": "refund_order", "arguments": {"order_id": "AUDIT-A", "amount": 100}},
    )
    assert_observation(gateway_response, "UNAUTHENTICATED", False)
    assert audit_stack.snapshot() == before
