import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from threading import Barrier

import pytest

from models import ApprovalRequest, Order, Product, User


@pytest.fixture
def security_flow(app_module, tmp_path):
    database_path = tmp_path / "phase4-security-audit.sqlite3"
    app = app_module.create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database_path}",
        "SEED_DAYS": 0,
        "LEGACY_READS": False,
        "API_TOKENS": {
            "operator-api": "user-a",
            "manager-api": "manager",
            "admin-api": "admin",
            "foreign-api": "user-b",
        },
        "HUMAN_APPROVAL_TOKENS": {
            "manager-human": "manager",
            "admin-human": "admin",
            "foreign-human": "user-b",
        },
    })
    with app.extensions["session_factory"].begin() as session:
        session.add_all([
            User(id="manager", tenant_id="merchant-12345", name="Manager", role="manager"),
            User(id="admin", tenant_id="merchant-12345", name="Admin", role="admin"),
        ])
        session.get(User, "user-b").role = "admin"
        session.add(Order(
            tenant_id="merchant-12345",
            order_id="AUDIT-ORDER",
            status="paid",
            created_at=f"{date.today()}T12:00:00+00:00",
            customer_name="Audit",
            total_amount=60_000,
            refunded_amount=0,
            logistics=None,
            items=[{"sku": "AUDIT-SKU", "name": "Audit", "quantity": 1, "price": 60_000}],
        ))
        session.add(Product(
            tenant_id="merchant-12345",
            sku="AUDIT-SKU",
            name="Audit",
            category="Audit",
            price=10_000,
            status="on_sale",
            listed_at=date.today().isoformat(),
            stock=1,
            threshold=0,
            daily_avg=1,
        ))

    class SecurityFlow:
        def request(self, method, path, token, body=None, headers=None):
            request_headers = {"Authorization": f"Bearer {token}", "X-Request-ID": "security-audit"}
            request_headers.update(headers or {})
            with app.test_client() as client:
                return client.open(path, method=method, headers=request_headers, json=body)

        def refund(self, amount, token="operator-api", key=None):
            body = {"amount": amount, "reason": "security audit"}
            if key is not None:
                body["idempotency_key"] = key
            return self.request("POST", "/v1/orders/AUDIT-ORDER/refund", token, body)

        def price(self, price, token="operator-api", key=None):
            body = {"price": price}
            if key is not None:
                body["idempotency_key"] = key
            return self.request("PATCH", "/v1/products/AUDIT-SKU/price", token, body)

        def action(self, approval_id, action, token):
            return self.request("POST", f"/v1/approvals/{approval_id}/{action}", token, {})

        def pending(self, response):
            assert response.status_code == 403
            body = response.get_json()
            assert body["code"] == "APPROVAL_REQUIRED"
            return body["approval"]

        def scalar(self, query):
            with closing(sqlite3.connect(database_path)) as connection:
                return connection.execute(query).fetchone()[0]

        def count(self, table):
            return self.scalar(f"SELECT COUNT(*) FROM {table}")

    flow = SecurityFlow()
    flow.app = app
    flow.database_path = database_path
    yield flow
    app.extensions["database"].dispose()


@pytest.mark.parametrize("token", [
    "operator-api", "manager-api", "admin-api", "manager-human", "admin-human",
])
@pytest.mark.parametrize("operation", ["refund", "price"])
def test_privileged_credentials_cannot_bypass_high_risk_approval(security_flow, token, operation):
    response = security_flow.refund(20_000, token) if operation == "refund" else security_flow.price(11_000, token)
    approval = security_flow.pending(response)
    assert approval["status"] == "PENDING"
    assert security_flow.scalar(
        "SELECT refunded_amount FROM orders WHERE order_id='AUDIT-ORDER'"
    ) == 0
    assert security_flow.scalar(
        "SELECT price FROM products WHERE sku='AUDIT-SKU'"
    ) == 10_000


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_separate_credential_does_not_enable_self_approval(security_flow, role):
    approval = security_flow.pending(security_flow.refund(
        20_000 if role == "manager" else 50_001,
        token=f"{role}-api",
        key=f"self-{role}",
    ))
    response = security_flow.action(approval["id"], "approve", f"{role}-human")
    assert response.status_code == 403
    assert response.get_json()["code"] == "PERMISSION_DENIED"
    with security_flow.app.extensions["session_factory"]() as session:
        persisted = session.get(ApprovalRequest, approval["id"])
        assert persisted.status == "PENDING" and persisted.approver_id is None


