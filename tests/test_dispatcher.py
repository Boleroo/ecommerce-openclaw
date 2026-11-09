import json
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from sqlalchemy import func, select

from dispatcher.audit import SQLiteAuditStore
from dispatcher.backend_client import BackendResponse, BackendTransportError, HTTPBackendClient
from dispatcher.dispatcher import Dispatcher, RetryPolicy
from dispatcher.gateway import create_app as create_gateway
from dispatcher.observation import Observation
from dispatcher.registry import REGISTRY, ToolRegistry
from dispatcher.schemas import OrderOutput, ProductOutput, RefundOutput
from models import Order, Refund, User


IDENTITY = {"user_id": "user-a", "tenant_id": "merchant-12345", "role": "operator", "permissions": ["business:read", "orders:refund", "products:price"]}
PRODUCTS = {"total": 0, "page": 1, "page_size": 20, "products": []}


class SequenceBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, endpoint, **kwargs):
        self.calls.append({"method": method, "endpoint": endpoint, **kwargs})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def audit(tmp_path):
    return SQLiteAuditStore(tmp_path / "audit.sqlite3")


@pytest.fixture
def backend(app):
    client = HTTPBackendClient("http://backend", transport=httpx.WSGITransport(app=app))
    yield client
    client.close()


@pytest.fixture
def dispatcher(backend, audit):
    return Dispatcher(backend, audit, sleep=lambda delay: None)


def assert_failure(observation, code, recoverable=None):
    assert observation.success is False
    assert observation.data is None
    assert observation.error.code == code
    if recoverable is not None:
        assert observation.error.recoverable is recoverable
    assert UUID(observation.trace_id).version == 4
    assert Observation.model_validate(observation.model_dump()) == observation


