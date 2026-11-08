import importlib
import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mock-api"))


@pytest.fixture(scope="session")
def app_module():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", "sqlite:///:memory:")
        patch.setenv("API_TOKENS", "{}")
        module = importlib.import_module("app")
    yield module
    module.app.extensions["database"].dispose()


@pytest.fixture
def app(app_module, tmp_path):
    application = app_module.create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'business.sqlite3'}",
        "API_TOKENS": {"test-a": "user-a", "test-b": "user-b"},
        "LEGACY_READS": True,
        "SEED_DAYS": 3,
    })
    yield application
    application.extensions["database"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_a():
    return {"Authorization": "Bearer test-a"}


@pytest.fixture
def auth_b():
    return {"Authorization": "Bearer test-b"}


@pytest.fixture
def paid_order(app, client, auth_a):
    from models import Order

    with app.extensions["session_factory"].begin() as session:
        session.add(Order(
            tenant_id="merchant-12345", order_id="TEST-PAID", status="paid",
            created_at=f"{date.today()}T12:00:00+08:00", customer_name="测试客户",
            total_amount=59800, refunded_amount=0,
            items=[{"sku": "SHOE-WH-42", "name": "经典白色运动鞋 42码", "quantity": 2, "price": 29900}],
            logistics=None,
        ))
    return client.get("/v1/orders/TEST-PAID", headers=auth_a).get_json()
