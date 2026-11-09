---
name: product-ranking
description: 查询商品排行：热销榜、滞销品、新品表现、品类分布、单品统计。
metadata: { "openclaw": { "emoji": "🏆" } }
---

# 商品排行 (Product Ranking)

仅调用 OpenClaw 中注册的受控 Dispatcher 工具。禁止使用 `exec`、shell、`curl`、浏览器或直接访问 Flask API。运行时身份不可作为参数提供或修改。

## 工具选择与参数

- 热销/滞销/新品：`product_ranking`。`period` 为 `today|week|month`，`sort` 为 `sales_desc|sales_asc|revenue_desc`，`filter` 为 `new|all`，`limit` 使用 schema 允许的正整数。
- 单品统计：`product_stats`，必填 `sku`，可选 `period`。
- 品类分布：`analytics_category`，参数为空对象。
- 商品列表/搜索：`list_products`，可用 `status`、`category`、`keyword`、分页参数。
- 用户明确要求改价时：先用 `product_stats` 核对 SKU 和当前价格，再调用可选工具 `change_price`。`price` 是正整数分值，且必须提供稳定 `idempotency_key`。不得自行调整价格。

## 错误处理

只信任结构化 Observation：成功读取 `data`；失败报告 `error.code` 与 `trace_id`。

- `INVALID_ARGUMENT`：按 schema 修正，不能猜 SKU、枚举或价格。
- `RESOURCE_NOT_FOUND`：用 `list_products` 在当前授权租户内确认；不得切换租户。
- 权限/认证错误：停止并报告，不尝试直连后端。
- `APPROVAL_REQUIRED`：告知用户已创建待审批请求。Agent 不可批准/拒绝，也不可接触人工审批凭证。
- `WRITE_OUTCOME_UNKNOWN`：不得重试改价；保留原 key 和 trace 供人工核对。

金额字段单位为分，展示时除以 100。比例字段按其 schema 含义展示，不能再次错误缩放。
