import os
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge, UnsupportedMediaType

from .audit import SQLiteAuditStore
from .backend_client import HTTPBackendClient
from .dispatcher import Dispatcher, RetryPolicy
from .observation import Observation


def create_app(config=None, *, backend=None, audit=None):
    app = Flask(__name__)
    app.config.from_mapping(
        BACKEND_URL=os.environ.get("DISPATCHER_BACKEND_URL", "http://127.0.0.1:8080"),
        AUDIT_DB_PATH=os.environ.get("DISPATCHER_AUDIT_DB", str(Path(__file__).resolve().parent / "audit.sqlite3")),
        BACKEND_TIMEOUT=5.0,
        MAX_RETRIES=2,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)
    backend = backend or HTTPBackendClient(app.config["BACKEND_URL"], app.config["BACKEND_TIMEOUT"])
    audit = audit or SQLiteAuditStore(app.config["AUDIT_DB_PATH"])
    dispatcher = Dispatcher(backend, audit, retry_policy=RetryPolicy(max_retries=app.config["MAX_RETRIES"]))
    app.extensions["dispatcher"] = dispatcher

    def observation_response(observation):
        status = 200
        if observation.error:
            code = observation.error.code
            status = {
                "INVALID_ARGUMENT": 400, "UNKNOWN_TOOL": 400, "UNAUTHENTICATED": 401,
                "PERMISSION_DENIED": 403, "TENANT_ACCESS_DENIED": 403,
                "RESOURCE_NOT_FOUND": 404, "INVALID_BUSINESS_STATE": 409,
                "REFUND_AMOUNT_EXCEEDED": 409, "PRICE_OUT_OF_RANGE": 400,
                "APPROVAL_REQUIRED": 403, "APPROVAL_REJECTED": 403, "APPROVAL_EXPIRED": 403,
                "INVALID_BACKEND_RESPONSE": 502, "WRITE_OUTCOME_UNKNOWN": 502,
                "INTERNAL_ERROR": 500,
            }.get(code, 503)
        response = jsonify(observation.model_dump(mode="json"))
        response.headers["X-Request-ID"] = observation.trace_id
        return response, status

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/tools")
    def list_tools():
        return jsonify({"tools": dispatcher.registry.describe(), "observation_schema": Observation.model_json_schema()})

    @app.post("/tools/execute")
    def execute_tool():
        scheme, separator, token = request.headers.get("Authorization", "").partition(" ")
        credential = token if separator and scheme.lower() == "bearer" else None
        try:
            payload = request.get_json(silent=False)
        except (BadRequest, UnsupportedMediaType, RecursionError):
            payload = None
        return observation_response(dispatcher.dispatch(payload, token=credential))

    @app.errorhandler(RequestEntityTooLarge)
    def oversized_request(error):
        return observation_response(dispatcher.dispatch(None))

    return app
