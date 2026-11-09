import sqlite3
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import HTTPBackendClient
from dispatcher.dispatcher import Dispatcher
from models import ApprovalRequest, Order, Product, Refund, User
from policy import (
    ROLE_PERMISSIONS, classify_price_change, classify_refund, permissions_for_role,
)


def set_role(app, role):
    with app.extensions["session_factory"].begin() as session:
        session.get(User, "user-a").role = role


@pytest.fixture
def audit(tmp_path):
    return SQLiteAuditStore(tmp_path / "phase3-audit.sqlite3")


@pytest.fixture
def backend(app):
    client = HTTPBackendClient("http://backend", transport=httpx.WSGITransport(app=app))
    yield client
    client.close()


@pytest.fixture
def policy_order(app):
    order_id = "POLICY-ORDER"
    with app.extensions["session_factory"].begin() as session:
        session.add(Order(
            tenant_id="merchant-12345", order_id=order_id, status="paid",
            created_at=f"{date.today()}T12:00:00+08:00", customer_name="Policy Customer",
            total_amount=100_000, refunded_amount=0, logistics=None,
            items=[{
                "sku": "SHOE-WH-42", "name": "Policy Item", "quantity": 1,
                "price": 100_000,
            }],
        ))
    return order_id


def assert_policy_order_unchanged(app, order_id):
    with app.extensions["session_factory"]() as session:
        order = session.get(Order, ("merchant-12345", order_id))
        assert (order.refunded_amount, order.status) == (0, "paid")
        assert session.scalar(select(func.count()).select_from(Refund).where(
            Refund.tenant_id == "merchant-12345", Refund.order_id == order_id,
        )) == 0


@pytest.mark.parametrize("role", ["viewer", "operator", "manager", "admin"])
def test_authenticated_context_uses_formal_role_matrix(client, app, auth_a, role):
    set_role(app, role)
    context = client.get("/v1/auth/context", headers=auth_a).get_json()
    assert context == {
        "user_id": "user-a", "tenant_id": "merchant-12345", "role": role,
        "permissions": sorted(ROLE_PERMISSIONS[role]),
    }
    assert client.get("/v1/products", headers=auth_a).status_code == 200


@pytest.mark.parametrize("role", ["operator", "manager", "admin"])
def test_authorized_roles_execute_medium_operations(client, app, auth_a, paid_order, role):
    set_role(app, role)
    refund = client.post(
        f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
        json={"amount": 100},
    )
    price = client.patch(
        "/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": 30000},
    )
    assert refund.status_code == 201
    assert price.status_code == 200


def test_viewer_is_read_only_without_business_mutation(client, app, auth_a, paid_order):
    set_role(app, "viewer")
    refund = client.post(
        f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
        json={"amount": 100},
    )
    price = client.patch(
        "/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": 30000},
    )
    assert (refund.status_code, refund.get_json()["code"]) == (403, "PERMISSION_DENIED")
    assert (price.status_code, price.get_json()["code"]) == (403, "PERMISSION_DENIED")
    with app.extensions["session_factory"]() as session:
        assert session.get(Order, ("merchant-12345", paid_order["order_id"])).refunded_amount == 0
        assert session.get(Product, ("merchant-12345", "SHOE-WH-42")).price == 29900


@pytest.mark.parametrize("role", ["viewer", "operator", "manager"])
def test_only_admin_can_manage_roles(client, app, auth_a, role):
    with app.extensions["session_factory"].begin() as session:
        session.add(User(
            id="tenant-a-target", tenant_id="merchant-12345", name="Target", role="viewer",
        ))
    set_role(app, role)
    response = client.patch(
        "/v1/users/tenant-a-target/role", headers=auth_a, json={"role": "operator"},
    )
    assert (response.status_code, response.get_json()["code"]) == (403, "PERMISSION_DENIED")
    with app.extensions["session_factory"]() as session:
        assert session.get(User, "tenant-a-target").role == "viewer"