def test_registry_matches_every_business_route(app):
    original_business_routes = {
        (method, rule.rule.replace("<order_id>", "{order_id}").replace("<sku>", "{sku}").replace("<user_id>", "{user_id}"))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/v1/") and rule.rule != "/v1/auth/context"
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    assert {(tool.method, tool.endpoint) for tool in REGISTRY.tools.values()} == original_business_routes
    assert len(REGISTRY.tools) == 22
    for description in REGISTRY.describe():
        assert description["required_permissions"]
        assert description["risk_level"] in ("LOW", "HIGH", "DYNAMIC")
        for schema in (description["input_schema"], description["output_schema"]):
            Draft202012Validator.check_schema(schema)
            assert schema["additionalProperties"] is False
    with pytest.raises(ValueError):
        ToolRegistry([REGISTRY.get("get_order"), REGISTRY.get("get_order")])
    with pytest.raises(TypeError):
        REGISTRY.tools["new"] = REGISTRY.get("get_order")


@pytest.mark.parametrize("name", list(REGISTRY.tools))
@pytest.mark.parametrize("token, prefix", [("test-a", ""), ("test-b", "B-")])
def test_all_registered_tools_against_real_backend(dispatcher, audit, app, paid_order, name, token, prefix):
    if prefix:
        with app.extensions["session_factory"].begin() as session:
            session.add(Order(
                tenant_id="merchant-67890", order_id="B-TEST-PAID",
                **{key: value for key, value in paid_order.items() if key not in ("order_id", "remaining_refundable_amount")},
            ))
    arguments = {}
    if name in ("get_order", "refund_order"):
        arguments["order_id"] = prefix + "TEST-PAID"
    if name in ("product_stats", "inventory_sku", "change_price"):
        arguments["sku"] = prefix + "SHOE-WH-42"
    if name == "refund_order":
        arguments.update(amount=100, reason="return")
    if name == "change_price":
        arguments["price"] = (31000 if prefix else 30000)
    if name == "change_user_role":
        user_id = "user-b" if prefix else "user-a"
        with app.extensions["session_factory"].begin() as session:
            session.get(User, user_id).role = "admin"
        arguments.update(user_id=user_id, role="admin")
    observation = dispatcher.execute(name, arguments, token=token)
    assert observation.success, observation.model_dump()
    assert observation.error is None
    tool = REGISTRY.get(name)
    Draft202012Validator(tool.input_model.model_json_schema()).validate(arguments)
    Draft202012Validator(tool.output_model.model_json_schema()).validate(observation.data)
    record = audit.get(observation.trace_id)
    assert record["state"] == "completed"
    assert record["success"] == 1
    assert record["tenant_id"] == ("merchant-67890" if prefix else "merchant-12345")
    assert record["user_id"] == ("user-b" if prefix else "user-a")
    expected_requests = 2 if tool.method == "GET" or name == "change_user_role" else 3
    assert len(record["requests"]) == expected_requests
    assert token not in json.dumps(record)


@pytest.mark.parametrize("name, arguments", [
    ("get_order", {}), ("get_order", {"order_id": "../orders/stats"}),
    ("get_order", {"order_id": "JD?tenant_id=x"}), ("get_order", {"order_id": "JD%2Fstats"}),
    ("list_orders", {"tenant_id": "merchant-67890"}), ("list_orders", {"user_id": "user-b"}),
    ("list_orders", {"role": "admin"}), ("list_orders", {"status": "anything"}),
    ("list_orders", {"page": "1"}), ("list_orders", {"page": True}),
    ("list_orders", {"page_size": 101}), ("list_orders", {"start_date": "2026-02-30"}),
    ("list_orders", {"start_date": "2026-03-02", "end_date": "2026-03-01"}),
    ("list_products", {"keyword": "x" * 201}), ("inventory_alerts", {"threshold": 0}),
    ("product_ranking", {"sort": "unsupported"}), ("analytics_trend", {"days": 367}),
    ("analytics_revenue", {"period": "year"}), ("refund_order", {"order_id": "TEST-PAID", "amount": 1.5}),
    ("refund_order", {"order_id": "TEST-PAID", "amount": True}),
    ("refund_order", {"order_id": "TEST-PAID", "amount": "100"}),
    ("refund_order", {"order_id": "TEST-PAID", "amount": 0}),
    ("refund_order", {"order_id": "TEST-PAID", "amount": 1, "reason": "x" * 501}),
    ("change_price", {"sku": "SHOE-WH-42", "price": 100000001}),
    ("change_price", {"sku": "SHOE-WH-42", "price": 1, "approved": True}),
    ("list_products", []), ("list_products", None),
])
def test_invalid_input_never_reaches_backend(audit, name, arguments):
    backend = SequenceBackend([])
    observation = Dispatcher(backend, audit).execute(name, arguments, token="test-a")
    assert_failure(observation, "INVALID_ARGUMENT", True)
    assert observation.error.retry_hint
    assert backend.calls == []
    assert audit.get(observation.trace_id)["error_code"] == "INVALID_ARGUMENT"


def test_argument_recovery_requires_explicit_corrected_call(dispatcher, paid_order):
    invalid = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": "100"}, token="test-a")
    assert_failure(invalid, "INVALID_ARGUMENT", True)
    corrected = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert corrected.success
    assert corrected.data["refund"]["amount"] == 100
    assert corrected.trace_id != invalid.trace_id


@pytest.mark.parametrize("payload", [None, [], {}, {"tool": "list_products", "arguments": {}, "tenant_id": "merchant-67890"}])
def test_invalid_envelopes_are_audited(audit, payload):
    observation = Dispatcher(SequenceBackend([]), audit).dispatch(payload, token="test-a")
    assert_failure(observation, "INVALID_ARGUMENT", True)
    assert audit.get(observation.trace_id)["state"] == "completed"


def test_unknown_tool_is_audited_without_execution(audit):
    backend = SequenceBackend([])
    observation = Dispatcher(backend, audit).execute("execute_shell", {}, token="test-a")
    assert_failure(observation, "UNKNOWN_TOOL", True)
    assert backend.calls == []
    assert audit.get(observation.trace_id)["tool"] == "<unknown>"


@pytest.mark.parametrize("token", [None, "", "invalid", "test-a\r\nX-Tenant: b", "non ascii é"])
def test_dispatcher_always_requires_auth_even_with_legacy_reads(dispatcher, token):
    assert_failure(dispatcher.execute("list_products", {}, token=token), "UNAUTHENTICATED", False)


def test_backend_context_requires_active_user(client, app, auth_a):
    assert client.get("/v1/auth/context").status_code == 401
    assert client.get("/v1/auth/context", headers=auth_a).get_json() == IDENTITY
    with app.extensions["session_factory"].begin() as session:
        session.get(User, "user-a").active = False
    assert client.get("/v1/auth/context", headers=auth_a).status_code == 401


def test_revoked_token_is_rechecked_for_each_call(dispatcher, app):
    assert dispatcher.execute("list_products", {}, token="test-a").success
    with app.extensions["session_factory"].begin() as session:
        session.get(User, "user-a").active = False
    assert_failure(dispatcher.execute("list_products", {}, token="test-a"), "UNAUTHENTICATED", False)


def test_permissions_checked_before_preflight_or_write(audit):
    backend = SequenceBackend([BackendResponse(200, {**IDENTITY, "permissions": ["business:read"]})])
    observation = Dispatcher(backend, audit).execute("change_price", {"sku": "SHOE-WH-42", "price": 1}, token="test-a")
    assert_failure(observation, "PERMISSION_DENIED", False)
    assert len(backend.calls) == 1


@pytest.mark.parametrize("name, arguments", [
    ("get_order", {"order_id": "TEST-PAID"}),
    ("refund_order", {"order_id": "TEST-PAID", "amount": 100}),
    ("product_stats", {"sku": "SHOE-WH-42"}),
    ("inventory_sku", {"sku": "SHOE-WH-42"}),
    ("change_price", {"sku": "SHOE-WH-42", "price": 123}),
])
def test_cross_tenant_access_is_rejected_by_backend(dispatcher, audit, paid_order, name, arguments):
    observation = dispatcher.execute(name, arguments, token="test-b")
    assert_failure(observation, "RESOURCE_NOT_FOUND", True)
    assert all(request["method"] == "GET" for request in audit.get(observation.trace_id)["requests"])


def test_business_preflight_does_not_send_invalid_refund(dispatcher, audit, app, paid_order):
    exceeded = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": paid_order["total_amount"] + 1}, token="test-a")
    assert_failure(exceeded, "REFUND_AMOUNT_EXCEEDED", False)
    with app.extensions["session_factory"].begin() as session:
        session.get(Order, ("merchant-12345", "TEST-PAID")).status = "pending"
    invalid_state = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": 1}, token="test-a")
    assert_failure(invalid_state, "INVALID_BUSINESS_STATE", False)
    for observation in (exceeded, invalid_state):
        assert all(request["method"] == "GET" for request in audit.get(observation.trace_id)["requests"])


@pytest.mark.parametrize("response, code", [
    (BackendResponse(200, {"code": "PERMISSION_DENIED", "message": "secret"}), "PERMISSION_DENIED"),
    (BackendResponse(200, {"success": False, "error": {"code": "TENANT_ACCESS_DENIED"}}), "TENANT_ACCESS_DENIED"),
    (BackendResponse(200, {"code": 40401}), "RESOURCE_NOT_FOUND"),
    (BackendResponse(404, {"code": 40402}), "RESOURCE_NOT_FOUND"),
    (BackendResponse(404, {"code": 40403}), "RESOURCE_NOT_FOUND"),
    (BackendResponse(200, {"success": False}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {"code": "UNKNOWN_FAILURE"}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {"error": {"message": "error without code"}}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {"total": 1}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {**PRODUCTS, "total": "0"}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {**PRODUCTS, "products": [{}]}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {**PRODUCTS, "page": 2}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, {**PRODUCTS, "total": True}), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, [], True), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(200, None, False), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(204, PRODUCTS), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(302, PRODUCTS), "INVALID_BACKEND_RESPONSE"),
    (BackendResponse(401, {"code": "DATABASE_BUSY"}), "UNAUTHENTICATED"),
    (BackendResponse(403, {"code": "UPSTREAM_TIMEOUT"}), "PERMISSION_DENIED"),
    (BackendResponse(500, {"code": "INTERNAL_ERROR"}), "INTERNAL_ERROR"),
])
def test_response_validation_and_error_normalization(audit, response, code):
    backend = SequenceBackend([BackendResponse(200, IDENTITY), response])
    observation = Dispatcher(backend, audit).execute("list_products", {}, token="test-a")
    assert_failure(observation, code)
    assert len(backend.calls) == 2
    assert "secret" not in observation.model_dump_json()