def test_foreign_admin_cannot_read_decide_or_execute_approval(security_flow):
    approval = security_flow.pending(security_flow.refund(20_000, key="foreign-denial"))
    for method, suffix, token in (
        ("GET", "", "foreign-api"),
        ("POST", "/approve", "foreign-human"),
        ("POST", "/reject", "foreign-human"),
        ("POST", "/execute", "foreign-api"),
    ):
        response = security_flow.request(method, f"/v1/approvals/{approval['id']}{suffix}", token, None if method == "GET" else {})
        assert response.status_code == 404
        assert response.get_json()["code"] == "RESOURCE_NOT_FOUND"
    with security_flow.app.extensions["session_factory"]() as session:
        persisted = session.get(ApprovalRequest, approval["id"])
        assert persisted.status == "PENDING" and persisted.approver_id is None
    assert security_flow.count("refunds") == 0


def test_two_distinct_approvals_cannot_oversubscribe_refundable_balance(security_flow):
    approvals = [
        security_flow.pending(security_flow.refund(40_000, key=f"competing-refund-{index}"))
        for index in range(2)
    ]
    for approval in approvals:
        assert security_flow.action(approval["id"], "approve", "manager-human").status_code == 200
    barrier = Barrier(2)

    def execute(approval):
        barrier.wait(timeout=10)
        return security_flow.action(approval["id"], "execute", "operator-api")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(execute, approvals))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert security_flow.scalar(
        "SELECT refunded_amount FROM orders WHERE order_id='AUDIT-ORDER'"
    ) == 40_000
    assert security_flow.count("refunds") == 1
    with security_flow.app.extensions["session_factory"]() as session:
        states = sorted(session.get(ApprovalRequest, item["id"]).status for item in approvals)
    assert states == ["COMPLETED", "FAILED"]


def test_two_distinct_price_approvals_cannot_replay_stale_baseline(security_flow):
    approvals = [
        security_flow.pending(security_flow.price(price, key=f"competing-price-{price}"))
        for price in (11_000, 11_500)
    ]
    for approval in approvals:
        assert security_flow.action(approval["id"], "approve", "manager-human").status_code == 200
    barrier = Barrier(2)

    def execute(approval):
        barrier.wait(timeout=10)
        return security_flow.action(approval["id"], "execute", "operator-api")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(execute, approvals))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert security_flow.scalar("SELECT price FROM products WHERE sku='AUDIT-SKU'") in (11_000, 11_500)
    with security_flow.app.extensions["session_factory"]() as session:
        states = sorted(session.get(ApprovalRequest, item["id"]).status for item in approvals)
    assert states == ["COMPLETED", "FAILED"]


def test_completed_key_and_execution_replays_never_repeat_business_effect(security_flow):
    approval = security_flow.pending(security_flow.refund(20_000, key="completed-replay"))
    assert security_flow.action(approval["id"], "approve", "manager-human").status_code == 200
    completed = security_flow.action(approval["id"], "execute", "operator-api")
    assert completed.status_code == 200
    original_receipt = security_flow.refund(20_000, key="completed-replay")
    execution_replay = security_flow.action(approval["id"], "execute", "operator-api")
    assert original_receipt.status_code == 403
    assert original_receipt.get_json()["approval"]["status"] == "PENDING"
    assert execution_replay.get_json() == completed.get_json()
    assert security_flow.scalar(
        "SELECT refunded_amount FROM orders WHERE order_id='AUDIT-ORDER'"
    ) == 20_000
    assert security_flow.count("refunds") == 1


