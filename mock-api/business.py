from collections import Counter
from uuid import uuid4

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select, text

from models import Order, Product, Refund, Tenant, User
from policy import ROLE_PERMISSIONS, classify_price_change, classify_refund


operations = Blueprint("operations", __name__)
REFUNDABLE_STATUSES = {"paid", "shipped", "delivered"}
MIN_PRICE = 1
MAX_PRICE = 100_000_000


class BusinessError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status


def positive_integer(value, field, maximum=None):
    if type(value) is not int or value <= 0:
        raise BusinessError("INVALID_ARGUMENT", f"{field} must be a positive integer in cents")
    if maximum is not None and value > maximum:
        raise BusinessError("PRICE_OUT_OF_RANGE", f"{field} must be between {MIN_PRICE} and {maximum} cents")
    return value


def body_fields(required, optional=()):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) - set(required) - set(optional) or set(required) - set(body):
        raise BusinessError("INVALID_ARGUMENT", f"Expected JSON fields: {', '.join(required)}")
    return body


def require_permission(permission):
    if permission not in g.permissions:
        raise BusinessError("PERMISSION_DENIED", "Required permission is missing", 403)


def products():
    return [product.to_dict() for product in g.db.scalars(
        select(Product).where(Product.tenant_id == g.tenant_id).order_by(Product.position, Product.sku)
    )]


def inventory():
    return {
        product.sku: {field: getattr(product, field) for field in ("stock", "threshold", "daily_avg")}
        for product in g.db.scalars(select(Product).where(Product.tenant_id == g.tenant_id))
    }


def tenant():
    return g.db.get(Tenant, g.tenant_id)


def orders_between(start_date, end_date, status_filter=None, keyword=None):
    from datetime import date

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (ValueError, TypeError):
        raise BusinessError("INVALID_ARGUMENT", "Dates must use YYYY-MM-DD")
    if start > end:
        raise BusinessError("INVALID_ARGUMENT", "start_date must not exceed end_date")
    statement = select(Order).where(
        Order.tenant_id == g.tenant_id,
        Order.created_at >= start.isoformat(),
        func.substr(Order.created_at, 1, 10) <= end.isoformat(),
    ).order_by(Order.created_at, Order.order_id)
    if status_filter:
        statement = statement.where(Order.status == status_filter)
    result = [order.to_dict() for order in g.db.scalars(statement)]
    if keyword:
        keyword = keyword.lower()
        result = [order for order in result if keyword in order["order_id"].lower() or any(
            keyword in item["name"].lower() for item in order["items"]
        )]
    return result


def refund_reasons(order_ids):
    reasons = Counter(g.db.scalars(select(Refund.reason).where(
        Refund.tenant_id == g.tenant_id, Refund.order_id.in_(order_ids)
    )))
    return [{"reason": reason, "count": count} for reason, count in reasons.most_common()]


@operations.post("/v1/orders/<order_id>/refund")
def refund_order(order_id):
    body = body_fields(("amount",), ("reason",))
    amount = positive_integer(body["amount"], "amount")
    reason = body.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 500:
        raise BusinessError("INVALID_ARGUMENT", "reason must be a string of at most 500 characters")
    require_permission("orders:refund")
    with g.db.begin():
        g.db.execute(text("BEGIN IMMEDIATE"))
        order = g.db.get(Order, (g.tenant_id, order_id))
        if order is None:
            raise BusinessError("RESOURCE_NOT_FOUND", "Order not found", 404)
        if order.status not in REFUNDABLE_STATUSES:
            raise BusinessError("INVALID_BUSINESS_STATE", "Order status does not permit refunds", 409)
        if amount > order.total_amount - order.refunded_amount:
            raise BusinessError("REFUND_AMOUNT_EXCEEDED", "Amount exceeds the remaining refundable amount", 409)
        decision = classify_refund(amount)
        if decision.approval_required:
            raise BusinessError("APPROVAL_REQUIRED", decision.approval_message, 403)
        order.refunded_amount += amount
        if order.refunded_amount == order.total_amount:
            order.status = "refunded"
        refund = Refund(
            id=str(uuid4()), tenant_id=g.tenant_id, order_id=order_id,
            user_id=g.user_id, amount=amount, reason=reason,
        )
        g.db.add(refund)
        g.db.flush()
        response = {"refund": refund.to_dict(), "order": order.to_dict()}
    return jsonify(response), 201


@operations.patch("/v1/products/<sku>/price")
def change_price(sku):
    body = body_fields(("price",))
    price = positive_integer(body["price"], "price", MAX_PRICE)
    require_permission("products:price")
    with g.db.begin():
        g.db.execute(text("BEGIN IMMEDIATE"))
        product = g.db.get(Product, (g.tenant_id, sku))
        if product is None:
            raise BusinessError("RESOURCE_NOT_FOUND", "Product not found", 404)
        previous_price = product.price
        decision = classify_price_change(previous_price, price)
        if decision.approval_required:
            raise BusinessError("APPROVAL_REQUIRED", decision.approval_message, 403)
        product.price = price
        response = {"product": product.to_dict(), "previous_price": previous_price}
    return jsonify(response)


@operations.patch("/v1/users/<user_id>/role")
def change_user_role(user_id):
    body = body_fields(("role",))
    role = body["role"]
    if not isinstance(role, str) or role not in ROLE_PERMISSIONS:
        raise BusinessError("INVALID_ARGUMENT", "role must be viewer, operator, manager or admin")
    require_permission("roles:manage")
    with g.db.begin():
        g.db.execute(text("BEGIN IMMEDIATE"))
        user = g.db.get(User, user_id)
        if user is None or user.tenant_id != g.tenant_id:
            raise BusinessError("RESOURCE_NOT_FOUND", "User not found", 404)
        user.role = role
        response = {"user_id": user.id, "role": user.role}
    return jsonify(response)
