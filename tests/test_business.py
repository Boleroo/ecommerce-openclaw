from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from business import BusinessError, MAX_PRICE, positive_integer
from models import Order, Product, Refund


@pytest.mark.parametrize("value", [None, True, False, "100", 1.0, 0, -1, [], {}])
def test_money_rejects_non_positive_integers(value):
    with pytest.raises(BusinessError) as failure:
        positive_integer(value, "amount")
    assert failure.value.code == "INVALID_ARGUMENT"


def test_money_limits():
    assert positive_integer(1, "price", MAX_PRICE) == 1
    assert positive_integer(MAX_PRICE, "price", MAX_PRICE) == MAX_PRICE
    with pytest.raises(BusinessError) as failure:
        positive_integer(MAX_PRICE + 1, "price", MAX_PRICE)
    assert failure.value.code == "PRICE_OUT_OF_RANGE"


def test_partial_and_full_refund(client, app, auth_a, paid_order):
    order_id = paid_order["order_id"]
    before = client.get("/v1/analytics/refunds?period=today", headers=auth_a).get_json()
    partial = client.post(f"/v1/orders/{order_id}/refund", headers=auth_a,
                          json={"amount": 100, "reason": "partial return"})
    assert partial.status_code == 201
    result = partial.get_json()
    assert result["refund"]["amount"] == 100
    assert result["order"]["status"] == "paid"
    assert result["order"]["remaining_refundable_amount"] == paid_order["total_amount"] - 100
    assert client.get(f"/v1/orders/{order_id}", headers=auth_a).get_json() == result["order"]
    after = client.get("/v1/analytics/refunds?period=today", headers=auth_a).get_json()
    assert after["refund_amount"] == before["refund_amount"] + 100
    assert after["refunded_orders"] == before["refunded_orders"] + 1
    assert {"reason": "partial return", "count": 1} in after["top_refund_reasons"]
    final = client.post(f"/v1/orders/{order_id}/refund", headers=auth_a,
                        json={"amount": paid_order["total_amount"] - 100})
    assert final.status_code == 201
    assert final.get_json()["order"]["status"] == "refunded"
    assert final.get_json()["order"]["remaining_refundable_amount"] == 0
    listed = client.get("/v1/orders?status=refunded&page_size=100", headers=auth_a).get_json()
    assert order_id in {order["order_id"] for order in listed["orders"]}
    with app.extensions["session_factory"]() as session:
        refunds = session.scalars(select(Refund).where(
            Refund.tenant_id == "merchant-12345", Refund.order_id == order_id
        )).all()
        assert len(refunds) == 2
        assert sum(refund.amount for refund in refunds) == paid_order["total_amount"]
        assert all(refund.user_id == "user-a" for refund in refunds)


@pytest.mark.parametrize("status", ["paid", "shipped", "delivered"])
def test_refundable_statuses(client, app, auth_a, paid_order, status):
    with app.extensions["session_factory"].begin() as session:
        session.get(Order, ("merchant-12345", paid_order["order_id"])).status = status
    assert client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
                       json={"amount": 1}).status_code == 201


@pytest.mark.parametrize("status", ["pending", "cancelled", "refunded"])
def test_invalid_refund_state(client, app, auth_a, paid_order, status):
    with app.extensions["session_factory"].begin() as session:
        order = session.get(Order, ("merchant-12345", paid_order["order_id"]))
        order.status = status
    response = client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
                           json={"amount": 1})
    assert response.status_code == 409
    assert response.get_json()["code"] == "INVALID_BUSINESS_STATE"


@pytest.mark.parametrize("body", [
    {}, {"amount": 0}, {"amount": -1}, {"amount": True}, {"amount": 1.5},
    {"amount": "100"}, {"amount": None}, {"amount": []}, {"amount": {}},
    {"amount": 1, "tenant_id": "merchant-67890"},
    {"amount": 1, "reason": 42}, {"amount": 1, "reason": "x" * 501}, [],
])
def test_invalid_refund_payload(client, auth_a, paid_order, body):
    response = client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a, json=body)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_ARGUMENT"
    assert client.get(f"/v1/orders/{paid_order['order_id']}", headers=auth_a).get_json() == paid_order


def test_refund_exceeding_remaining_balance(client, auth_a, paid_order):
    path = f"/v1/orders/{paid_order['order_id']}/refund"
    assert client.post(path, headers=auth_a, json={"amount": 100}).status_code == 201
    response = client.post(path, headers=auth_a, json={"amount": paid_order["total_amount"]})
    assert response.status_code == 409
    assert response.get_json()["code"] == "REFUND_AMOUNT_EXCEEDED"
    assert client.get(path.removesuffix("/refund"), headers=auth_a).get_json()["refunded_amount"] == 100


@pytest.mark.parametrize("price", [28405, 30000, 31395])
def test_price_change_updates_reads(client, auth_a, price):
    response = client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": price})
    assert response.status_code == 200
    assert response.get_json()["previous_price"] == 29900
    assert response.get_json()["product"]["price"] == price
    products = client.get("/v1/products?keyword=SHOE-WH-42", headers=auth_a).get_json()["products"]
    assert products[0]["price"] == price
    assert client.get("/v1/products/SHOE-WH-42/stats", headers=auth_a).get_json()["price"] == price
    ranking = client.get("/v1/products/ranking?limit=100", headers=auth_a).get_json()["ranking"]
    assert next(product for product in ranking if product["sku"] == "SHOE-WH-42")["avg_price"] == price


