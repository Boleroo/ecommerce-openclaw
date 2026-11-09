import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from business import BusinessError, apply_operation, body_fields, validate_operation
from models import ApprovalRequest, AuditLog, IdempotencyRecord, User, utc_now
from policy import permissions_for_role


approvals = Blueprint("approvals", __name__)
OPERATION_PERMISSIONS = {"refund_order": "orders:refund", "change_price": "products:price"}


def digest(document):
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def operation_document(operation_id, tenant_id, initiator_id, operation, resource_id, parameters):
    return dict(
        operation_id=operation_id, tenant_id=tenant_id, initiator_id=initiator_id,
        operation=operation, resource_id=resource_id, parameters=parameters,
    )


def approval_digest(approval):
    document = operation_document(
        approval.operation_id, approval.tenant_id, approval.initiator_id,
        approval.operation, approval.resource_id, approval.parameters,
    )
    document.update(approval_level=approval.approval_level, expires_at=approval.expires_at)
    return digest(document)


def active_permissions(user_id):
    user = g.db.get(User, user_id, populate_existing=True)
    if user is None or not user.active or user.tenant_id != g.tenant_id:
        raise BusinessError("PERMISSION_DENIED", "An active same-tenant user is required", 403)
    try:
        return permissions_for_role(user.role)
    except ValueError:
        raise BusinessError("PERMISSION_DENIED", "Invalid user role", 403) from None


def require_operation_permission(user_id, operation):
    if operation not in OPERATION_PERMISSIONS:
        raise BusinessError("APPROVAL_INVALID", "Invalid approval operation", 409)
    if OPERATION_PERMISSIONS[operation] not in active_permissions(user_id):
        raise BusinessError("PERMISSION_DENIED", "Operation permission is required", 403)


def audit_event(event, resource_id, **details):
    trace_id = request.headers.get("X-Request-ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trace_id):
        trace_id = g.get("audit_trace_id") or str(uuid4())
        g.audit_trace_id = trace_id
    g.db.add(AuditLog(
        tenant_id=g.tenant_id, user_id=g.user_id, operation=event,
        resource_id=resource_id, details={"trace_id": trace_id, **details},
    ))


def submission_key(body_key):
    header_key = request.headers.get("Idempotency-Key")
    if body_key is not None and header_key is not None and body_key != header_key:
        raise BusinessError("INVALID_ARGUMENT", "Conflicting idempotency keys")
    key = body_key if body_key is not None else header_key
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key)):
        raise BusinessError("INVALID_ARGUMENT", "Idempotency key must be a bounded identifier")
    return key


def submit_operation(operation, resource_id, parameters, body_key):
    key = submission_key(body_key)
    with g.db.begin():
        g.db.execute(text("BEGIN IMMEDIATE"))
        require_operation_permission(g.user_id, operation)
        record = None
        if key is not None:
            record = g.db.scalar(select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == g.tenant_id, IdempotencyRecord.key == key,
            ))
        operation_id = record.operation_id if record is not None else str(uuid4())
        request_digest = digest(operation_document(
            operation_id, g.tenant_id, g.user_id, operation, resource_id, parameters,
        ))
        if record is not None:
            if record.initiator_id != g.user_id or record.request_digest != request_digest:
                audit_event("idempotency.conflict", resource_id, operation_id=operation_id)
                return jsonify({"code": "IDEMPOTENCY_CONFLICT", "message": "Key is bound to a different request"}), 409
            if record.status != "COMPLETED" or record.response_status is None:
                return jsonify({"code": "WRITE_OUTCOME_UNKNOWN", "message": "Inspect persisted operation before retrying"}), 409
            audit_event("idempotency.replayed", resource_id, operation_id=operation_id)
            return jsonify(record.response), record.response_status
        resource, decision = validate_operation(operation, resource_id, parameters)
        if decision.approval_required:
            bound_parameters = dict(parameters)
            if operation == "change_price":
                bound_parameters["expected_price"] = resource.price
            approval = ApprovalRequest(
                id=str(uuid4()), operation_id=operation_id, tenant_id=g.tenant_id,
                initiator_id=g.user_id, operation=operation, resource_id=resource_id,
                parameters=bound_parameters, approval_level=decision.approval_level,
                status="PENDING", created_at=utc_now(),
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=current_app.config["APPROVAL_TTL_SECONDS"])).isoformat(),
            )
            approval.request_digest = approval_digest(approval)
            g.db.add(approval)
            audit_event("approval.created", approval.id, operation_id=operation_id,
                        status="PENDING", request_digest=approval.request_digest)
            response, status = {
                "code": "APPROVAL_REQUIRED", "message": decision.approval_message,
                "approval": approval.to_dict(),
            }, 403
        else:
            response, status = apply_operation(operation, resource, parameters, g.user_id)
            audit_event("operation.completed", resource_id, operation_id=operation_id, request_digest=request_digest)
        if key is not None:
            g.db.add(IdempotencyRecord(
                tenant_id=g.tenant_id, key=key, initiator_id=g.user_id,
                operation_id=operation_id, request_digest=request_digest,
                status="COMPLETED", response=response, response_status=status,
            ))
    return jsonify(response), status