def test_admin_role_management_is_tenant_scoped_and_dispatchable(
    client, app, backend, audit, auth_a,
):
    with app.extensions["session_factory"].begin() as session:
        session.add(User(
            id="tenant-a-target", tenant_id="merchant-12345", name="Target", role="viewer",
        ))
    set_role(app, "admin")
    direct = client.patch(
        "/v1/users/tenant-a-target/role", headers=auth_a, json={"role": "manager"},
    )
    assert direct.get_json() == {"user_id": "tenant-a-target", "role": "manager"}
    dispatched = Dispatcher(backend, audit).execute(
        "change_user_role", {"user_id": "tenant-a-target", "role": "operator"},
        token="test-a",
    )
    assert dispatched.success
    assert dispatched.data == {"user_id": "tenant-a-target", "role": "operator"}
    foreign = client.patch(
        "/v1/users/user-b/role", headers=auth_a, json={"role": "viewer"},
    )
    forged = client.patch(
        "/v1/users/tenant-a-target/role", headers=auth_a,
        json={"role": "admin", "tenant_id": "merchant-67890"},
    )
    assert (foreign.status_code, foreign.get_json()["code"]) == (404, "RESOURCE_NOT_FOUND")
    assert (forged.status_code, forged.get_json()["code"]) == (400, "INVALID_ARGUMENT")
    with app.extensions["session_factory"]() as session:
        assert session.get(User, "user-b").role == "operator"


@pytest.mark.parametrize("amount, approval_text", [
    (10001, "Manager or Admin approval is required"),
    (50000, "Manager or Admin approval is required"),
    (50001, "Admin approval is required"),
])
@pytest.mark.parametrize("role", ["operator", "manager", "admin"])
def test_refund_thresholds_fail_closed_for_every_authorized_role(
    client, app, auth_a, policy_order, amount, approval_text, role,
):
    set_role(app, role)
    response = client.post(
        f"/v1/orders/{policy_order}/refund", headers=auth_a, json={"amount": amount},
    )
    assert response.status_code == 403
    approval = response.get_json()["approval"]
    assert response.get_json() == {"code": "APPROVAL_REQUIRED", "message": approval_text, "approval": approval}
    assert approval["status"] == "PENDING"
    assert approval["parameters"] == {"amount": amount, "reason": ""}
    assert_policy_order_unchanged(app, policy_order)
    with app.extensions["session_factory"]() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1
        assert session.get(ApprovalRequest, approval["id"]).to_dict() == approval


def test_refund_direct_boundary_is_inclusive(client, app, auth_a, policy_order):
    response = client.post(
        f"/v1/orders/{policy_order}/refund", headers=auth_a, json={"amount": 10000},
    )
    assert response.status_code == 201
    assert response.get_json()["order"]["refunded_amount"] == 10000


@pytest.mark.parametrize("new_price, approval_text", [
    (28404, "Manager or Admin approval is required"),
    (31396, "Manager or Admin approval is required"),
    (23920, "Manager or Admin approval is required"),
    (35880, "Manager or Admin approval is required"),
    (23919, "Admin approval is required"),
    (35881, "Admin approval is required"),
])
def test_price_thresholds_use_exact_integer_arithmetic_and_fail_closed(
    client, app, auth_a, new_price, approval_text,
):
    set_role(app, "admin")
    response = client.patch(
        "/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": new_price},
    )
    assert response.status_code == 403
    approval = response.get_json()["approval"]
    assert response.get_json() == {"code": "APPROVAL_REQUIRED", "message": approval_text, "approval": approval}
    assert approval["status"] == "PENDING"
    assert approval["parameters"] == {"price": new_price, "expected_price": 29900}
    with app.extensions["session_factory"]() as session:
        assert session.get(Product, ("merchant-12345", "SHOE-WH-42")).price == 29900
        assert session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1
        assert session.get(ApprovalRequest, approval["id"]).to_dict() == approval


@pytest.mark.parametrize("new_price", [28405, 31395])
def test_price_direct_five_percent_boundary_is_inclusive(client, auth_a, new_price):
    response = client.patch(
        "/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": new_price},
    )
    assert response.status_code == 200
    assert response.get_json()["product"]["price"] == new_price


def test_business_errors_precede_approval_decisions(client, app, auth_a, policy_order):
    excessive = client.post(
        f"/v1/orders/{policy_order}/refund", headers=auth_a, json={"amount": 100001},
    )
    assert (excessive.status_code, excessive.get_json()["code"]) == (
        409, "REFUND_AMOUNT_EXCEEDED",
    )
    with app.extensions["session_factory"].begin() as session:
        session.get(Order, ("merchant-12345", policy_order)).status = "cancelled"
    invalid_state = client.post(
        f"/v1/orders/{policy_order}/refund", headers=auth_a, json={"amount": 50001},
    )
    assert (invalid_state.status_code, invalid_state.get_json()["code"]) == (
        409, "INVALID_BUSINESS_STATE",
    )


