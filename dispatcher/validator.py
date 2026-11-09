from pydantic import ValidationError

from .observation import ERRORS, ToolFailure
from .schemas import ApprovalOutput


TRANSIENT_CODES = {"UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE", "DATABASE_BUSY"}
LEGACY_CODES = {40401: "RESOURCE_NOT_FOUND", 40402: "RESOURCE_NOT_FOUND", 40403: "RESOURCE_NOT_FOUND"}
WRITE_REJECTIONS = {
    "INVALID_ARGUMENT": 400, "PRICE_OUT_OF_RANGE": 400,
    "RESOURCE_NOT_FOUND": 404, "INVALID_BUSINESS_STATE": 409,
    "REFUND_AMOUNT_EXCEEDED": 409,
    "IDEMPOTENCY_CONFLICT": 409, "APPROVAL_INVALID": 409, "APPROVAL_FAILED": 409,
}
AUTHORIZATION_CODES = {
    "PERMISSION_DENIED", "TENANT_ACCESS_DENIED", "APPROVAL_REQUIRED",
    "APPROVAL_REJECTED", "APPROVAL_EXPIRED",
}


def validate_response(response, output_model, success_status=200, *, is_write=False):
    if is_write and response.status_code not in (success_status, 400, 401, 403, 404, 409):
        raise ToolFailure("WRITE_OUTCOME_UNKNOWN")
    body = response.body
    code = None
    if response.is_json and isinstance(body, dict):
        code = body.get("code")
        if isinstance(body.get("error"), dict):
            code = body["error"].get("code", code)
        if type(code) is int:
            code = LEGACY_CODES.get(code)
    if response.status_code == 401:
        raise ToolFailure("UNAUTHENTICATED")
    if response.status_code == 403:
        failure = ToolFailure(code if isinstance(code, str) and code in AUTHORIZATION_CODES else "PERMISSION_DENIED")
        if code == "APPROVAL_REQUIRED" and isinstance(body, dict) and "approval" in body:
            try:
                failure.error.approval = ApprovalOutput.model_validate(body["approval"])
                if failure.error.approval.status != "PENDING":
                    raise ValueError("Expected pending approval")
            except (ValidationError, ValueError):
                raise ToolFailure("INVALID_BACKEND_RESPONSE") from None
        raise failure
    if response.is_json and isinstance(body, dict):
        if isinstance(code, str) and code in ERRORS:
            if is_write and WRITE_REJECTIONS.get(code) != response.status_code:
                raise ToolFailure("WRITE_OUTCOME_UNKNOWN")
            if response.status_code != 429 or not ERRORS[code][2]:
                raise ToolFailure(code)
        if response.status_code == 429:
            raise ToolFailure("UPSTREAM_UNAVAILABLE")
        if "code" in body or body.get("success") is False or body.get("error") is not None:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if response.status_code in (429, 502, 503, 504):
        raise ToolFailure("UPSTREAM_TIMEOUT" if response.status_code == 504 else "UPSTREAM_UNAVAILABLE")
    if response.status_code == 404:
        raise ToolFailure("RESOURCE_NOT_FOUND")
    if response.status_code != success_status or not response.is_json:
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    try:
        return output_model.model_validate(body)
    except ValidationError:
        raise ToolFailure("INVALID_BACKEND_RESPONSE") from None


def validate_postconditions(tool, arguments, result, before=None):
    if hasattr(arguments, "approval_id"):
        if result.id != arguments.approval_id or (tool.name == "execute_approval" and result.status != "COMPLETED"):
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if hasattr(arguments, "order_id"):
        order = result.order if tool.name == "refund_order" else result
        if order.order_id != arguments.order_id:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if hasattr(arguments, "sku"):
        product = result.product if tool.name == "change_price" else result
        if product.sku != arguments.sku:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if hasattr(arguments, "period") and result.period != arguments.period:
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if hasattr(arguments, "page"):
        entries = getattr(result, "orders", getattr(result, "products", getattr(result, "customers", [])))
        if result.page != arguments.page or result.page_size != arguments.page_size or len(entries) > result.page_size or len(entries) > result.total:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "list_orders":
        for order in result.orders:
            if arguments.status and order.status != arguments.status:
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
            if not arguments.start_date <= order.created_at[:10] <= arguments.end_date:
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
            if arguments.keyword and not any(arguments.keyword.lower() in value.lower() for value in [order.order_id, *(item.name for item in order.items)]):
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
        field = "total_amount" if arguments.sort == "amount_desc" else "created_at"
        validate_ordering(result.orders, field, arguments.sort != "created_asc")
    if tool.name == "list_products":
        for product in result.products:
            if arguments.status != "all" and product.status != arguments.status:
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
            if arguments.category and product.category != arguments.category:
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
            if arguments.keyword and not any(arguments.keyword.lower() in value.lower() for value in (product.sku, product.name)):
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "list_customers":
        field = {"last_order_desc": "last_order_date", "total_spent_desc": "total_spent", "order_count_desc": "total_orders"}[arguments.sort]
        validate_ordering(result.customers, field, True)
    if tool.name == "order_stats":
        if result.period.start != arguments.start_date or result.period.end != arguments.end_date or sum(result.by_status.model_dump().values()) != result.total_orders:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "analytics_trend" and (result.metric != arguments.metric or len(result.points) != arguments.days):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "analytics_aov" and (result.days != arguments.days or len(result.points) != arguments.days):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "product_ranking":
        if len(result.ranking) > arguments.limit or [item.rank for item in result.ranking] != list(range(1, len(result.ranking) + 1)):
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
        validate_ordering(result.ranking, "sales_count" if arguments.sort.startswith("sales") else "revenue", arguments.sort.endswith("desc"))
    if tool.name == "change_price" and result.product.price != arguments.price:
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "change_user_role" and (
        result.user_id != arguments.user_id or result.role != arguments.role
    ):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "refund_order":
        if result.refund.order_id != arguments.order_id or result.refund.amount != arguments.amount or result.refund.reason != arguments.reason:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
        if before is not None and result.order.refunded_amount < before.refunded_amount + arguments.amount:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
        for field in (() if before is None else ("total_amount", "items", "customer_name", "created_at", "logistics")):
            if getattr(result.order, field) != getattr(before, field):
                raise ToolFailure("INVALID_BACKEND_RESPONSE")


def validate_ordering(entries, field, reverse):
    values = [getattr(entry, field) for entry in entries]
    if values != sorted(values, reverse=reverse):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