def load_approval(approval_id):
    if g.user_id is None:
        raise BusinessError("UNAUTHENTICATED", "Authentication is required", 401)
    approval = g.db.get(ApprovalRequest, approval_id)
    if approval is None or approval.tenant_id != g.tenant_id:
        raise BusinessError("RESOURCE_NOT_FOUND", "Approval not found", 404)
    permissions = active_permissions(g.user_id)
    if g.user_id != approval.initiator_id and "approvals:manager" not in permissions:
        raise BusinessError("PERMISSION_DENIED", "Approval access denied", 403)
    return approval


def verify_digest(approval):
    try:
        valid = approval.request_digest and hmac.compare_digest(approval.request_digest, approval_digest(approval))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise BusinessError("APPROVAL_INVALID", "Approval digest does not match its operation", 409)


def expire_if_due(approval):
    if approval.status in ("PENDING", "APPROVED") and (
        approval.expires_at is None or datetime.fromisoformat(approval.expires_at) <= datetime.now(timezone.utc)
    ):
        approval.status = "EXPIRED"
        audit_event("approval.expired", approval.id, status="EXPIRED", operation_id=approval.operation_id)


def require_reviewer(approval, user_id):
    if user_id == approval.initiator_id:
        raise BusinessError("PERMISSION_DENIED", "Self-approval is forbidden", 403)
    permission = {"manager": "approvals:manager", "admin": "approvals:admin"}.get(approval.approval_level)
    if permission is None or permission not in active_permissions(user_id):
        raise BusinessError("PERMISSION_DENIED", "Required approval authority is missing", 403)


def revalidate_operation(approval):
    require_operation_permission(approval.initiator_id, approval.operation)
    resource, decision = validate_operation(approval.operation, approval.resource_id, approval.parameters)
    if decision.approval_level != approval.approval_level:
        raise BusinessError("APPROVAL_INVALID", "Approval policy changed; submit a new request", 409)
    return resource


def terminal_error(approval):
    codes = {"REJECTED": "APPROVAL_REJECTED", "EXPIRED": "APPROVAL_EXPIRED", "FAILED": "APPROVAL_FAILED"}
    if approval.status in codes:
        raise BusinessError(codes[approval.status], "Approval cannot execute in its current state", 403 if approval.status != "FAILED" else 409)


def process_approval(approval_id, action):
    try:
        if action != "get":
            try:
                body_fields(())
            except BusinessError as error:
                with g.db.begin():
                    audit_event("approval.denied", approval_id[:128], action=action, code=error.code)
                return jsonify({"code": error.code, "message": error.message}), error.status
        return process_transaction(approval_id, action)
    except SQLAlchemyError:
        g.db.rollback()
        with g.db.begin():
            audit_event("approval.infrastructure_failed", approval_id[:128], action=action, code="INTERNAL_ERROR")
        raise


