from datetime import date, timedelta

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from models import Order, Product, Refund, User


READ_ROUTES = [
    ("/healthz", {"status"}),
    ("/v1/orders", {"total", "page", "page_size", "orders"}),
    ("/v1/orders/stats", {"period", "total_orders", "total_amount", "by_status", "compared_to_yesterday"}),
    ("/v1/products", {"total", "page", "page_size", "products"}),
    ("/v1/products/ranking", {"period", "ranking"}),
    ("/v1/products/SHOE-WH-42/stats", {"sku", "name", "category", "period", "sales_count", "revenue", "avg_daily_sales", "current_stock", "price"}),
    ("/v1/inventory/summary", {"total_skus", "total_stock", "low_stock_count", "out_of_stock_count", "overstock_count"}),
    ("/v1/inventory/alerts", {"alerts"}),
    ("/v1/inventory/SHOE-WH-42", {"sku", "name", "current_stock", "threshold", "daily_sales_avg", "estimated_stockout_days", "status"}),
    ("/v1/inventory/restock-suggestions", {"suggestions"}),
    ("/v1/customers/summary", {"total_customers", "new_customers_30d", "repeat_rate", "avg_order_value", "top_cities"}),
    ("/v1/customers/retention", {"period", "new_customers", "repeat_customers", "repeat_rate", "lost_customers", "avg_orders_per_customer", "cohort"}),
    ("/v1/customers/geo", {"regions"}),
    ("/v1/customers", {"total", "page", "page_size", "customers"}),
    ("/v1/analytics/revenue", {"period", "current", "previous", "change"}),
    ("/v1/analytics/trend", {"metric", "points"}),
    ("/v1/analytics/refunds", {"period", "total_orders", "refunded_orders", "refund_rate", "refund_amount", "top_refund_reasons"}),
    ("/v1/analytics/category", {"categories"}),
    ("/v1/analytics/avg-order-value", {"days", "points"}),
]


@pytest.mark.parametrize("path, keys", READ_ROUTES)
def test_existing_reads_work_without_credentials(client, path, keys):
    response = client.get(path)
    assert response.status_code == 200
    assert set(response.get_json()) == keys


def test_order_detail_keeps_legacy_fields(client, paid_order):
    response = client.get(f"/v1/orders/{paid_order['order_id']}")
    assert response.status_code == 200
    assert {"order_id", "created_at", "status", "customer_name", "total_amount", "items", "logistics"} <= response.get_json().keys()


def test_fixture_compatibility(client):
    products = client.get("/v1/products").get_json()
    assert products["total"] == 12
    assert products["products"][0] == {
        "sku": "SHOE-WH-42", "name": "经典白色运动鞋 42码", "category": "运动鞋",
        "price": 29900, "status": "on_sale", "listed_at": "2025-12-01",
    }
    assert client.get("/v1/inventory/SHOE-WH-42").get_json()["current_stock"] == 3
    assert client.get("/v1/customers/summary").get_json()["total_customers"] == 3256


def test_order_filters_and_stable_multiday_ids(client, auth_a, paid_order):
    start = (date.today() - timedelta(days=2)).isoformat()
    result = client.get(f"/v1/orders?start_date={start}&sort=created_asc&page_size=100", headers=auth_a).get_json()
    timestamps = [order["created_at"] for order in result["orders"]]
    assert timestamps == sorted(timestamps)
    for order in result["orders"]:
        assert client.get(f"/v1/orders/{order['order_id']}", headers=auth_a).get_json() == order
    filtered = client.get(f"/v1/orders?keyword={paid_order['order_id']}&status=paid", headers=auth_a).get_json()
    assert filtered["total"] == 1
    assert filtered["orders"] == [paid_order]
    first = client.get("/v1/orders?page_size=2&page=1&sort=amount_desc", headers=auth_a).get_json()
    second = client.get("/v1/orders?page_size=2&page=2&sort=amount_desc", headers=auth_a).get_json()
    assert len(first["orders"]) == len(second["orders"]) == 2
    assert not {order["order_id"] for order in first["orders"]} & {order["order_id"] for order in second["orders"]}
    assert first["orders"][-1]["total_amount"] >= second["orders"][0]["total_amount"]


