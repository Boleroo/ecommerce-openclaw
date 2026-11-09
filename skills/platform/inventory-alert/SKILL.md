---
name: inventory-alert
description: 库存监控：低库存预警、断货提醒、库存总览、补货建议。
metadata: { "openclaw": { "emoji": "📦" } }
---

# 库存预警 (Inventory Alert)

所有数据必须通过受控 Dispatcher 工具获取。禁止 `exec`、shell、`curl`、浏览器和直接业务 API 请求；不得把 token 或租户身份写入参数。

## 工具选择

- 总览：`inventory_summary`，参数 `{}`。
- 预警：`inventory_alerts`，`type` 必须为 `low_stock`、`out_of_stock` 或 `overstock`。
- 单 SKU：`inventory_sku`，必填 `sku`。
- 补货建议：`restock_suggestions`，参数 `{}`。
- 需要商品名称或核对 SKU 时：使用 `list_products` 或 `product_stats`，不要猜测。

## Observation 处理

- `success: true` 时读取 `data`。
- `INVALID_ARGUMENT` 可依据 schema 和 `retry_hint` 修正一次明确的参数错误。
- `RESOURCE_NOT_FOUND` 可用授权商品列表核对 SKU。
- `PERMISSION_DENIED`、`TENANT_ACCESS_DENIED`、`UNAUTHENTICATED` 必须停止并报告，不能绕过。
- `APPROVAL_REQUIRED` 不应由这些只读库存工具产生；若出现，报告 trace 并停止。
- `WRITE_OUTCOME_UNKNOWN` 不应自动重试，报告 `trace_id` 供调查。

`estimated_cost` 单位为分，展示时除以 100。不要从预警结果推断或执行补货写操作；平台没有注册此类写工具。