def test_unkeyed_low_risk_repeats_are_not_deduplicated(security_flow):
    responses = [security_flow.refund(100) for _ in range(2)]
    assert [response.status_code for response in responses] == [201, 201]
    assert security_flow.scalar(
        "SELECT refunded_amount FROM orders WHERE order_id='AUDIT-ORDER'"
    ) == 200
    assert security_flow.count("refunds") == 2
    assert security_flow.count("idempotency_records") == 0


def test_approval_creation_failure_rolls_back_idempotency_and_audit(security_flow):
    with closing(sqlite3.connect(security_flow.database_path)) as connection:
        connection.execute(
            "CREATE TRIGGER reject_idempotency BEFORE INSERT ON idempotency_records "
            "BEGIN SELECT RAISE(ABORT, 'injected idempotency failure'); END"
        )
        connection.commit()
    response = security_flow.refund(20_000, key="creation-failure")
    assert response.status_code == 500
    assert security_flow.count("approval_requests") == 0
    assert security_flow.count("idempotency_records") == 0
    assert security_flow.count("audit_logs") == 0
    assert security_flow.count("refunds") == 0


def test_approval_decision_failure_leaves_request_pending(security_flow):
    approval = security_flow.pending(security_flow.refund(20_000, key="decision-failure"))
    with closing(sqlite3.connect(security_flow.database_path)) as connection:
        connection.execute(
            "CREATE TRIGGER reject_approved_audit BEFORE INSERT ON audit_logs "
            "WHEN NEW.operation='approval.approved' "
            "BEGIN SELECT RAISE(ABORT, 'injected decision failure'); END"
        )
        connection.commit()
    response = security_flow.action(approval["id"], "approve", "manager-human")
    assert response.status_code == 500
    with security_flow.app.extensions["session_factory"]() as session:
        persisted = session.get(ApprovalRequest, approval["id"])
        assert persisted.status == "PENDING" and persisted.approver_id is None
    assert security_flow.count("refunds") == 0


def test_completion_audit_failure_rolls_back_effect_and_completed_state(security_flow):
    approval = security_flow.pending(security_flow.refund(20_000, key="completion-failure"))
    assert security_flow.action(approval["id"], "approve", "manager-human").status_code == 200
    with closing(sqlite3.connect(security_flow.database_path)) as connection:
        connection.execute(
            "CREATE TRIGGER reject_completed_audit BEFORE INSERT ON audit_logs "
            "WHEN NEW.operation='approval.completed' "
            "BEGIN SELECT RAISE(ABORT, 'injected completion failure'); END"
        )
        connection.commit()
    response = security_flow.action(approval["id"], "execute", "operator-api")
    assert response.status_code == 500
    with security_flow.app.extensions["session_factory"]() as session:
        persisted = session.get(ApprovalRequest, approval["id"])
        assert persisted.status == "APPROVED" and persisted.result is None
    assert security_flow.scalar(
        "SELECT refunded_amount FROM orders WHERE order_id='AUDIT-ORDER'"
    ) == 0
    assert security_flow.count("refunds") == 0


def test_execution_revalidates_initiator_and_reviewer_permissions(security_flow):
    approval = security_flow.pending(security_flow.refund(20_000, key="permission-recheck"))
    assert security_flow.action(approval["id"], "approve", "manager-human").status_code == 200
    with security_flow.app.extensions["session_factory"].begin() as session:
        session.get(User, "manager").role = "operator"
        session.get(User, "user-a").role = "viewer"
    response = security_flow.action(approval["id"], "execute", "operator-api")
    assert response.status_code == 403
    assert response.get_json()["code"] == "PERMISSION_DENIED"
    with security_flow.app.extensions["session_factory"]() as session:
        persisted = session.get(ApprovalRequest, approval["id"])
        assert persisted.status == "FAILED"
    assert security_flow.count("refunds") == 0