@pytest.mark.parametrize("path", [
    "/v1/orders?page=zero", "/v1/products?page_size=-1", "/v1/customers?page=0",
    "/v1/orders?start_date=invalid", "/v1/orders?start_date=2027-01-01&end_date=2026-01-01",
    "/v1/analytics/trend?days=abc", "/v1/analytics/avg-order-value?days=367",
    "/v1/products/ranking?limit=-1",
])
def test_invalid_read_parameters(client, path):
    response = client.get(path)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("path, code", [
    ("/v1/orders/missing", 40401),
    ("/v1/products/missing/stats", 40402),
    ("/v1/inventory/missing", 40403),
])
def test_legacy_not_found_codes(client, path, code):
    response = client.get(path)
    assert response.status_code == 404
    assert response.get_json()["code"] == code


def test_all_expected_tables_exist(app):
    assert set(inspect(app.extensions["database"]).get_table_names()) == {
        "tenants", "users", "products", "orders", "refunds", "approval_requests", "idempotency_records", "audit_logs",
    }


@pytest.mark.parametrize("path, keys", READ_ROUTES[1:])
def test_authenticated_reads_stay_in_each_tenant(client, auth_a, auth_b, path, keys):
    path_b = path.replace("SHOE-WH-42", "B-SHOE-WH-42")
    response_a = client.get(path, headers=auth_a)
    response_b = client.get(path_b, headers=auth_b)
    assert response_a.status_code == response_b.status_code == 200
    assert set(response_b.get_json()) == keys
    assert response_a.get_json() != response_b.get_json()
    assert "B-SHOE" not in response_a.get_data(as_text=True)
    assert "王*明" not in response_b.get_data(as_text=True)


def test_cross_tenant_reads_and_writes_are_hidden(client, app, auth_a, auth_b, paid_order):
    order_b = client.get("/v1/orders", headers=auth_b).get_json()["orders"][0]
    for own_headers, foreign_order, foreign_sku in (
        (auth_a, order_b, "B-SHOE-WH-42"),
        (auth_b, paid_order, "SHOE-WH-42"),
    ):
        assert client.get(f"/v1/orders/{foreign_order['order_id']}", headers=own_headers).status_code == 404
        assert client.get(f"/v1/products/{foreign_sku}/stats", headers=own_headers).status_code == 404
        assert client.get(f"/v1/inventory/{foreign_sku}", headers=own_headers).status_code == 404
        assert client.post(f"/v1/orders/{foreign_order['order_id']}/refund", headers=own_headers,
                           json={"amount": 1}).status_code == 404
        assert client.patch(f"/v1/products/{foreign_sku}/price", headers=own_headers,
                            json={"price": 1}).status_code == 404
    assert client.get(f"/v1/orders/{paid_order['order_id']}", headers=auth_a).get_json() == paid_order
    assert client.get(f"/v1/orders/{order_b['order_id']}", headers=auth_b).get_json() == order_b
    with app.extensions["session_factory"]() as session:
        assert session.get(Product, ("merchant-12345", "SHOE-WH-42")).price == 29900
        assert session.get(Product, ("merchant-67890", "B-SHOE-WH-42")).price == 30900


def test_identity_cannot_be_overridden(client, auth_a, auth_b, paid_order):
    assert client.get("/v1/products?tenant_id=merchant-67890", headers=auth_a).status_code == 400
    forged = {**auth_b, "X-Tenant-ID": "merchant-12345", "X-User-ID": "user-a", "X-Role": "admin"}
    assert client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=forged,
                       json={"amount": 1}).status_code == 404
    products = client.get("/v1/products", headers=forged).get_json()["products"]
    assert all(product["sku"].startswith("B-") for product in products)
    assert client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a,
                        json={"price": 1, "tenant_id": "merchant-67890"}).status_code == 400


def test_same_resource_identifiers_remain_isolated(client, app, auth_a, auth_b, paid_order):
    with app.extensions["session_factory"].begin() as session:
        product = session.get(Product, ("merchant-12345", "SHOE-WH-42"))
        session.add(Product(tenant_id="merchant-67890", **product.to_dict(), stock=1, threshold=1, daily_avg=1))
        order_data = {key: value for key, value in paid_order.items() if key != "remaining_refundable_amount"}
        session.add(Order(tenant_id="merchant-67890", **order_data))
    assert client.patch("/v1/products/SHOE-WH-42/price", headers=auth_b, json={"price": 30000}).status_code == 200
    assert client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_b,
                       json={"amount": 100}).status_code == 201
    assert client.get("/v1/products/SHOE-WH-42/stats", headers=auth_a).get_json()["price"] == 29900
    assert client.get(f"/v1/orders/{paid_order['order_id']}", headers=auth_a).get_json()["refunded_amount"] == 0
    with app.extensions["session_factory"]() as session:
        assert session.scalar(select(func.count()).select_from(Refund).where(
            Refund.order_id == paid_order["order_id"], Refund.tenant_id == "merchant-12345"
        )) == 0


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}, {"Authorization": "Basic test-a"}, {"X-Tenant-ID": "merchant-12345"}])
def test_writes_require_authentication(client, paid_order, headers):
    assert client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=headers,
                       json={"amount": 1}).status_code == 401
    assert client.patch("/v1/products/SHOE-WH-42/price", headers=headers,
                        json={"price": 1}).status_code == 401


