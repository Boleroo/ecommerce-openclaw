---
name: inventory-alert
description: 库存监控：低库存预警、断货提醒、库存总览、补货建议。
metadata:
  openclaw:
    emoji: "📦"
    requires: 
      bins: ["curl", "jq"]
      env: ["JD_API_ENDPOINT"]
---

# 库存预警 (Inventory Alert)

监控 $JD_SHOP_NAME 的库存状况，及时发现缺货风险。

## 库存总览

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/summary" | jq .
```

返回字段：`total_skus` (总SKU数), `total_stock` (总库存), `low_stock_count` (低库存数), `out_of_stock_count` (断货数), `overstock_count` (积压数)

## 低库存预警

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/alerts?type=low_stock" | jq '.alerts[] | {
  sku,
  name,
  current_stock,
  threshold,
  daily_sales_avg,
  estimated_stockout_days,
  suggested_restock
}'
```

## 断货商品

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/alerts?type=out_of_stock" | jq '.alerts[] | {
  sku,
  name,
  daily_sales_avg,
  suggested_restock
}'
```

## 积压商品（库存过高）

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/alerts?type=overstock" | jq '.alerts[] | {
  sku,
  name,
  current_stock,
  daily_sales_avg
}'
```

## 查询单个 SKU 的库存

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/SHOE-WH-42" | jq .
```

返回字段：`current_stock`, `threshold`, `daily_sales_avg`, `estimated_stockout_days`, `status` (out_of_stock / low_stock / normal)

## 补货建议

```bash
curl -s "$JD_API_ENDPOINT/v1/inventory/restock-suggestions" | jq '.suggestions[] | {
  sku,
  name,
  current_stock,
  daily_sales_avg,
  suggested_quantity,
  estimated_cost_yuan: (.estimated_cost / 100),
  urgency
}'
```

## 状态与紧急程度说明

- `out_of_stock`: 已断货 (库存=0)，需立即补货
- `low_stock`: 低库存 (低于阈值)，需尽快补货
- `normal`: 库存正常
- urgency 紧急程度: `critical` (已断货) > `high` (库存<5) > `medium` (低于阈值)
- 预估成本 `estimated_cost` 单位为**分**，展示时除以 100 转为元
- `estimated_stockout_days` 表示按当前销售速度，多少天后断货