@pytest.mark.parametrize("code", ["UNAUTHENTICATED", "PERMISSION_DENIED", "TENANT_ACCESS_DENIED", "INVALID_BUSINESS_STATE", "REFUND_AMOUNT_EXCEEDED", "APPROVAL_REQUIRED", "APPROVAL_REJECTED", "APPROVAL_EXPIRED", "INTERNAL_ERROR"])
def test_nonrecoverable_errors_never_retry(audit, code):
    backend = SequenceBackend([BackendResponse(200, IDENTITY), BackendResponse(503, {"code": code})])
    sleeps = []
    observation = Dispatcher(backend, audit, sleep=sleeps.append).execute("list_products", {}, token="test-a")
    assert_failure(observation, code, False)
    assert len(backend.calls) == 2
    assert sleeps == []


@pytest.mark.parametrize("failure", [
    BackendTransportError("UPSTREAM_TIMEOUT"), BackendTransportError("UPSTREAM_UNAVAILABLE"),
    BackendResponse(503, {"code": "DATABASE_BUSY"}), BackendResponse(502, None, False),
    BackendResponse(504, None, False), BackendResponse(429, None, False),
])
def test_transient_reads_retry_twice_then_succeed(audit, failure):
    backend = SequenceBackend([BackendResponse(200, IDENTITY), failure, failure, BackendResponse(200, PRODUCTS)])
    sleeps = []
    observation = Dispatcher(backend, audit, sleep=sleeps.append).execute("list_products", {}, token="test-a")
    assert observation.success
    assert sleeps == [0.1, 0.2]
    assert len(backend.calls) == 4
    assert {call["trace_id"] for call in backend.calls} == {observation.trace_id}
    assert len(audit.get(observation.trace_id)["requests"]) == 4


