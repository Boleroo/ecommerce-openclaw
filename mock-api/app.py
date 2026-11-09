"""Persistent Phase 1 business backend for the existing OpenClaw mock API."""

import json
import os
from pathlib import Path

from flask import Flask, g, jsonify, request
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from business import BusinessError, operations
from approvals import approvals
from database import init_database
from models import Tenant, User
from policy import permissions_for_role
from read_api import reads
from seed import seed_database


def create_app(config=None):
    app = Flask(__name__)
    database_path = Path(__file__).resolve().parent / "business.sqlite3"
    app.config.from_mapping(
        DATABASE_URL=os.environ.get("DATABASE_URL", f"sqlite:///{database_path}"),
        API_TOKENS=os.environ.get("API_TOKENS", "{}"),
        HUMAN_APPROVAL_TOKENS=os.environ.get("HUMAN_APPROVAL_TOKENS", "{}"),
        APPROVAL_TTL_SECONDS=3600,
        LEGACY_READS=os.environ.get("LEGACY_READS"),
        SEED_DAYS=90,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)
    if isinstance(app.config["API_TOKENS"], str):
        app.config["API_TOKENS"] = json.loads(app.config["API_TOKENS"])
    tokens = app.config["API_TOKENS"]
    if not isinstance(tokens, dict) or any(
        not isinstance(token, str) or not token.strip()
        or not isinstance(user_id, str) or not user_id
        for token, user_id in tokens.items()
    ):
        raise ValueError("API_TOKENS must map nonempty bearer tokens to user IDs")
    human_tokens = app.config["HUMAN_APPROVAL_TOKENS"]
    if isinstance(human_tokens, str):
        human_tokens = json.loads(human_tokens)
    app.config["HUMAN_APPROVAL_TOKENS"] = human_tokens
    if not isinstance(human_tokens, dict) or any(
        not isinstance(token, str) or not token.strip() or not isinstance(user_id, str) or not user_id
        for token, user_id in human_tokens.items()
    ) or set(human_tokens) & set(tokens):
        raise ValueError("Human approval credentials must be valid and distinct from API credentials")
    if type(app.config["APPROVAL_TTL_SECONDS"]) is not int or not 1 <= app.config["APPROVAL_TTL_SECONDS"] <= 86400:
        raise ValueError("APPROVAL_TTL_SECONDS must be between 1 and 86400")
    if app.config["LEGACY_READS"] is None:
        app.config["LEGACY_READS"] = not bool(tokens or human_tokens)
    elif isinstance(app.config["LEGACY_READS"], str):
        if app.config["LEGACY_READS"].lower() not in ("true", "false"):
            raise ValueError("LEGACY_READS must be true or false")
        app.config["LEGACY_READS"] = app.config["LEGACY_READS"].lower() == "true"
    init_database(app)
    seed_database(app)

    @app.before_request
    def establish_context():
        if not request.path.startswith("/v1/"):
            return
        authorization = request.headers.get("Authorization")
        g.human_approval = False
        if authorization is None and request.method in ("GET", "HEAD", "OPTIONS") and app.config["LEGACY_READS"]:
            g.tenant_id = "merchant-12345"
            g.user_id = None
            g.role = "viewer"
            g.permissions = permissions_for_role(g.role)
        else:
            scheme, separator, token = (authorization or "").partition(" ")
            user_id = (tokens.get(token) or human_tokens.get(token)) if separator and scheme.lower() == "bearer" else None
            if not user_id:
                raise BusinessError("UNAUTHENTICATED", "A valid bearer token is required", 401)
            with app.extensions["session_factory"]() as session:
                user = session.get(User, user_id)
                if user is None or not user.active or session.get(Tenant, user.tenant_id) is None:
                    raise BusinessError("UNAUTHENTICATED", "A valid active user is required", 401)
                try:
                    permissions = permissions_for_role(user.role)
                except ValueError:
                    raise BusinessError("UNAUTHENTICATED", "A valid active user is required", 401) from None
                g.tenant_id = user.tenant_id
                g.user_id = user.id
                g.role = user.role
                g.permissions = permissions
                g.human_approval = token in human_tokens
        if {"tenant_id", "user_id", "role", "permissions"} & request.args.keys():
            raise BusinessError("INVALID_ARGUMENT", "Identity must come from server-side authentication")
        g.db = app.extensions["session_factory"]()

    @app.teardown_request
    def close_session(error=None):
        session = g.pop("db", None)
        if session is not None:
            session.close()

    @app.get("/v1/auth/context")
    def authenticated_context():
        if g.user_id is None:
            raise BusinessError("UNAUTHENTICATED", "A valid bearer token is required", 401)
        return jsonify({
            "user_id": g.user_id,
            "tenant_id": g.tenant_id,
            "role": g.role,
            "permissions": sorted(g.permissions),
        })

    @app.errorhandler(BusinessError)
    def business_error(error):
        return jsonify({"code": error.code, "message": error.message}), error.status

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        session = g.get("db")
        if session is not None:
            session.rollback()
        if isinstance(error, OperationalError) and "locked" in str(error.orig).lower():
            return jsonify({"code": "DATABASE_BUSY", "message": "Database is busy; try again later"}), 503
        app.logger.exception("Database operation failed")
        return jsonify({"code": "INTERNAL_ERROR", "message": "Database operation failed"}), 500

    app.register_blueprint(reads)
    app.register_blueprint(operations)
    app.register_blueprint(approvals)
    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
