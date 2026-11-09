from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel

from . import schemas


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    method: Literal["GET", "POST", "PATCH"]
    endpoint: str
    required_permissions: frozenset[str] = frozenset({"business:read"})
    risk_level: Literal["LOW", "HIGH", "DYNAMIC"] = "LOW"
    path_fields: tuple[str, ...] = ()
    success_status: int = 200

    def describe(self):
        return {
            "name": self.name, "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "required_permissions": sorted(self.required_permissions),
            "risk_level": self.risk_level, "method": self.method,
            "endpoint": self.endpoint, "success_status": self.success_status,
        }


class ToolRegistry:
    def __init__(self, tools):
        entries = {}
        for tool in tools:
            if tool.name in entries:
                raise ValueError(f"Duplicate tool: {tool.name}")
            entries[tool.name] = tool
        self.tools = MappingProxyType(entries)

    def get(self, name):
        return self.tools.get(name)

    def describe(self):
        return [tool.describe() for tool in self.tools.values()]


REGISTRY = ToolRegistry([
    Tool("list_orders", "List stored orders with date, status, keyword and pagination filters.", schemas.OrderListInput, schemas.OrderListOutput, "GET", "/v1/orders"),
    Tool("get_order", "Get an order and its remaining refundable balance.", schemas.OrderInput, schemas.OrderOutput, "GET", "/v1/orders/{order_id}", path_fields=("order_id",)),
    Tool("order_stats", "Summarize stored orders by status and date range.", schemas.DateRange, schemas.OrderStatsOutput, "GET", "/v1/orders/stats"),
    Tool("list_products", "List the tenant's product catalog and current prices.", schemas.ProductListInput, schemas.ProductListOutput, "GET", "/v1/products"),
    Tool("product_ranking", "Rank products using demo sales calculations.", schemas.RankingInput, schemas.RankingOutput, "GET", "/v1/products/ranking"),
    Tool("product_stats", "Get current price, stock and demo sales statistics for a product.", schemas.ProductStatsInput, schemas.ProductStatsOutput, "GET", "/v1/products/{sku}/stats", path_fields=("sku",)),
    Tool("inventory_summary", "Summarize inventory and stock alerts.", schemas.EmptyInput, schemas.InventorySummaryOutput, "GET", "/v1/inventory/summary"),
    Tool("inventory_alerts", "List low-stock, out-of-stock or overstock alerts.", schemas.AlertInput, schemas.AlertsOutput, "GET", "/v1/inventory/alerts"),
    Tool("inventory_sku", "Get stock and replenishment thresholds for a product.", schemas.ProductInput, schemas.InventoryOutput, "GET", "/v1/inventory/{sku}", path_fields=("sku",)),
    Tool("restock_suggestions", "Get suggested replenishment quantities and costs.", schemas.EmptyInput, schemas.RestockOutput, "GET", "/v1/inventory/restock-suggestions"),
    Tool("customers_summary", "Get the tenant's persisted customer summary.", schemas.EmptyInput, schemas.CustomerSummaryOutput, "GET", "/v1/customers/summary"),
    Tool("customers_retention", "Get the monthly customer retention snapshot.", schemas.RetentionInput, schemas.RetentionOutput, "GET", "/v1/customers/retention"),
    Tool("customers_geo", "Get customer distribution by city.", schemas.EmptyInput, schemas.GeoOutput, "GET", "/v1/customers/geo"),
    Tool("list_customers", "List customer records with sorting and pagination.", schemas.CustomerListInput, schemas.CustomerListOutput, "GET", "/v1/customers"),
    Tool("analytics_revenue", "Compare today's gross order revenue with yesterday.", schemas.RevenueInput, schemas.RevenueOutput, "GET", "/v1/analytics/revenue"),
    Tool("analytics_trend", "Get daily revenue, order or customer counts.", schemas.TrendInput, schemas.TrendOutput, "GET", "/v1/analytics/trend"),
    Tool("analytics_refunds", "Summarize persisted refunds, including partial refunds.", schemas.PeriodInput, schemas.RefundAnalyticsOutput, "GET", "/v1/analytics/refunds"),
    Tool("analytics_category", "Get demo sales calculations grouped by product category.", schemas.EmptyInput, schemas.CategoryOutput, "GET", "/v1/analytics/category"),
    Tool("analytics_aov", "Get daily average gross order value.", schemas.DaysInput, schemas.AverageOutput, "GET", "/v1/analytics/avg-order-value"),
    Tool("refund_order", "Refund an eligible order in integer cents. Backend risk policy may require approval. Never automatically retry this write.", schemas.RefundInput, schemas.RefundResult, "POST", "/v1/orders/{order_id}/refund", frozenset({"business:read", "orders:refund"}), "DYNAMIC", ("order_id",), 201),
    Tool("change_price", "Change a catalog price in integer cents. Backend risk policy may require approval. Never automatically retry this write.", schemas.PriceInput, schemas.PriceResult, "PATCH", "/v1/products/{sku}/price", frozenset({"business:read", "products:price"}), "DYNAMIC", ("sku",)),
    Tool("change_user_role", "Assign a role to a user in the authenticated tenant. Admin only. Never automatically retry this write.", schemas.RoleInput, schemas.RoleResult, "PATCH", "/v1/users/{user_id}/role", frozenset({"business:read", "roles:manage"}), "HIGH", ("user_id",)),
])