def test_retry_budget_is_shared_with_authentication(audit):
    timeout = BackendTransportError("UPSTREAM_TIMEOUT")
    backend = SequenceBackend([timeout, BackendResponse(200, IDENTITY), timeout, timeout])
    sleeps = []
    observation = Dispatcher(backend, audit, sleep=sleeps.append).execute("list_products", {}, token="test-a")
    assert_failure(observation, "UPSTREAM_TIMEOUT", True)
    assert len(backend.calls) == 4
    assert sleeps == [0.1, 0.2]


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_configured_retry_limit(audit, retries):
    backend = SequenceBackend([BackendTransportError("UPSTREAM_TIMEOUT")] * (retries + 1))
    observation = Dispatcher(backend, audit, retry_policy=RetryPolicy(retries, 0)).execute("list_products", {}, token="test-a")
    assert_failure(observation, "UPSTREAM_TIMEOUT", True)
    assert len(backend.calls) == retries + 1


@pytest.mark.parametrize("retries", [-1, 3, True, 1.5])
def test_retry_limits_cannot_be_unbounded(retries):
    with pytest.raises(ValueError):
        RetryPolicy(retries)


@pytest.mark.parametrize("failure", [
    BackendTransportError("UPSTREAM_TIMEOUT"), BackendTransportError("UPSTREAM_UNAVAILABLE"),
    BackendResponse(503, {"code": "DATABASE_BUSY"}), BackendResponse(500, {"code": "INTERNAL_ERROR"}),
    BackendResponse(201, {"unexpected": "body"}), BackendResponse(200, None, False),
    RuntimeError("sensitive detail"),
])
def test_unknown_write_outcomes_are_never_retried(audit, paid_order, failure):
    backend = SequenceBackend([BackendResponse(200, IDENTITY), BackendResponse(200, paid_order), failure])
    sleeps = []
    observation = Dispatcher(backend, audit, sleep=sleeps.append).execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert "Do not retry" in observation.error.retry_hint
    assert len(backend.calls) == 3
    assert sum(call["method"] == "POST" for call in backend.calls) == 1
    assert sleeps == []