@pytest.mark.parametrize("body, code", [
    ({}, "INVALID_ARGUMENT"), ({"price": 0}, "INVALID_ARGUMENT"),
    ({"price": -1}, "INVALID_ARGUMENT"), ({"price": True}, "INVALID_ARGUMENT"),
    ({"price": 1.5}, "INVALID_ARGUMENT"), ({"price": "100"}, "INVALID_ARGUMENT"),
    ({"price": None}, "INVALID_ARGUMENT"), ({"price": MAX_PRICE + 1}, "PRICE_OUT_OF_RANGE"),
    ({"price": 100, "role": "admin"}, "INVALID_ARGUMENT"),
])
def test_invalid_price(client, auth_a, body, code):
    response = client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a, json=body)
    assert response.status_code == 400
    assert response.get_json()["code"] == code
    assert client.get("/v1/products/SHOE-WH-42/stats", headers=auth_a).get_json()["price"] == 29900


@pytest.mark.parametrize("payload, content_type", [("{", "application/json"), ("price=100", "text/plain"), ("null", "application/json")])
def test_malformed_json(client, auth_a, payload, content_type):
    response = client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a,
                            data=payload, content_type=content_type)
    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_ARGUMENT"


def test_missing_resources(client, auth_a):
    refund = client.post("/v1/orders/missing/refund", headers=auth_a, json={"amount": 1})
    price = client.patch("/v1/products/missing/price", headers=auth_a, json={"price": 1})
    for response in (refund, price):
        assert response.status_code == 404
        assert response.get_json()["code"] == "RESOURCE_NOT_FOUND"


def test_restart_preserves_changes_and_item_prices(client, app, app_module, auth_a, paid_order):
    sku = paid_order["items"][0]["sku"]
    assert client.patch(f"/v1/products/{sku}/price", headers=auth_a, json={"price": 30000}).status_code == 200
    assert client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
                       json={"amount": 500}).status_code == 201
    with app.extensions["session_factory"]() as session:
        counts_before = [session.scalar(select(func.count()).select_from(model)) for model in (Order, Product, Refund)]
    app.extensions["database"].dispose()
    restarted = app_module.create_app(dict(app.config))
    try:
        restarted_client = restarted.test_client()
        assert restarted_client.get(f"/v1/products/{sku}/stats", headers=auth_a).get_json()["price"] == 30000
        order = restarted_client.get(f"/v1/orders/{paid_order['order_id']}", headers=auth_a).get_json()
        assert order["refunded_amount"] == 500
        assert order["items"] == paid_order["items"]
        assert order["total_amount"] == paid_order["total_amount"]
        with restarted.extensions["session_factory"]() as session:
            assert [session.scalar(select(func.count()).select_from(model)) for model in (Order, Product, Refund)] == counts_before
    finally:
        restarted.extensions["database"].dispose()


def test_refund_rolls_back_when_insert_fails(client, app, auth_a, paid_order):
    def fail_refund_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO refunds"):
            raise SQLAlchemyError("simulated insert failure")

    engine = app.extensions["database"]
    event.listen(engine, "before_cursor_execute", fail_refund_insert)
    try:
        response = client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
                               json={"amount": 100})
    finally:
        event.remove(engine, "before_cursor_execute", fail_refund_insert)
    assert response.status_code == 500
    assert "simulated" not in response.get_data(as_text=True)
    assert client.get(f"/v1/orders/{paid_order['order_id']}", headers=auth_a).get_json() == paid_order
    with app.extensions["session_factory"]() as session:
        assert session.scalar(select(func.count()).select_from(Refund).where(Refund.order_id == paid_order["order_id"])) == 0


def test_price_rolls_back_on_commit_failure(client, app, auth_a):
    def fail_commit(connection):
        raise SQLAlchemyError("simulated commit failure")

    engine = app.extensions["database"]
    event.listen(engine, "commit", fail_commit)
    try:
        response = client.patch("/v1/products/SHOE-WH-42/price", headers=auth_a, json={"price": 30000})
    finally:
        event.remove(engine, "commit", fail_commit)
    assert response.status_code == 500
    assert client.get("/v1/products/SHOE-WH-42/stats", headers=auth_a).get_json()["price"] == 29900


def test_concurrent_refunds_cannot_exceed_balance(app, auth_a, paid_order):
    barrier = Barrier(2)

    def refund():
        with app.test_client() as thread_client:
            barrier.wait(timeout=10)
            response = thread_client.post(f"/v1/orders/{paid_order['order_id']}/refund", headers=auth_a,
                                          json={"amount": paid_order["total_amount"]})
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda unused_index: refund(), range(2)))
    assert sorted(results) == [201, 409]
    with app.extensions["session_factory"]() as session:
        order = session.get(Order, ("merchant-12345", paid_order["order_id"]))
        assert order.refunded_amount == order.total_amount
        assert session.scalar(select(func.count()).select_from(Refund).where(
            Refund.tenant_id == "merchant-12345", Refund.order_id == order.order_id
        )) == 1
