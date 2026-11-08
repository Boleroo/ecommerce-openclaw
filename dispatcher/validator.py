from pydantic import ValidationError

from .observation import ERRORS, ToolFailure


TRANSIENT_CODES = {"UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE", "DATABASE_BUSY"}
LEGACY_CODES = {40401: "RESOURCE_NOT_FOUND", 40402: "RESOURCE_NOT_FOUND", 40403: "RESOURCE_NOT_FOUND"}


def validate_response(response, output_model, success_status=200):
    if response.status_code == 401:
        raise ToolFailure("UNAUTHENTICATED")
    if response.status_code == 403:
        raise ToolFailure("PERMISSION_DENIED")
    body = response.body
    if response.is_json and isinstance(body, dict):
        code = body.get("code")
        if isinstance(body.get("error"), dict):
            code = body["error"].get("code", code)
        if type(code) is int:
            code = LEGACY_CODES.get(code)
        if isinstance(code, str) and code in ERRORS:
            raise ToolFailure(code)
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
    if tool.name == "order_stats":
        if result.period.start != arguments.start_date or result.period.end != arguments.end_date or sum(result.by_status.model_dump().values()) != result.total_orders:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "analytics_trend" and (result.metric != arguments.metric or len(result.points) != arguments.days):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "analytics_aov" and (result.days != arguments.days or len(result.points) != arguments.days):
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "product_ranking" and len(result.ranking) > arguments.limit:
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "change_price" and result.product.price != arguments.price:
        raise ToolFailure("INVALID_BACKEND_RESPONSE")
    if tool.name == "refund_order":
        if result.refund.order_id != arguments.order_id or result.refund.amount != arguments.amount or result.refund.reason != arguments.reason:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
        if result.order.refunded_amount < before.refunded_amount + arguments.amount:
            raise ToolFailure("INVALID_BACKEND_RESPONSE")
        for field in ("total_amount", "items", "customer_name", "created_at", "logistics"):
            if getattr(result.order, field) != getattr(before, field):
                raise ToolFailure("INVALID_BACKEND_RESPONSE")