def process_transaction(approval_id, action):
    failure = None
    with g.db.begin():
        g.db.execute(text("BEGIN IMMEDIATE"))
        approval = None
        validating_operation = False
        try:
            approval = load_approval(approval_id)
            if action in ("approve", "reject"):
                if not g.human_approval:
                    raise BusinessError("PERMISSION_DENIED", "A separate human approval credential is required", 403)
                require_reviewer(approval, g.user_id)
            elif action == "execute" and g.user_id != approval.initiator_id:
                raise BusinessError("PERMISSION_DENIED", "Only the initiator may execute the approved operation", 403)
            verify_digest(approval)
            expire_if_due(approval)
            if action != "get":
                terminal_error(approval)
            if action == "approve":
                if approval.status == "PENDING":
                    validating_operation = True
                    revalidate_operation(approval)
                    validating_operation = False
                    approval.status = "APPROVED"
                    approval.approver_id = g.user_id
                    approval.decided_at = utc_now()
                    audit_event("approval.approved", approval.id, status="APPROVED", operation_id=approval.operation_id)
                elif approval.status != "APPROVED" or approval.approver_id != g.user_id:
                    raise BusinessError("INVALID_BUSINESS_STATE", "Approval decision is already final", 409)
            elif action == "reject":
                if approval.status != "PENDING":
                    raise BusinessError("INVALID_BUSINESS_STATE", "Approval decision is already final", 409)
                approval.status = "REJECTED"
                approval.approver_id = g.user_id
                approval.decided_at = utc_now()
                audit_event("approval.rejected", approval.id, status="REJECTED", operation_id=approval.operation_id)
            elif action == "execute":
                if approval.status == "COMPLETED":
                    audit_event("approval.replayed", approval.id, status="COMPLETED", operation_id=approval.operation_id)
                else:
                    if approval.status != "APPROVED":
                        raise BusinessError("APPROVAL_REQUIRED", "A completed human approval is required", 403)
                    validating_operation = True
                    require_reviewer(approval, approval.approver_id)
                    resource = revalidate_operation(approval)
                    validating_operation = False
                    approval.status = "EXECUTING"
                    audit_event("approval.executing", approval.id, status="EXECUTING", operation_id=approval.operation_id)
                    g.db.flush()
                    result, unused_status = apply_operation(
                        approval.operation, resource, approval.parameters, approval.initiator_id,
                    )
                    approval.result = result
                    approval.status = "COMPLETED"
                    audit_event("approval.completed", approval.id, status="COMPLETED", operation_id=approval.operation_id)
        except BusinessError as error:
            failure = error
            if approval is not None and (error.code == "APPROVAL_INVALID" or validating_operation) and approval.status in ("PENDING", "APPROVED"):
                approval.status = "FAILED"
                approval.failure_code = error.code
                audit_event("approval.failed", approval.id, status="FAILED", code=error.code, operation_id=approval.operation_id)
            audit_event("approval.denied", approval_id[:128], action=action, code=error.code)
        if failure is None:
            response = approval.to_dict()
    if failure is not None:
        return jsonify({"code": failure.code, "message": failure.message}), failure.status
    return jsonify(response)


@approvals.get("/v1/approvals/<approval_id>")
def get_approval(approval_id):
    return process_approval(approval_id, "get")


@approvals.post("/v1/approvals/<approval_id>/approve")
def approve(approval_id):
    return process_approval(approval_id, "approve")


@approvals.post("/v1/approvals/<approval_id>/reject")
def reject(approval_id):
    return process_approval(approval_id, "reject")


@approvals.post("/v1/approvals/<approval_id>/execute")
def execute(approval_id):
    return process_approval(approval_id, "execute")