def test_invalid_credentials_never_fall_back_to_legacy_reads(client):
    response = client.get("/v1/products", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


def test_inactive_users_are_rejected(client, app, auth_a):
    with app.extensions["session_factory"].begin() as session:
        session.get(User, "user-a").active = False
    assert client.get("/v1/products", headers=auth_a).status_code == 401
    assert client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": 1}).status_code == 401


def test_strict_mode_requires_auth_for_all_business_reads(client, app, auth_a):
    app.config["LEGACY_READS"] = False
    for path, keys in READ_ROUTES[1:]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth_a).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_foreign_keys_and_amount_constraints(app, paid_order):
    with pytest.raises(IntegrityError), app.extensions["session_factory"].begin() as session:
        session.add(Refund(id="invalid", tenant_id="merchant-67890", order_id=paid_order["order_id"], amount=1))
    with pytest.raises(IntegrityError), app.extensions["session_factory"].begin() as session:
        order = session.get(Order, ("merchant-12345", paid_order["order_id"]))
        order.refunded_amount = order.total_amount + 1


def test_tokens_enable_strict_reads_by_default(app_module, tmp_path, monkeypatch):
    monkeypatch.delenv("LEGACY_READS", raising=False)
    strict_app = app_module.create_app({
        "TESTING": True, "DATABASE_URL": f"sqlite:///{tmp_path / 'strict.sqlite3'}",
        "API_TOKENS": {"test-a": "user-a"}, "SEED_DAYS": 1,
    })
    try:
        strict_client = strict_app.test_client()
        assert strict_client.get("/v1/products").status_code == 401
        assert strict_client.get("/v1/products", headers={"Authorization": "Bearer test-a"}).status_code == 200
    finally:
        strict_app.extensions["database"].dispose()


def test_legacy_default_without_tokens(app_module, tmp_path, monkeypatch):
    monkeypatch.delenv("LEGACY_READS", raising=False)
    legacy_app = app_module.create_app({
        "TESTING": True, "DATABASE_URL": f"sqlite:///{tmp_path / 'legacy.sqlite3'}",
        "API_TOKENS": {}, "SEED_DAYS": 1,
    })
    try:
        legacy_client = legacy_app.test_client()
        assert legacy_client.get("/v1/products").status_code == 200
        assert legacy_client.patch("/v1/products/SHOE-WH-42/price", json={"price": 1}).status_code == 401
        assert legacy_client.get("/v1/products/B-SHOE-WH-42/stats").status_code == 404
    finally:
        legacy_app.extensions["database"].dispose()


def test_second_tenant_can_write_without_affecting_first(client, app, auth_a, auth_b):
    before_a = client.get("/v1/analytics/refunds?period=today", headers=auth_a).get_json()
    order_b = client.get("/v1/orders", headers=auth_b).get_json()["orders"][0]
    with app.extensions["session_factory"].begin() as session:
        order = session.get(Order, ("merchant-67890", order_b["order_id"]))
        order.status = "paid"
        order.refunded_amount = 0
    assert client.post(f"/v1/orders/{order_b['order_id']}/refund", headers=auth_b,
                       json={"amount": 100}).status_code == 201
    assert client.patch("/v1/products/B-SHOE-WH-42/price", headers=auth_b,
                        json={"price": 31000}).status_code == 200
    assert client.get("/v1/products/B-SHOE-WH-42/stats", headers=auth_b).get_json()["price"] == 31000
    assert client.get("/v1/products/SHOE-WH-42/stats", headers=auth_a).get_json()["price"] == 29900
    assert client.get("/v1/analytics/refunds?period=today", headers=auth_a).get_json() == before_a
