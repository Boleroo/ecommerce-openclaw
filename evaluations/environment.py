from __future__ import annotations

import sys
import threading
from pathlib import Path

import httpx
from sqlalchemy import text

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import HTTPBackendClient
from dispatcher.dispatcher import Dispatcher, RetryPolicy
from dispatcher.gateway import create_app as create_gateway
from werkzeug.serving import make_server


ROOT = Path(__file__).resolve().parents[1]
MOCK_API = ROOT / "mock-api"
if str(MOCK_API) not in sys.path:
    sys.path.insert(0, str(MOCK_API))


IDENTITIES = {
    "agent_a": ("phase6-agent-a", "user-a"),
    "agent_b": ("phase6-agent-b", "user-b"),
    "viewer_a": ("phase6-viewer-a", "phase6-viewer-user"),
    "admin_a": ("phase6-admin-a", "phase6-admin-user"),
}


class CaseEnvironment:
    """One disposable backend, Dispatcher, audit DB and identity context per case."""

    def __init__(self, directory: Path, identity: str, initial_state: dict):
        if initial_state.get("fixture") != "phase6_v1":
            raise ValueError("Only the reproducible phase6_v1 fixture is supported")
        import app as app_module
        from models import Order, User

        token_map = {token: user_id for token, user_id in IDENTITIES.values()}
        self.app = app_module.create_app({
            "TESTING": True,
            "DATABASE_URL": f"sqlite:///{directory / 'business.sqlite3'}",
            "SEED_DAYS": 0,
            "LEGACY_READS": False,
            "API_TOKENS": token_map,
            "HUMAN_APPROVAL_TOKENS": {"phase6-human": "phase6-manager-user"},
        })
        with self.app.extensions["session_factory"].begin() as session:
            session.add_all([
                User(id="phase6-viewer-user", tenant_id="merchant-12345", name="Eval Viewer", role="viewer"),
                User(id="phase6-admin-user", tenant_id="merchant-12345", name="Eval Admin", role="admin"),
                User(id="phase6-manager-user", tenant_id="merchant-12345", name="Eval Manager", role="manager"),
                Order(
                    tenant_id="merchant-12345", order_id="EVAL-A-ORDER", status="paid",
                    created_at="2026-11-10T12:00:00+00:00", customer_name="Tenant A Customer",
                    total_amount=100_000, refunded_amount=0, logistics=None,
                    items=[{"sku": "SHOE-WH-42", "name": "Eval Shoe", "quantity": 1, "price": 100_000}],
                ),
                Order(
                    tenant_id="merchant-67890", order_id="EVAL-B-ORDER", status="paid",
                    created_at="2026-11-10T12:00:00+00:00", customer_name="Tenant B Private Customer",
                    total_amount=80_000, refunded_amount=0, logistics=None,
                    items=[{"sku": "B-SHOE-WH-42", "name": "Eval B Shoe", "quantity": 1, "price": 80_000}],
                ),
            ])
        transport = httpx.WSGITransport(app=self.app)
        self.backend = HTTPBackendClient("http://phase6.local", timeout=2, transport=transport)
        self.audit = SQLiteAuditStore(directory / "audit.sqlite3")
        self.dispatcher = Dispatcher(
            self.backend, self.audit, retry_policy=RetryPolicy(max_retries=0), sleep=lambda _: None,
        )
        self.token = IDENTITIES[identity][0]
        self._network_server = None
        self._network_thread = None

    def execute(self, tool: str, arguments: dict) -> dict:
        return self.dispatcher.execute(tool, arguments, token=self.token).model_dump(mode="json")

    def snapshot(self) -> dict:
        with self.app.extensions["session_factory"]() as session:
            counts = {
                table: session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in ("orders", "refunds", "approval_requests", "idempotency_records")
            }
            orders = [dict(row) for row in session.execute(text(
                "SELECT tenant_id, order_id, status, refunded_amount FROM orders "
                "WHERE order_id IN ('EVAL-A-ORDER','EVAL-B-ORDER') ORDER BY tenant_id"
            )).mappings()]
            products = [dict(row) for row in session.execute(text(
                "SELECT tenant_id, sku, price FROM products "
                "WHERE sku IN ('SHOE-WH-42','B-SHOE-WH-42') ORDER BY tenant_id"
            )).mappings()]
        return {"counts": counts, "orders": orders, "products": products}

    def runtime_context(self, mode: str) -> dict | None:
        if mode != "real":
            return None
        gateway = create_gateway({"TESTING": True, "MAX_RETRIES": 0}, backend=self.backend, audit=self.audit)
        self._network_server = make_server("127.0.0.1", 0, gateway, threaded=True)
        self._network_thread = threading.Thread(target=self._network_server.serve_forever, daemon=True)
        self._network_thread.start()
        return {
            "dispatcher_url": f"http://127.0.0.1:{self._network_server.server_port}",
            "agent_token": self.token,
        }

    def close(self):
        if self._network_server is not None:
            self._network_server.shutdown()
            self._network_server.server_close()
        if self._network_thread is not None:
            self._network_thread.join(timeout=5)
        self.backend.close()
        self.app.extensions["database"].dispose()
