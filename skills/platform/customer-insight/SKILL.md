---
name: customer-insight
description: 客户分析：客户概览、新客/回头客比例、地域分布、客单价趋势、活跃客户。
metadata: { "openclaw": { "emoji": "👥" } }
---

# 客户分析 (Customer Insight)

仅使用注册的受控 Dispatcher 工具。不得用 `exec`、shell、`curl`、浏览器或直接 HTTP 请求后端。身份和租户来自运行时凭证，绝不作为工具参数。

## 工具选择与参数

- 客户概览：`customers_summary`，参数 `{}`。
- 新客/回头客：`customers_retention`，`period` 为 `month`。
- 地域分布：`customers_geo`，参数 `{}`。
- 活跃/高消费客户：`list_customers`，`sort` 为 `last_order_desc`、`total_spent_desc` 或 `order_count_desc`，并使用合法分页参数。
- 客单价趋势：`analytics_aov`，`days` 为 1–366。

## Observation 处理

检查 `success` 后再读 `data`。验证错误只能按 schema 修正；资源缺失只能在当前租户的授权列表内核对。认证、权限或跨租户拒绝必须原样报告，不能尝试其他接口、身份或工具绕过。若收到 `APPROVAL_REQUIRED` 或 `WRITE_OUTCOME_UNKNOWN`，报告 `error.code` 和 `trace_id` 并停止；Agent 不能批准/拒绝请求，也不能自动重试未知写结果。

金额单位为分，展示时除以 100。`repeat_rate` 和 `retained_pct` 为 0–1 比例。客户姓名已脱敏，不得尝试还原或关联外部数据。
