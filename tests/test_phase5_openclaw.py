import json
import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from werkzeug.serving import make_server

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import HTTPBackendClient
from dispatcher.gateway import create_app as create_gateway
from dispatcher.registry import REGISTRY
from scripts.generate_openclaw_tools import agent_input_schema


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "openclaw-plugin"


@contextmanager
def running_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(scope="session")
def built_plugin():
    subprocess.run(["npm", "run", "build"], cwd=PLUGIN, check=True, capture_output=True, text=True)
    return PLUGIN


@pytest.fixture
def phase5_stack(app_module, tmp_path, built_plugin):
    from models import Order, Product, User

    database_path = tmp_path / "phase5-business.sqlite3"
    app = app_module.create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database_path}",
        "SEED_DAYS": 0,
        "LEGACY_READS": False,
        "API_TOKENS": {
            "phase5-agent-a": "user-a",
            "phase5-agent-b": "user-b",
            "phase5-viewer": "phase5-viewer-user",
            "phase5-admin": "phase5-admin-user",
        },
        "HUMAN_APPROVAL_TOKENS": {"phase5-human-manager": "phase5-manager-user"},
    })
    with app.extensions["session_factory"].begin() as session:
        session.add_all([
            User(id="phase5-viewer-user", tenant_id="merchant-12345", name="Viewer", role="viewer"),
            User(id="phase5-admin-user", tenant_id="merchant-12345", name="Admin", role="admin"),
            User(id="phase5-manager-user", tenant_id="merchant-12345", name="Manager", role="manager"),
            Order(
                tenant_id="merchant-12345", order_id="PHASE5-ORDER", status="paid",
                created_at="2026-11-09T12:00:00+00:00", customer_name="Agent Customer",
                total_amount=100_000, refunded_amount=0, logistics=None,
                items=[{"sku": "PHASE5-SKU", "name": "Agent Item", "quantity": 1, "price": 100_000}],
            ),
            Order(
                tenant_id="merchant-67890", order_id="PHASE5-FOREIGN", status="paid",
                created_at="2026-11-09T12:00:00+00:00", customer_name="Foreign Customer",
                total_amount=100_000, refunded_amount=0, logistics=None,
                items=[{"sku": "B-PHASE5", "name": "Foreign Item", "quantity": 1, "price": 100_000}],
            ),
            Product(
                tenant_id="merchant-12345", sku="PHASE5-SKU", name="Agent Item", category="Test",
                price=10_000, status="on_sale", listed_at="2026-11-09",
                stock=20, threshold=5, daily_avg=2,
            ),
        ])

    audit = SQLiteAuditStore(tmp_path / "phase5-dispatcher.sqlite3")
    with running_server(app) as backend_url:
        backend = HTTPBackendClient(backend_url, timeout=1.0)
        gateway = create_gateway({"TESTING": True, "MAX_RETRIES": 0}, backend=backend, audit=audit)
        with running_server(gateway) as dispatcher_url:
            yield {
                "app": app,
                "database_path": database_path,
                "dispatcher_url": dispatcher_url,
                "invoke": lambda tool, arguments, token="phase5-agent-a": invoke_adapter(
                    tool, arguments, token, dispatcher_url
                ),
            }
        backend.close()
    app.extensions["database"].dispose()