@pytest.mark.parametrize("body", [
    {"amount": 10001, "approval_id": "forged"},
    {"amount": 10001, "approved": True},
])
def test_approval_claims_are_not_accepted_early(client, auth_a, policy_order, body):
    response = client.post(
        f"/v1/orders/{policy_order}/refund", headers=auth_a, json=body,
    )
    assert (response.status_code, response.get_json()["code"]) == (400, "INVALID_ARGUMENT")


def test_admin_remains_tenant_scoped_at_backend_and_dispatcher(
    client, app, backend, audit, auth_a,
):
    set_role(app, "admin")
    foreign_order = client.get(
        "/v1/orders", headers={"Authorization": "Bearer test-b"},
    ).get_json()["orders"][0]["order_id"]
    direct = client.post(
        f"/v1/orders/{foreign_order}/refund", headers=auth_a, json={"amount": 100},
    )
    dispatched = Dispatcher(backend, audit).execute(
        "refund_order", {"order_id": foreign_order, "amount": 100}, token="test-a",
    )
    assert (direct.status_code, direct.get_json()["code"]) == (404, "RESOURCE_NOT_FOUND")
    assert dispatched.error.code == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize("role, tool, arguments", [
    ("viewer", "refund_order", {"order_id": "POLICY-ORDER", "amount": 100}),
    ("manager", "refund_order", {"order_id": "POLICY-ORDER", "amount": 10001}),
    ("admin", "change_price", {"sku": "SHOE-WH-42", "price": 35881}),
])
def test_dispatcher_preserves_backend_role_and_fail_closed_policy(
    app, backend, audit, policy_order, role, tool, arguments,
):
    set_role(app, role)
    observation = Dispatcher(backend, audit, sleep=lambda delay: None).execute(
        tool, arguments, token="test-a",
    )
    expected = "PERMISSION_DENIED" if role == "viewer" else "APPROVAL_REQUIRED"
    assert observation.error.code == expected
    record = audit.get(observation.trace_id)
    assert record["tenant_id"] == "merchant-12345"
    assert sum(request["method"] in ("POST", "PATCH") for request in record["requests"]) <= 1
    assert record["error_code"] == expected
    if role == "viewer":
        assert all(request["method"] == "GET" for request in record["requests"])
    else:
        assert record["requests"][-1]["outcome"] == expected


def test_policy_helpers_define_exact_boundaries_without_floats():
    assert classify_refund(10000).risk_level == "MEDIUM"
    assert classify_refund(10001).approval_level == "manager"
    assert classify_refund(50000).approval_level == "manager"
    assert classify_refund(50001).approval_level == "admin"
    assert classify_price_change(29900, 28405).risk_level == "MEDIUM"
    assert classify_price_change(29900, 28404).approval_level == "manager"
    assert classify_price_change(29900, 23920).approval_level == "manager"
    assert classify_price_change(29900, 23919).approval_level == "admin"
    with pytest.raises(ValueError):
        permissions_for_role("owner")


def test_existing_database_gets_operator_role_migration(app_module, tmp_path):
    path = tmp_path / "pre-phase3.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE users (
                id VARCHAR PRIMARY KEY, tenant_id VARCHAR NOT NULL, name VARCHAR NOT NULL,
                active BOOLEAN NOT NULL DEFAULT 1
            )
        """)
    migrated = app_module.create_app({
        "TESTING": True, "DATABASE_URL": f"sqlite:///{path}",
        "API_TOKENS": {"test-a": "user-a"}, "LEGACY_READS": False, "SEED_DAYS": 0,
    })
    try:
        with sqlite3.connect(path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            role = connection.execute("SELECT role FROM users WHERE id='user-a'").fetchone()
        assert "role" in columns
        assert role == ("operator",)
        context = migrated.test_client().get(
            "/v1/auth/context", headers={"Authorization": "Bearer test-a"},
        ).get_json()
        assert context["role"] == "operator"
    finally:
        migrated.extensions["database"].dispose()