def test_business_failure_in_write_response_is_not_retried(audit, paid_order):
    backend = SequenceBackend([
        BackendResponse(200, IDENTITY), BackendResponse(200, paid_order),
        BackendResponse(409, {"code": "REFUND_AMOUNT_EXCEEDED"}),
    ])
    observation = Dispatcher(backend, audit).execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "REFUND_AMOUNT_EXCEEDED", False)
    assert len(backend.calls) == 3


@pytest.mark.parametrize("tool, arguments, method", [
    ("refund_order", {"order_id": "TEST-PAID", "amount": 100}, "POST"),
    ("change_price", {"sku": "SHOE-WH-42", "price": 30000}, "PATCH"),
])
def test_conflicting_500_after_real_write_is_always_unknown(backend, audit, monkeypatch, paid_order, tool, arguments, method):
    actual_request = backend.request
    writes = []

    def replace_committed_response(request_method, endpoint, **kwargs):
        response = actual_request(request_method, endpoint, **kwargs)
        if request_method == method:
            writes.append(endpoint)
            return BackendResponse(500, {"code": "INVALID_ARGUMENT"})
        return response

    monkeypatch.setattr(backend, "request", replace_committed_response)
    observation = Dispatcher(backend, audit).execute(tool, arguments, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert len(writes) == 1


@pytest.mark.parametrize("body", [
    {"code": "RATE_LIMITED", "message": "wait"},
    {"code": "DATABASE_BUSY", "message": "wait"},
])
def test_structured_rate_limits_follow_bounded_read_retry(audit, body):
    limited = BackendResponse(429, body)
    backend = SequenceBackend([BackendResponse(200, IDENTITY), limited, limited, limited])
    sleeps = []
    observation = Dispatcher(backend, audit, sleep=sleeps.append).execute("list_products", {}, token="test-a")
    assert_failure(observation, "UPSTREAM_UNAVAILABLE", True)
    assert len(backend.calls) == 4
    assert sleeps == [0.1, 0.2]


@pytest.mark.parametrize("model, field, value", [
    (ProductOutput, "listed_at", "2026-02-30"),
    (RefundOutput, "created_at", "2026-01-01T12:00:00"),
    (RefundOutput, "created_at", "not-a-timestamp"),
    (RefundOutput, "id", ""),
])
def test_business_output_fields_reject_invalid_dates_and_identifiers(model, field, value, paid_order):
    product = {"sku": "SKU-1", "name": "name", "category": "category", "price": 100, "status": "on_sale", "listed_at": "2026-01-01"}
    refund = {"id": "refund-1", "order_id": paid_order["order_id"], "amount": 100, "reason": "", "created_at": paid_order["created_at"]}
    with pytest.raises(ValidationError):
        model.model_validate({**(product if model is ProductOutput else refund), field: value})


def test_date_contracts_publish_machine_checkable_formats():
    product_schema = ProductOutput.model_json_schema()
    refund_schema = RefundOutput.model_json_schema()
    assert product_schema["properties"]["listed_at"]["format"] == "date"
    assert refund_schema["properties"]["created_at"]["format"] == "date-time"
    checker = FormatChecker()
    Draft202012Validator(product_schema, format_checker=checker).validate({
        "sku": "SKU-1", "name": "name", "category": "category", "price": 100,
        "status": "on_sale", "listed_at": "2026-01-01",
    })


@pytest.mark.parametrize("tool, arguments, response_key, field, replacement", [
    ("list_orders", {"status": "paid"}, "orders", "status", "pending"),
    ("list_orders", {"keyword": "TEST-PAID"}, "orders", "order_id", "OTHER"),
    ("list_products", {"category": "运动鞋"}, "products", "category", "other"),
    ("list_products", {"keyword": "SHOE-WH-42"}, "products", "sku", "OTHER"),
])
def test_list_results_must_match_requested_filters(backend, audit, monkeypatch, paid_order, tool, arguments, response_key, field, replacement):
    actual_request = backend.request

    def corrupt_result(method, endpoint, **kwargs):
        response = actual_request(method, endpoint, **kwargs)
        if endpoint in ("/v1/orders", "/v1/products"):
            response.body[response_key][0][field] = replacement
        return response

    monkeypatch.setattr(backend, "request", corrupt_result)
    observation = Dispatcher(backend, audit).execute(tool, arguments, token="test-a")
    assert_failure(observation, "INVALID_BACKEND_RESPONSE", False)


def test_unexpected_failures_record_sanitized_diagnostics(audit, caplog):
    backend = SequenceBackend([
        BackendResponse(200, IDENTITY), RuntimeError("private-token-value"),
    ])
    with caplog.at_level(logging.ERROR):
        observation = Dispatcher(backend, audit).execute("list_products", {}, token="test-a")
    assert_failure(observation, "INTERNAL_ERROR", False)
    record = audit.get(observation.trace_id)
    assert record["requests"][-1]["outcome"] == "INTERNAL_ERROR"
    assert observation.trace_id in caplog.text
    assert "RuntimeError" in caplog.text
    assert "private-token-value" not in caplog.text


def test_response_amount_cannot_silently_change(audit, paid_order):
    wrong_order = {**paid_order, "refunded_amount": 99, "remaining_refundable_amount": paid_order["total_amount"] - 99}
    backend = SequenceBackend([
        BackendResponse(200, IDENTITY), BackendResponse(200, paid_order),
        BackendResponse(201, {"order": wrong_order, "refund": {"id": "refund-1", "order_id": "TEST-PAID", "amount": 99, "reason": "", "created_at": paid_order["created_at"]}}),
    ])
    observation = Dispatcher(backend, audit).execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert backend.calls[-1]["json_body"] == {"amount": 100, "reason": ""}


def test_refund_output_balance_invariants(paid_order):
    with pytest.raises(ValidationError):
        OrderOutput.model_validate({**paid_order, "refunded_amount": 1})
    with pytest.raises(ValidationError):
        OrderOutput.model_validate({**paid_order, "status": "refunded"})


def test_audit_is_persistent_and_excludes_payloads(dispatcher, audit, paid_order):
    observation = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": 100, "reason": "private reason"}, token="test-a")
    restarted = SQLiteAuditStore(audit.path)
    record = restarted.get(observation.trace_id)
    assert record["success"] == 1
    assert record["user_id"] == "user-a"
    assert "test-a" not in json.dumps(record)
    assert "private reason" not in json.dumps(record)


def test_audit_start_failure_blocks_backend(audit, monkeypatch):
    def fail_start(*args):
        raise OSError("secret path")

    monkeypatch.setattr(audit, "start", fail_start)
    backend = SequenceBackend([])
    observation = Dispatcher(backend, audit).execute("list_products", {}, token="test-a")
    assert_failure(observation, "AUDIT_UNAVAILABLE", False)
    assert backend.calls == []


def test_audit_finish_failure_preserves_pending_record(dispatcher, audit, paid_order, monkeypatch):
    def fail_finish(*args):
        raise OSError("disk full")

    monkeypatch.setattr(audit, "finish", fail_finish)
    observation = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert audit.get(observation.trace_id)["state"] == "started"
    assert audit.get(observation.trace_id)["user_id"] == "user-a"
    assert audit.get(observation.trace_id)["requests"][-1] == {
        "method": "POST", "endpoint": "/v1/orders/TEST-PAID/refund", "outcome": "dispatch_pending",
    }


def test_audit_checkpoint_failure_prevents_write(dispatcher, audit, paid_order, app, monkeypatch):
    def fail_checkpoint(*args):
        raise OSError("disk full")

    monkeypatch.setattr(audit, "checkpoint", fail_checkpoint)
    observation = dispatcher.execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "AUDIT_UNAVAILABLE", False)
    with app.extensions["session_factory"]() as session:
        assert session.get(Order, ("merchant-12345", "TEST-PAID")).refunded_amount == 0


