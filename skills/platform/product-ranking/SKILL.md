---
name: product-ranking
description: 查询商品排行：热销榜、滞销品、新品表现、品类分布、单品统计。
metadata:
  openclaw:
    emoji: "🏆"
    requires: 
      bins: ["curl", "jq"]
      env: ["JD_API_ENDPOINT"]
---

# 商品排行 (Product Ranking)

查询 $JD_SHOP_NAME 的商品销售排行与统计。所有金额单位为**分**。

## 热销 TOP N

```bash
# 本周热销前 10
curl -s "$JD_API_ENDPOINT/v1/products/ranking?sort=sales_desc&period=week&limit=10" | jq '.ranking[] | {
  rank,
  sku,
  name,
  category,
  sales_count,
  revenue_yuan: (.revenue / 100),
  stock_remaining
}'
```

## 滞销商品

```bash
curl -s "$JD_API_ENDPOINT/v1/products/ranking?sort=sales_asc&period=month&limit=5" | jq '.ranking[] | {
  rank,
  name,
  sales_count,
  stock_remaining
}'
```

## 新品表现（近 30 天上架）

```bash
curl -s "$JD_API_ENDPOINT/v1/products/ranking?filter=new&sort=sales_desc" | jq '.ranking[] | {
  name,
  sales_count,
  revenue_yuan: (.revenue / 100)
}'
```

## 单品销售统计

```bash
# 查询某个 SKU 的销售详情（替换 SKU 编码）
curl -s "$JD_API_ENDPOINT/v1/products/SHOE-WH-42/stats?period=week" | jq '{
  sku,
  name,
  period,
  sales_count,
  revenue_yuan: (.revenue / 100),
  avg_daily_sales,
  current_stock,
  price_yuan: (.price / 100)
}'
```

## 品类销售分布

```bash
curl -s "$JD_API_ENDPOINT/v1/analytics/category" | jq '.categories[] | {
  category,
  product_count,
  sales_count,
  revenue_yuan: (.revenue / 100),
  revenue_share: (.revenue_pct * 100 | tostring + "%")
}'
```

## 商品列表

```bash
# 在售商品
curl -s "$JD_API_ENDPOINT/v1/products?status=on_sale" | jq '.products[] | {
  sku,
  name,
  category,
  price_yuan: (.price / 100),
  status
}'

# 搜索商品
curl -s "$JD_API_ENDPOINT/v1/products?keyword=白色" | jq '.products[] | {sku, name}'
```

## 参数说明

- `period`: `today` (今天) / `week` (本周) / `month` (本月)
- `sort`: `sales_desc` (销量降序) / `sales_asc` (销量升序) / `revenue_desc` (收入降序)
- `filter`: `new` (近 30 天上架) / `all` (全部)
- `limit`: 返回条数，默认 10
- 金额字段单位是**分**，展示时除以 100 转为元