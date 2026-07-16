---
name: sales-query
description: 查询店铺销售数据：订单列表、订单详情、销售额、退货率、销售趋势。
metadata:
  openclaw:
    emoji: "💰"
    requires: 
      bins": ["curl", "jq"]
      env": ["JD_API_ENDPOINT"]
---

# 销售查询 (Sales Query)

查询 $JD_SHOP_NAME 的销售运营数据。所有金额单位为**分**，展示给用户时需除以 100 转为元。

## 今日销售概况

```bash
curl -s "$JD_API_ENDPOINT/v1/orders/stats" | jq '{
  date: .period.start,
  total_orders: .total_orders,
  total_amount_yuan: (.total_amount / 100),
  by_status: .by_status,
  vs_yesterday: .compared_to_yesterday
}'
```

## 指定日期范围的销售

```bash
curl -s "$JD_API_ENDPOINT/v1/orders/stats?start_date=2026-02-01&end_date=2026-02-14" | jq '{
  period: .period,
  total_orders: .total_orders,
  total_amount_yuan: (.total_amount / 100)
}'
```

## 营收分析（今日 vs 昨日）

```bash
curl -s "$JD_API_ENDPOINT/v1/analytics/revenue" | jq '{
  today_revenue_yuan: (.current.revenue / 100),
  today_orders: .current.orders,
  yesterday_revenue_yuan: (.previous.revenue / 100),
  change_pct: .change
}'
```

## 查看最近订单

```bash
# 最近 10 笔订单
curl -s "$JD_API_ENDPOINT/v1/orders?page_size=10" | jq '.orders[] | {
  order_id,
  created_at,
  status,
  customer: .customer_name,
  amount_yuan: (.total_amount / 100),
  product: .items[0].name
}'
```

## 按状态筛选订单

```bash
# 待付款订单
curl -s "$JD_API_ENDPOINT/v1/orders?status=pending" | jq '{total: .total, orders: [.orders[] | {order_id, amount_yuan: (.total_amount / 100)}]}'

# 已退款订单
curl -s "$JD_API_ENDPOINT/v1/orders?status=refunded" | jq '{total: .total, orders: [.orders[] | {order_id, amount_yuan: (.total_amount / 100)}]}'
```

## 查询单个订单

```bash
curl -s "$JD_API_ENDPOINT/v1/orders/JD202602140001" | jq '{
  order_id,
  status,
  customer: .customer_name,
  amount_yuan: (.total_amount / 100),
  items: [.items[] | {name, quantity, price_yuan: (.price / 100)}],
  logistics
}'
```

## 销售趋势（近 N 天）

```bash
# 近 7 天收入趋势
curl -s "$JD_API_ENDPOINT/v1/analytics/trend?days=7&metric=revenue" | jq '{
  metric,
  trend: [.points[] | {date, value_yuan: (.value / 100)}]
}'

# 近 7 天订单量趋势
curl -s "$JD_API_ENDPOINT/v1/analytics/trend?days=7&metric=orders" | jq '.points[] | {date, orders: .value}'
```

## 退货退款分析

```bash
curl -s "$JD_API_ENDPOINT/v1/analytics/refunds?period=week" | jq '{
  period,
  total_orders,
  refunded_orders,
  refund_rate,
  refund_amount_yuan: (.refund_amount / 100),
  top_reasons: .top_refund_reasons
}'
```

## 展示注意事项

- 金额字段值的单位是**分**，向用户展示时**必须除以 100** 转为元，如 `29900` 展示为 `¥299.00`
- 日期格式为 `YYYY-MM-DD`，时间带 `+08:00` 时区
- status 中文对照：`pending`=待付款, `paid`=已付款, `shipped`=已发货, `delivered`=已签收, `refunded`=已退款, `cancelled`=已取消
- 百分比变化 `change_pct` 正数表示增长，负数表示下降