def test_committed_refund_with_lost_response_is_not_replayed(backend, audit, app, paid_order, monkeypatch):
    actual_request = backend.request
    writes = []

    def lose_write_response(method, endpoint, **kwargs):
        response = actual_request(method, endpoint, **kwargs)
        if method == "POST":
            writes.append(endpoint)
            assert response.status_code == 201
            raise BackendTransportError("UPSTREAM_TIMEOUT")
        return response

    monkeypatch.setattr(backend, "request", lose_write_response)
    observation = Dispatcher(backend, audit).execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert writes == ["/v1/orders/TEST-PAID/refund"]
    with app.extensions["session_factory"]() as session:
        assert session.get(Order, ("merchant-12345", "TEST-PAID")).refunded_amount == 100
        assert session.scalar(select(func.count()).select_from(Refund).where(Refund.order_id == "TEST-PAID")) == 1


@pytest.mark.parametrize("mutation, expected_code", [("revoke", "UNAUTHENTICATED"), ("cancel", "INVALID_BUSINESS_STATE")])
def test_backend_rechecks_authorization_and_business_state_after_preflight(backend, audit, app, paid_order, monkeypatch, mutation, expected_code):
    actual_request = backend.request

    def mutate_after_preflight(method, endpoint, **kwargs):
        response = actual_request(method, endpoint, **kwargs)
        if endpoint == "/v1/orders/TEST-PAID":
            with app.extensions["session_factory"].begin() as session:
                if mutation == "revoke":
                    session.get(User, "user-a").active = False
                else:
                    session.get(Order, ("merchant-12345", "TEST-PAID")).status = "cancelled"
        return response

    monkeypatch.setattr(backend, "request", mutate_after_preflight)
    observation = Dispatcher(backend, audit).execute("refund_order", {"order_id": "TEST-PAID", "amount": 100}, token="test-a")
    assert_failure(observation, expected_code, False)
    with app.extensions["session_factory"]() as session:
        assert session.get(Order, ("merchant-12345", "TEST-PAID")).refunded_amount == 0


