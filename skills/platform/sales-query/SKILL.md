---
name: sales-query
description: 查询店铺销售数据：订单列表、订单详情、销售额、退货率、销售趋势。
metadata: { "openclaw": { "emoji": "💰" } }
---

# 销售查询 (Sales Query)

仅使用已注册的受控 Dispatcher 工具，不得使用 `exec`、shell、`curl`、浏览器或直接 HTTP 请求业务后端。身份和租户由可信运行时凭证确定，绝不能把 token、`tenant_id`、`user_id` 或角色放入工具参数。

## 工具选择

- 销售概况：`order_stats`，可选 `start_date`、`end_date`（`YYYY-MM-DD`）。
- 最近或筛选订单：`list_orders`，使用 `status`、`keyword`、日期、分页和排序参数。
- 单个订单：`get_order`，必填 `order_id`。
- 今日营收比较：`analytics_revenue`，`period` 只能为 `daily`。
- 趋势：`analytics_trend`，`days` 为 1–366，`metric` 为 `revenue`、`orders` 或 `customers`。
- 退款分析：`analytics_refunds`，`period` 为 `today`、`week` 或 `month`。
- 用户明确要求退款时：先用 `get_order` 核对状态和可退余额，再调用可选工具 `refund_order`。必填 `order_id`、正整数分值 `amount` 和稳定的 `idempotency_key`；可选 `reason`。不得自行改变金额或为同一未知结果生成新 key。

## 结构化 Observation

先检查 `success`。成功时只使用 `data`；失败时报告 `error.code`、安全的 `message` 和 `trace_id`。

- `INVALID_ARGUMENT`：根据 `retry_hint` 和工具 schema 修正参数；不要猜订单号或金额。
- `RESOURCE_NOT_FOUND`：可用授权列表工具确认标识符；不得更改或伪造租户身份。
- `PERMISSION_DENIED` / `TENANT_ACCESS_DENIED` / `UNAUTHENTICATED`：如实报告并停止，不得尝试其他接口或凭证绕过。
- `APPROVAL_REQUIRED`：说明请求已进入人工审批，返回可安全展示的审批 ID、状态和 `trace_id`。Agent 不能批准或拒绝；不要索取或使用人工审批凭证。
- `WRITE_OUTCOME_UNKNOWN`：立即停止，明确说明不得自动重试；保留原 `idempotency_key` 和 `trace_id` 供人工核对。

所有金额均为分，展示给用户时除以 100。订单状态：`pending` 待付款、`paid` 已付款、`shipped` 已发货、`delivered` 已签收、`refunded` 已退款、`cancelled` 已取消。
