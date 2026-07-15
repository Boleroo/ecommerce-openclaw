---
name: customer-insight
description: 客户分析：客户概览、新客/回头客比例、地域分布、客单价趋势、活跃客户。
metadata:
  {
    "openclaw":
      {
        "emoji": "👥",
        "requires": { "bins": ["curl", "jq"], "env": ["JD_API_ENDPOINT"] },
      },
  }
---

# 客户分析 (Customer Insight)

分析 $JD_SHOP_NAME 的客户群体特征。所有金额单位为**分**。

## 客户概览

```bash
curl -s "$JD_API_ENDPOINT/v1/customers/summary" | jq '{
  total_customers,
  new_customers_30d,
  repeat_rate_pct: (.repeat_rate * 100),
  avg_order_value_yuan: (.avg_order_value / 100),
  top_cities: [.top_cities[] | {city, customers, revenue_yuan: (.revenue / 100)}]
}'
```

## 新客 vs 回头客

```bash
curl -s "$JD_API_ENDPOINT/v1/customers/retention?period=month" | jq '{
  period,
  new_customers,
  repeat_customers,
  repeat_rate_pct: (.repeat_rate * 100),
  lost_customers,
  avg_orders_per_customer,
  cohort: [.cohort[] | {month, acquired, retained_pct: (.retained_pct * 100)}]
}'
```

## 地域分布

```bash
curl -s "$JD_API_ENDPOINT/v1/customers/geo" | jq '.regions[] | {
  city,
  customers,
  revenue_yuan: (.revenue / 100)
}'
```

## 活跃客户列表

```bash
# 最近下单的客户
curl -s "$JD_API_ENDPOINT/v1/customers?sort=last_order_desc&page_size=10" | jq '.customers[] | {
  customer_id,
  name,
  city,
  total_orders,
  total_spent_yuan: (.total_spent / 100),
  last_order_date,
  level
}'
```

## 高消费客户

```bash
curl -s "$JD_API_ENDPOINT/v1/customers?sort=total_spent_desc&page_size=10" | jq '.customers[] | {
  name,
  city,
  total_spent_yuan: (.total_spent / 100),
  total_orders,
  level
}'
```

## 客单价趋势

```bash
curl -s "$JD_API_ENDPOINT/v1/analytics/avg-order-value?days=7" | jq '{
  days,
  trend: [.points[] | {date, avg_order_value_yuan: (.avg_order_value / 100)}]
}'
```

## 展示注意事项

- 金额单位为**分**，展示时除以 100 转为元
- 客户姓名已脱敏（如 "王*明"），不要尝试还原
- `repeat_rate` 和 `retained_pct` 是 0-1 的小数，展示时乘以 100 加 % 号
- 客户等级: 普通 → 银牌 → 金牌 → 钻石