@pytest.mark.parametrize("corruption", ["price", "sku", "status", "timeout"])
def test_price_response_checks_never_replay_a_write(backend, audit, app, monkeypatch, corruption):
    actual_request = backend.request
    writes = []

    def corrupt_price_result(method, endpoint, **kwargs):
        response = actual_request(method, endpoint, **kwargs)
        if method != "PATCH":
            return response
        writes.append(endpoint)
        if corruption == "timeout":
            raise BackendTransportError("UPSTREAM_TIMEOUT")
        if corruption == "status":
            return BackendResponse(201, response.body)
        response.body["product"][corruption] = 124 if corruption == "price" else "WRONG-SKU"
        return response

    monkeypatch.setattr(backend, "request", corrupt_price_result)
    observation = Dispatcher(backend, audit).execute("change_price", {"sku": "SHOE-WH-42", "price": 30000}, token="test-a")
    assert_failure(observation, "WRITE_OUTCOME_UNKNOWN", False)
    assert len(writes) == 1


def test_concurrent_executions_keep_identity_and_trace_separate(dispatcher, audit):
    tokens = ["test-a", "test-b", "test-a", "test-b"]

    def execute(token):
        return dispatcher.execute("list_products", {}, token=token)

    with ThreadPoolExecutor(max_workers=4) as executor:
        observations = list(executor.map(execute, tokens))
    assert len({observation.trace_id for observation in observations}) == 4
    for token, observation in zip(tokens, observations):
        assert observation.success
        expected_sku = "SHOE-WH-42" if token == "test-a" else "B-SHOE-WH-42"
        assert observation.data["products"][0]["sku"] == expected_sku
        assert audit.get(observation.trace_id)["user_id"] == ("user-a" if token == "test-a" else "user-b")


