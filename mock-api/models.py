from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, ForeignKey, ForeignKeyConstraint,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base


Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    customer_profile = Column(JSON, nullable=False)
    customers = Column(JSON, nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'operator', 'manager', 'admin')", name="valid_user_role"),
    )

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="operator")
    active = Column(Boolean, nullable=False, default=True)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price BETWEEN 1 AND 100000000", name="valid_price"),
        CheckConstraint("stock >= 0", name="valid_stock"),
    )

    tenant_id = Column(String, ForeignKey("tenants.id"), primary_key=True)
    sku = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    listed_at = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    stock = Column(Integer, nullable=False)
    threshold = Column(Integer, nullable=False)
    daily_avg = Column(Integer, nullable=False)

    def to_dict(self):
        return {field: getattr(self, field) for field in (
            "sku", "name", "category", "price", "status", "listed_at"
        )}


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_amount > 0", name="positive_order_amount"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= total_amount",
            name="valid_refunded_amount",
        ),
    )

    tenant_id = Column(String, ForeignKey("tenants.id"), primary_key=True)
    order_id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    total_amount = Column(Integer, nullable=False)
    refunded_amount = Column(Integer, nullable=False, default=0)
    items = Column(JSON, nullable=False)
    logistics = Column(JSON, nullable=True)

    def to_dict(self):
        result = {field: getattr(self, field) for field in (
            "order_id", "created_at", "status", "customer_name", "total_amount",
            "items", "logistics", "refunded_amount",
        )}
        result["remaining_refundable_amount"] = self.total_amount - self.refunded_amount
        return result


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_id"], ["orders.tenant_id", "orders.order_id"]
        ),
        CheckConstraint("amount > 0", name="positive_refund_amount"),
    )

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    order_id = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    reason = Column(String, nullable=False, default="")
    created_at = Column(String, nullable=False, default=utc_now)

    def to_dict(self):
        return {field: getattr(self, field) for field in (
            "id", "order_id", "amount", "reason", "created_at"
        )}


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    initiator_id = Column(String, ForeignKey("users.id"), nullable=False)
    operation = Column(String, nullable=False)
    parameters = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(String, nullable=False, default=utc_now)
    expires_at = Column(String, nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    key = Column(String, nullable=False)
    request_digest = Column(String, nullable=False)
    status = Column(String, nullable=False)
    response = Column(JSON, nullable=True)
    created_at = Column(String, nullable=False, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    operation = Column(String, nullable=False)
    resource_id = Column(String, nullable=False)
    details = Column(JSON, nullable=False)
    created_at = Column(String, nullable=False, default=utc_now)