def invoke_adapter(tool, arguments, token, dispatcher_url):
    environment = os.environ.copy()
    environment.update({
        "OPENCLAW_ECOMMERCE_API_TOKEN": token,
        "OPENCLAW_ECOMMERCE_DISPATCHER_URL": dispatcher_url,
        "OPENCLAW_ECOMMERCE_TIMEOUT_MS": "2000",
    })
    completed = subprocess.run(
        ["node", str(ROOT / "tests" / "phase5_invoke_adapter.mjs"), tool, json.dumps(arguments)],
        cwd=ROOT, env=environment, check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(completed.stdout)


def test_registration_and_generated_schema_match_dispatcher(built_plugin):
    manifest = json.loads((built_plugin / "openclaw.plugin.json").read_text())
    assert manifest["contracts"]["tools"] == list(REGISTRY.tools)
    assert len(manifest["contracts"]["tools"]) == 24
    assert not any(name in manifest["contracts"]["tools"] for name in ("approve_approval", "reject_approval"))
    assert set(manifest["toolMetadata"]) == {
        name for name, tool in REGISTRY.tools.items() if tool.method != "GET"
    }
    generated = (built_plugin / "src" / "registry.generated.ts").read_text()
    assert "phase5-agent-a" not in generated
    assert "tenant_id" not in generated


def test_generated_tool_schemas_are_valid_and_preserve_registry_contracts():
    for name, tool in REGISTRY.tools.items():
        schema = agent_input_schema(tool)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False
        if name in {"refund_order", "change_price"}:
            assert "idempotency_key" in schema["required"]
        else:
            assert schema == tool.input_model.model_json_schema()


@pytest.mark.parametrize(
    "tool,arguments,identity_field,identity",
    [
        ("get_order", {"order_id": "PHASE5-ORDER"}, "order_id", "PHASE5-ORDER"),
        ("product_stats", {"sku": "PHASE5-SKU"}, "sku", "PHASE5-SKU"),
        ("inventory_sku", {"sku": "PHASE5-SKU"}, "sku", "PHASE5-SKU"),
    ],
)
def test_read_only_poc_adapter_dispatcher_backend_sqlite(phase5_stack, tool, arguments, identity_field, identity):
    result = phase5_stack["invoke"](tool, arguments)
    assert result["success"] is True
    assert result["data"][identity_field] == identity
    assert result["trace_id"]


def test_structured_errors_and_trace_ids(phase5_stack):
    invalid = phase5_stack["invoke"]("get_order", {"order_id": "PHASE5-ORDER", "tenant_id": "merchant-67890"})
    missing = phase5_stack["invoke"]("get_order", {"order_id": "DOES-NOT-EXIST"})
    assert invalid["error"]["code"] == "INVALID_ARGUMENT" and invalid["error"]["recoverable"] is True
    assert missing["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert invalid["trace_id"] and missing["trace_id"]


def test_runtime_identity_rbac_and_cross_tenant_denial(phase5_stack):
    foreign = phase5_stack["invoke"]("get_order", {"order_id": "PHASE5-ORDER"}, "phase5-agent-b")
    denied = phase5_stack["invoke"](
        "change_price",
        {"sku": "PHASE5-SKU", "price": 10_100, "idempotency_key": "viewer-price"},
        "phase5-viewer",
    )
    assert foreign["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert denied["error"]["code"] == "PERMISSION_DENIED"


def test_approval_separation_idempotency_and_execution(phase5_stack):
    arguments = {
        "order_id": "PHASE5-ORDER",
        "amount": 20_000,
        "reason": "Phase 5 approval",
        "idempotency_key": "phase5-high-risk-refund",
    }
    first = phase5_stack["invoke"]("refund_order", arguments)
    second = phase5_stack["invoke"]("refund_order", arguments)
    assert first["error"]["code"] == second["error"]["code"] == "APPROVAL_REQUIRED"
    approval = first["error"]["approval"]
    assert approval["id"] == second["error"]["approval"]["id"]

    # Agent credentials cannot make the human decision.
    with phase5_stack["app"].test_client() as client:
        agent_decision = client.post(
            f"/v1/approvals/{approval['id']}/approve",
            headers={"Authorization": "Bearer phase5-agent-a"}, json={},
        )
        human_decision = client.post(
            f"/v1/approvals/{approval['id']}/approve",
            headers={"Authorization": "Bearer phase5-human-manager"}, json={},
        )
    assert agent_decision.status_code == 403
    assert human_decision.status_code == 200

    completed = phase5_stack["invoke"]("execute_approval", {"approval_id": approval["id"]})
    replay = phase5_stack["invoke"]("execute_approval", {"approval_id": approval["id"]})
    assert completed["success"] is True and completed["data"]["status"] == "COMPLETED"
    assert replay["data"] == completed["data"]

    from models import IdempotencyRecord, Refund
    with phase5_stack["app"].extensions["session_factory"]() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_backend_unavailability_is_structured_and_not_retried(built_plugin):
    result = invoke_adapter("list_products", {}, "phase5-agent-a", "http://127.0.0.1:9")
    assert result["success"] is False
    assert result["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert result["trace_id"]


def test_supported_agent_surfaces_do_not_contain_direct_backend_bypass():
    for skill in (ROOT / "skills" / "platform").glob("*/SKILL.md"):
        content = skill.read_text()
        assert "curl -" not in content
        assert "JD_API_ENDPOINT" not in content
    adapter = (PLUGIN / "src" / "adapter.ts").read_text()
    assert "OPENCLAW_ECOMMERCE_API_TOKEN" in adapter
    assert "DISPATCHER_BACKEND_URL" not in adapter
    policy = json.loads((ROOT / "config" / "openclaw-secure-agent.example.json").read_text())
    denied = set(policy["tools"]["deny"])
    assert {"group:runtime", "group:web", "group:ui"} <= denied
    launcher = (ROOT / "start.sh").read_text()
    assert "-u HUMAN_APPROVAL_TOKENS" in launcher
    assert "-u API_TOKENS" in launcher
    assert "JD_API_ENDPOINT" not in launcher
