from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def valid_day(value):
    date.fromisoformat(value)
    return value


def valid_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value


def valid_month(value):
    date.fromisoformat(f"{value}-01")
    return value


Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
Positive = Annotated[int, Field(gt=0)]
Nonnegative = Annotated[int, Field(ge=0)]
Price = Annotated[int, Field(ge=1, le=100_000_000)]
Ratio = Annotated[float, Field(ge=0, le=1)]
Day = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", json_schema_extra={"format": "date"}), AfterValidator(valid_day)]
Timestamp = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$", json_schema_extra={"format": "date-time"}), AfterValidator(valid_timestamp)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"), AfterValidator(valid_month)]
Period = Literal["today", "week", "month"]
OrderStatus = Literal["pending", "paid", "shipped", "delivered", "refunded", "cancelled"]
UserRole = Literal["viewer", "operator", "manager", "admin"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EmptyInput(Schema):
    pass


class ToolCall(Schema):
    tool: Annotated[str, Field(min_length=1, max_length=128)]
    arguments: dict = Field(default_factory=dict)


class Identity(Schema):
    user_id: Identifier
    tenant_id: Identifier
    role: UserRole
    permissions: list[str]


class DateRange(Schema):
    start_date: Day = Field(default_factory=lambda: date.today().isoformat())
    end_date: Day = Field(default_factory=lambda: date.today().isoformat())

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self


class Pagination(Schema):
    page: Positive = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 20


class OrderListInput(DateRange, Pagination):
    status: OrderStatus | None = None
    keyword: Annotated[str, Field(max_length=200)] | None = None
    sort: Literal["created_desc", "created_asc", "amount_desc"] = "created_desc"


class OrderInput(Schema):
    order_id: Identifier


class ProductInput(Schema):
    sku: Identifier


class ProductListInput(Pagination):
    status: Literal["all", "on_sale", "off_shelf"] = "all"
    category: Annotated[str, Field(max_length=100)] | None = None
    keyword: Annotated[str, Field(max_length=200)] | None = None


class PeriodInput(Schema):
    period: Period = "week"


class RankingInput(PeriodInput):
    sort: Literal["sales_desc", "sales_asc", "revenue_desc", "revenue_asc"] = "sales_desc"
    filter: Literal["all", "new"] = "all"
    limit: Annotated[int, Field(ge=1, le=100)] = 10


class ProductStatsInput(ProductInput, PeriodInput):
    pass


class AlertInput(Schema):
    type: Literal["low_stock", "out_of_stock", "overstock"] = "low_stock"
    threshold: Positive | None = None


class RetentionInput(Schema):
    period: Literal["month"] = "month"


class CustomerListInput(Pagination):
    sort: Literal["last_order_desc", "total_spent_desc", "order_count_desc"] = "last_order_desc"


class RevenueInput(Schema):
    period: Literal["daily"] = "daily"


class DaysInput(Schema):
    days: Annotated[int, Field(ge=1, le=366)] = 7


class TrendInput(DaysInput):
    metric: Literal["revenue", "orders", "customers"] = "revenue"


class RefundInput(OrderInput):
    amount: Positive
    reason: Annotated[str, Field(max_length=500)] = ""


class PriceInput(ProductInput):
    price: Price


class RoleInput(Schema):
    user_id: Identifier
    role: UserRole


class Item(Schema):
    sku: Identifier
    name: str
    quantity: Positive
    price: Price


class Logistics(Schema):
    carrier: str
    tracking_no: str


class OrderOutput(Schema):
    order_id: Identifier
    created_at: Timestamp
    status: OrderStatus
    customer_name: str
    total_amount: Positive
    refunded_amount: Nonnegative
    remaining_refundable_amount: Nonnegative
    items: Annotated[list[Item], Field(min_length=1)]
    logistics: Logistics | None

    @model_validator(mode="after")
    def consistent_balances(self):
        if self.refunded_amount + self.remaining_refundable_amount != self.total_amount:
            raise ValueError("Inconsistent refundable balance")
        if sum(item.price * item.quantity for item in self.items) != self.total_amount:
            raise ValueError("Order total does not match its items")
        if (self.status == "refunded") != (self.refunded_amount == self.total_amount):
            raise ValueError("Refund status does not match the balance")
        return self


class PageOutput(Schema):
    total: Nonnegative
    page: Positive
    page_size: Annotated[int, Field(ge=1, le=100)]


class OrderListOutput(PageOutput):
    orders: list[OrderOutput]


class DatePeriod(Schema):
    start: Day
    end: Day


class StatusCounts(Schema):
    pending: Nonnegative
    paid: Nonnegative
    shipped: Nonnegative
    delivered: Nonnegative
    refunded: Nonnegative
    cancelled: Nonnegative


class YesterdayComparison(Schema):
    orders_change_pct: float
    amount_change_pct: float


class OrderStatsOutput(Schema):
    period: DatePeriod
    total_orders: Nonnegative
    total_amount: Nonnegative
    by_status: StatusCounts
    compared_to_yesterday: YesterdayComparison


class ProductOutput(Schema):
    sku: Identifier
    name: str
    category: str
    price: Price
    status: Literal["on_sale", "off_shelf"]
    listed_at: Day


class ProductListOutput(PageOutput):
    products: list[ProductOutput]


class RankingItem(Schema):
    sku: Identifier
    name: str
    category: str
    sales_count: Nonnegative
    revenue: Nonnegative
    avg_price: Price
    stock_remaining: Nonnegative
    rank: Positive


class RankingOutput(Schema):
    period: Period
    ranking: list[RankingItem]


class ProductStatsOutput(Schema):
    sku: Identifier
    name: str
    category: str
    period: Period
    sales_count: Nonnegative
    revenue: Nonnegative
    avg_daily_sales: Annotated[float, Field(ge=0)]
    current_stock: Nonnegative
    price: Price


class InventorySummaryOutput(Schema):
    total_skus: Nonnegative
    total_stock: Nonnegative
    low_stock_count: Nonnegative
    out_of_stock_count: Nonnegative
    overstock_count: Nonnegative


class InventoryOutput(Schema):
    sku: Identifier
    name: str
    current_stock: Nonnegative
    threshold: Positive
    daily_sales_avg: Nonnegative
    estimated_stockout_days: Annotated[float, Field(ge=0)]
    status: Literal["out_of_stock", "low_stock", "normal"]


class InventoryAlert(Schema):
    sku: Identifier
    name: str
    current_stock: Nonnegative
    threshold: Positive
    daily_sales_avg: Nonnegative
    estimated_stockout_days: Annotated[float, Field(ge=0)]
    alert_type: Literal["low_stock", "out_of_stock", "overstock"]
    suggested_restock: Nonnegative


class AlertsOutput(Schema):
    alerts: list[InventoryAlert]


class RestockSuggestion(Schema):
    sku: Identifier
    name: str
    current_stock: Nonnegative
    daily_sales_avg: Nonnegative
    suggested_quantity: Nonnegative
    estimated_cost: Nonnegative
    urgency: Literal["critical", "high", "medium"]


class RestockOutput(Schema):
    suggestions: list[RestockSuggestion]


class Region(Schema):
    city: str
    customers: Nonnegative
    revenue: Nonnegative


class CustomerSummaryOutput(Schema):
    total_customers: Nonnegative
    new_customers_30d: Nonnegative
    repeat_rate: Ratio
    avg_order_value: Nonnegative
    top_cities: list[Region]


class Cohort(Schema):
    month: Month
    acquired: Nonnegative
    retained_pct: Ratio


class RetentionOutput(Schema):
    period: Literal["month"]
    new_customers: Nonnegative
    repeat_customers: Nonnegative
    repeat_rate: Ratio
    lost_customers: Nonnegative
    avg_orders_per_customer: Annotated[float, Field(ge=0)]
    cohort: list[Cohort]


class GeoOutput(Schema):
    regions: list[Region]


class Customer(Schema):
    customer_id: Identifier
    name: str
    city: str
    total_orders: Nonnegative
    total_spent: Nonnegative
    last_order_date: Day
    level: Literal["普通", "银牌", "金牌", "钻石"]


class CustomerListOutput(PageOutput):
    customers: list[Customer]


class RevenueSnapshot(Schema):
    date: Day
    revenue: Nonnegative
    orders: Nonnegative
    avg_order_value: Nonnegative


class RevenueChange(Schema):
    revenue_pct: float
    orders_pct: float
    avg_order_value_pct: float


class RevenueOutput(Schema):
    period: Literal["daily"]
    current: RevenueSnapshot
    previous: RevenueSnapshot
    change: RevenueChange


class TrendPoint(Schema):
    date: Day
    value: Nonnegative


class TrendOutput(Schema):
    metric: Literal["revenue", "orders", "customers"]
    points: list[TrendPoint]


class RefundReason(Schema):
    reason: str
    count: Positive


class RefundAnalyticsOutput(Schema):
    period: Period
    total_orders: Nonnegative
    refunded_orders: Nonnegative
    refund_rate: Ratio
    refund_amount: Nonnegative
    top_refund_reasons: list[RefundReason]


class Category(Schema):
    category: str
    product_count: Positive
    sales_count: Nonnegative
    revenue: Nonnegative
    revenue_pct: Ratio


class CategoryOutput(Schema):
    categories: list[Category]


class AveragePoint(Schema):
    date: Day
    avg_order_value: Nonnegative


class AverageOutput(Schema):
    days: Positive
    points: list[AveragePoint]


class RefundOutput(Schema):
    id: Identifier
    order_id: Identifier
    amount: Positive
    reason: Annotated[str, Field(max_length=500)]
    created_at: Timestamp


class RefundResult(Schema):
    refund: RefundOutput
    order: OrderOutput


class PriceResult(Schema):
    product: ProductOutput
    previous_price: Price


class RoleResult(Schema):
    user_id: Identifier
    role: UserRole
