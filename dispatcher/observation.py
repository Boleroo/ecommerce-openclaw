from typing import Any, Literal

from pydantic import model_validator

from .schemas import Schema


ErrorCode = Literal[
    "INVALID_ARGUMENT", "UNKNOWN_TOOL", "UNAUTHENTICATED", "PERMISSION_DENIED",
    "TENANT_ACCESS_DENIED", "RESOURCE_NOT_FOUND", "INVALID_BUSINESS_STATE",
    "REFUND_AMOUNT_EXCEEDED", "PRICE_OUT_OF_RANGE", "APPROVAL_REQUIRED",
    "APPROVAL_REJECTED", "APPROVAL_EXPIRED", "UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE",
    "DATABASE_BUSY", "INVALID_BACKEND_RESPONSE", "WRITE_OUTCOME_UNKNOWN",
    "AUDIT_UNAVAILABLE", "INTERNAL_ERROR",
]


class Error(Schema):
    code: ErrorCode
    category: Literal["VALIDATION", "AUTHORIZATION", "BUSINESS", "INFRASTRUCTURE", "RESPONSE"]
    message: str
    recoverable: bool
    retry_hint: str | None


class Observation(Schema):
    success: bool
    tool: str
    error: Error | None
    data: Any
    trace_id: str

    @model_validator(mode="after")
    def consistent_outcome(self):
        if self.success != (self.error is None) or (not self.success and self.data is not None):
            raise ValueError("Observation success, error and data are inconsistent")
        return self


ERRORS = {
    "INVALID_ARGUMENT": ("VALIDATION", "Arguments do not match the tool schema", True, "Check the tool input_schema and submit corrected arguments; amounts are integer cents."),
    "UNKNOWN_TOOL": ("VALIDATION", "Tool is not registered", True, "Choose a name from the tool registry."),
    "UNAUTHENTICATED": ("AUTHORIZATION", "A valid active backend user is required", False, None),
    "PERMISSION_DENIED": ("AUTHORIZATION", "Required permissions are missing", False, None),
    "TENANT_ACCESS_DENIED": ("AUTHORIZATION", "Tenant access denied", False, None),
    "RESOURCE_NOT_FOUND": ("BUSINESS", "Resource is unavailable in the authenticated tenant", True, "Verify the identifier using an authorized list tool; do not change tenant identity."),
    "INVALID_BUSINESS_STATE": ("BUSINESS", "The resource state does not permit this operation", False, None),
    "REFUND_AMOUNT_EXCEEDED": ("BUSINESS", "Amount exceeds the remaining refundable balance", False, "Review the order balance with the user; do not silently reduce the amount."),
    "PRICE_OUT_OF_RANGE": ("BUSINESS", "Price is outside the permitted business limits", True, "Use an explicitly requested price between 1 and 100000000 cents."),
    "APPROVAL_REQUIRED": ("AUTHORIZATION", "Backend requires approval", False, None),
    "APPROVAL_REJECTED": ("AUTHORIZATION", "Backend rejected approval", False, None),
    "APPROVAL_EXPIRED": ("AUTHORIZATION", "Backend approval has expired", False, None),
    "UPSTREAM_TIMEOUT": ("INFRASTRUCTURE", "Backend request timed out", True, "A read may be retried later; the automatic retry budget is exhausted."),
    "UPSTREAM_UNAVAILABLE": ("INFRASTRUCTURE", "Backend is temporarily unavailable", True, "A read may be retried later; the automatic retry budget is exhausted."),
    "DATABASE_BUSY": ("INFRASTRUCTURE", "Backend database is busy", True, "A read may be retried later; the automatic retry budget is exhausted."),
    "INVALID_BACKEND_RESPONSE": ("RESPONSE", "Backend response failed contract validation", False, "Investigate the backend response contract before retrying."),
    "WRITE_OUTCOME_UNKNOWN": ("INFRASTRUCTURE", "The write may have executed; its outcome could not be verified", False, "Do not retry this write. Inspect persisted business state using the trace ID and resolve the outcome manually."),
    "AUDIT_UNAVAILABLE": ("INFRASTRUCTURE", "The execution audit could not be persisted", False, "Restore the audit store and inspect the trace before considering another execution."),
    "INTERNAL_ERROR": ("INFRASTRUCTURE", "Tool execution failed internally", False, "Investigate the trace before considering another execution."),
}


def make_error(code):
    category, message, recoverable, retry_hint = ERRORS[code]
    return Error(code=code, category=category, message=message, recoverable=recoverable, retry_hint=retry_hint)


class ToolFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.error = make_error(code)