def test_gateway_catalog_execution_and_trace(backend, audit, auth_a):
    gateway = create_gateway({"TESTING": True}, backend=backend, audit=audit).test_client()
    catalog = gateway.get("/tools").get_json()
    assert len(catalog["tools"]) == 22
    Draft202012Validator.check_schema(catalog["observation_schema"])
    response = gateway.post("/tools/execute", headers={**auth_a, "X-Request-ID": "forged", "X-Tenant-ID": "merchant-67890"},
                            json={"tool": "list_products", "arguments": {}})
    assert response.status_code == 200
    observation = Observation.model_validate(response.get_json())
    assert observation.trace_id == response.headers["X-Request-ID"] != "forged"
    assert observation.data["products"][0]["sku"] == "SHOE-WH-42"
    assert audit.get(observation.trace_id)["tenant_id"] == "merchant-12345"


@pytest.mark.parametrize("body, content_type", [("{", "application/json"), ("null", "application/json"), ("tool=list_products", "text/plain"), ("x" * 17000, "application/json")])
def test_gateway_malformed_bodies_are_observed_and_audited(backend, audit, body, content_type):
    gateway = create_gateway({"TESTING": True}, backend=backend, audit=audit).test_client()
    response = gateway.post("/tools/execute", data=body, content_type=content_type)
    assert response.status_code == 400
    observation = Observation.model_validate(response.get_json())
    assert_failure(observation, "INVALID_ARGUMENT", True)
    assert audit.get(observation.trace_id)


def test_gateway_does_not_accept_authentication_in_arguments(backend, audit):
    gateway = create_gateway({"TESTING": True}, backend=backend, audit=audit).test_client()
    response = gateway.post("/tools/execute", json={"tool": "list_products", "arguments": {}})
    assert response.status_code == 401
    response = gateway.post("/tools/execute", json={"tool": "list_products", "arguments": {"token": "test-a"}})
    assert response.status_code == 400


def test_http_client_forwards_credentials_trace_and_never_follows_redirects():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://other-host/steal"})

    client = HTTPBackendClient("http://backend", transport=httpx.MockTransport(handler))
    try:
        response = client.request("GET", "/v1/products", token="private-token", trace_id="trace-1", params={"page": 2})
        assert response.status_code == 302
        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer private-token"
        assert requests[0].headers["X-Request-ID"] == "trace-1"
        assert str(requests[0].url) == "http://backend/v1/products?page=2"
    finally:
        client.close()


@pytest.mark.parametrize("exception, code", [(httpx.ReadTimeout("secret"), "UPSTREAM_TIMEOUT"), (httpx.ConnectError("secret"), "UPSTREAM_UNAVAILABLE")])
def test_http_transport_failures_are_normalized(exception, code):
    def handler(request):
        raise exception

    client = HTTPBackendClient("http://backend", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendTransportError) as failure:
            client.request("GET", "/v1/products", token="secret-token", trace_id="trace-1")
        assert failure.value.code == code
        assert "secret" not in str(failure.value)
    finally:
        client.close()


@pytest.mark.parametrize("url", ["file:///tmp/data", "http://user:password@backend", "http://backend/path", "http://backend?token=secret", "http://backend#fragment"])
def test_backend_origin_is_fixed_server_configuration(url):
    with pytest.raises(ValueError):
        HTTPBackendClient(url)
