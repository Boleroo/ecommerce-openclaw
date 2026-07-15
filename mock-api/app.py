"""
JD 商家数据 Mock API
PoC 用途：为 OpenClaw Skills 提供模拟的商家运营数据。
店铺：张三运动鞋专营店 (merchant-12345)
"""

import random
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Mock data: 张三运动鞋专营店
# ---------------------------------------------------------------------------

PRODUCTS = [
    {"sku": "SHOE-WH-42", "name": "经典白色运动鞋 42码", "category": "运动鞋", "price": 29900, "status": "on_sale", "listed_at": "2025-12-01"},
    {"sku": "SHOE-WH-43", "name": "经典白色运动鞋 43码", "category": "运动鞋", "price": 29900, "status": "on_sale", "listed_at": "2025-12-01"},
    {"sku": "SHOE-BK-42", "name": "经典黑色运动鞋 42码", "category": "运动鞋", "price": 28900, "status": "on_sale", "listed_at": "2025-12-15"},
    {"sku": "SHOE-BK-43", "name": "经典黑色运动鞋 43码", "category": "运动鞋", "price": 28900, "status": "on_sale", "listed_at": "2025-12-15"},
    {"sku": "SHOE-RD-42", "name": "限定红色运动鞋 42码", "category": "运动鞋", "price": 35900, "status": "on_sale", "listed_at": "2026-01-20"},
    {"sku": "SNKR-BL-41", "name": "透气蓝色跑步鞋 41码", "category": "跑步鞋", "price": 39900, "status": "on_sale", "listed_at": "2026-01-10"},
    {"sku": "SNKR-BL-42", "name": "透气蓝色跑步鞋 42码", "category": "跑步鞋", "price": 39900, "status": "on_sale", "listed_at": "2026-01-10"},
    {"sku": "SNKR-GR-42", "name": "透气绿色跑步鞋 42码", "category": "跑步鞋", "price": 39900, "status": "on_sale", "listed_at": "2026-01-10"},
    {"sku": "BOOT-BK-42", "name": "高帮黑色篮球鞋 42码", "category": "篮球鞋", "price": 49900, "status": "on_sale", "listed_at": "2025-11-01"},
    {"sku": "BOOT-WH-43", "name": "高帮白色篮球鞋 43码", "category": "篮球鞋", "price": 49900, "status": "on_sale", "listed_at": "2025-11-01"},
    {"sku": "SNDL-BK-42", "name": "运动凉鞋 黑色 42码", "category": "凉鞋", "price": 15900, "status": "off_shelf", "listed_at": "2025-06-01"},
    {"sku": "SLPR-GY-M", "name": "居家拖鞋 灰色 M码", "category": "拖鞋", "price": 5900, "status": "on_sale", "listed_at": "2025-08-01"},
]

INVENTORY = {
    "SHOE-WH-42": {"stock": 3, "threshold": 10, "daily_avg": 18},
    "SHOE-WH-43": {"stock": 25, "threshold": 10, "daily_avg": 12},
    "SHOE-BK-42": {"stock": 45, "threshold": 10, "daily_avg": 8},
    "SHOE-BK-43": {"stock": 38, "threshold": 10, "daily_avg": 6},
    "SHOE-RD-42": {"stock": 0, "threshold": 10, "daily_avg": 15},
    "SNKR-BL-41": {"stock": 60, "threshold": 15, "daily_avg": 5},
    "SNKR-BL-42": {"stock": 8, "threshold": 15, "daily_avg": 10},
    "SNKR-GR-42": {"stock": 72, "threshold": 15, "daily_avg": 4},
    "BOOT-BK-42": {"stock": 120, "threshold": 20, "daily_avg": 3},
    "BOOT-WH-43": {"stock": 95, "threshold": 20, "daily_avg": 2},
    "SNDL-BK-42": {"stock": 200, "threshold": 30, "daily_avg": 0},
    "SLPR-GY-M": {"stock": 150, "threshold": 50, "daily_avg": 7},
}

CUSTOMERS_DATA = [
    {"city": "北京", "customers": 523, "revenue": 18560000},
    {"city": "上海", "customers": 412, "revenue": 15230000},
    {"city": "广州", "customers": 298, "revenue": 10540000},
    {"city": "深圳", "customers": 276, "revenue": 9870000},
    {"city": "成都", "customers": 198, "revenue": 7020000},
    {"city": "杭州", "customers": 185, "revenue": 6550000},
    {"city": "武汉", "customers": 168, "revenue": 5960000},
    {"city": "南京", "customers": 155, "revenue": 5490000},
    {"city": "重庆", "customers": 142, "revenue": 5030000},
    {"city": "西安", "customers": 128, "revenue": 4530000},
]

CUSTOMER_NAMES = [
    "王*明", "李*华", "张*丽", "刘*强", "陈*龙", "杨*芳", "黄*伟",
    "赵*敏", "周*杰", "吴*霞", "孙*鹏", "马*婷", "朱*军", "胡*燕",
]

CARRIERS = ["京东物流", "顺丰速运", "中通快递", "圆通速递", "韵达快递"]
STATUSES = ["pending", "paid", "shipped", "delivered", "refunded", "cancelled"]


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _date_n_days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _gen_order_id(idx):
    return f"JD{datetime.now().strftime('%Y%m%d')}{idx:04d}"


def _seed_daily(day_offset=0):
    """Return a deterministic seed for a given day offset from today."""
    d = datetime.now() - timedelta(days=day_offset)
    return int(d.strftime("%Y%m%d"))


def _gen_orders(start_date, end_date, status_filter=None, keyword=None):
    """Generate deterministic mock orders for a date range."""
    orders = []
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    ed = datetime.strptime(end_date, "%Y-%m-%d")
    day = sd
    global_idx = 1
    while day <= ed:
        rng = random.Random(_seed_daily((datetime.now() - day).days))
        count = rng.randint(35, 55)
        for i in range(count):
            prod = rng.choice(PRODUCTS[:10])  # only on_sale products
            qty = rng.randint(1, 3)
            st = rng.choices(
                STATUSES, weights=[8, 12, 35, 40, 4, 1], k=1
            )[0]
            hour = rng.randint(0, 23)
            minute = rng.randint(0, 59)
            order = {
                "order_id": f"JD{day.strftime('%Y%m%d')}{global_idx:04d}",
                "created_at": f"{day.strftime('%Y-%m-%d')}T{hour:02d}:{minute:02d}:00+08:00",
                "status": st,
                "customer_name": rng.choice(CUSTOMER_NAMES),
                "total_amount": prod["price"] * qty,
                "items": [
                    {
                        "sku": prod["sku"],
                        "name": prod["name"],
                        "quantity": qty,
                        "price": prod["price"],
                    }
                ],
                "logistics": {
                    "carrier": rng.choice(CARRIERS),
                    "tracking_no": f"JD{rng.randint(10**9, 10**10 - 1)}",
                }
                if st in ("shipped", "delivered")
                else None,
            }
            if status_filter and order["status"] != status_filter:
                global_idx += 1
                day_delta_check = True  # skip
            else:
                if keyword:
                    kw = keyword.lower()
                    if kw not in order["order_id"].lower() and not any(
                        kw in it["name"].lower() for it in order["items"]
                    ):
                        global_idx += 1
                        continue
                orders.append(order)
            global_idx += 1
        day += timedelta(days=1)
    return orders


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Orders API
# ---------------------------------------------------------------------------

@app.route("/v1/orders")
def list_orders():
    start = request.args.get("start_date", _today())
    end = request.args.get("end_date", _today())
    status = request.args.get("status")
    keyword = request.args.get("keyword")
    sort = request.args.get("sort", "created_desc")
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)

    orders = _gen_orders(start, end, status, keyword)

    if sort == "created_asc":
        orders.sort(key=lambda o: o["created_at"])
    elif sort == "amount_desc":
        orders.sort(key=lambda o: o["total_amount"], reverse=True)
    else:
        orders.sort(key=lambda o: o["created_at"], reverse=True)

    total = len(orders)
    start_idx = (page - 1) * page_size
    page_orders = orders[start_idx : start_idx + page_size]

    return jsonify(
        {"total": total, "page": page, "page_size": page_size, "orders": page_orders}
    )


@app.route("/v1/orders/<order_id>")
def get_order(order_id):
    # Try to extract date from order_id (JD20260214xxxx)
    try:
        date_part = order_id[2:10]
        date_str = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
        orders = _gen_orders(date_str, date_str)
        for o in orders:
            if o["order_id"] == order_id:
                return jsonify(o)
    except (ValueError, IndexError):
        pass
    return jsonify({"code": 40401, "message": f"order {order_id} not found"}), 404


@app.route("/v1/orders/stats")
def order_stats():
    start = request.args.get("start_date", _today())
    end = request.args.get("end_date", _today())
    orders = _gen_orders(start, end)

    by_status = {}
    for s in STATUSES:
        by_status[s] = sum(1 for o in orders if o["status"] == s)

    total_amount = sum(o["total_amount"] for o in orders)

    # Yesterday comparison
    yd = _date_n_days_ago(1)
    yesterday_orders = _gen_orders(yd, yd)
    yd_amount = sum(o["total_amount"] for o in yesterday_orders)
    yd_count = len(yesterday_orders)

    orders_pct = ((len(orders) - yd_count) / max(yd_count, 1)) * 100
    amount_pct = ((total_amount - yd_amount) / max(yd_amount, 1)) * 100

    return jsonify(
        {
            "period": {"start": start, "end": end},
            "total_orders": len(orders),
            "total_amount": total_amount,
            "by_status": by_status,
            "compared_to_yesterday": {
                "orders_change_pct": round(orders_pct, 1),
                "amount_change_pct": round(amount_pct, 1),
            },
        }
    )


# ---------------------------------------------------------------------------
# Products API
# ---------------------------------------------------------------------------

@app.route("/v1/products")
def list_products():
    status = request.args.get("status", "all")
    category = request.args.get("category")
    keyword = request.args.get("keyword")
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)

    prods = PRODUCTS[:]
    if status != "all":
        prods = [p for p in prods if p["status"] == status]
    if category:
        prods = [p for p in prods if p["category"] == category]
    if keyword:
        kw = keyword.lower()
        prods = [p for p in prods if kw in p["name"].lower() or kw in p["sku"].lower()]

    total = len(prods)
    start_idx = (page - 1) * page_size
    return jsonify(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "products": prods[start_idx : start_idx + page_size],
        }
    )


@app.route("/v1/products/ranking")
def product_ranking():
    sort = request.args.get("sort", "sales_desc")
    period = request.args.get("period", "week")
    filt = request.args.get("filter", "all")
    limit = int(request.args.get("limit", 10))

    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)
    rng = random.Random(42)

    ranking = []
    for p in PRODUCTS:
        if p["status"] != "on_sale":
            continue
        if filt == "new":
            listed = datetime.strptime(p["listed_at"], "%Y-%m-%d")
            if (datetime.now() - listed).days > 30:
                continue
        sales = rng.randint(20, 200) * period_days // 7
        inv = INVENTORY.get(p["sku"], {})
        ranking.append(
            {
                "sku": p["sku"],
                "name": p["name"],
                "category": p["category"],
                "sales_count": sales,
                "revenue": sales * p["price"],
                "avg_price": p["price"],
                "stock_remaining": inv.get("stock", 0),
            }
        )

    reverse = sort in ("sales_desc", "revenue_desc")
    key = "sales_count" if "sales" in sort else "revenue"
    ranking.sort(key=lambda r: r[key], reverse=reverse)

    for i, r in enumerate(ranking, 1):
        r["rank"] = i

    return jsonify({"period": period, "ranking": ranking[:limit]})


@app.route("/v1/products/<sku>/stats")
def product_stats(sku):
    prod = next((p for p in PRODUCTS if p["sku"] == sku), None)
    if not prod:
        return jsonify({"code": 40402, "message": f"product {sku} not found"}), 404

    period = request.args.get("period", "week")
    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)
    rng = random.Random(hash(sku))
    sales = rng.randint(30, 180) * period_days // 7
    inv = INVENTORY.get(sku, {})

    return jsonify(
        {
            "sku": sku,
            "name": prod["name"],
            "category": prod["category"],
            "period": period,
            "sales_count": sales,
            "revenue": sales * prod["price"],
            "avg_daily_sales": round(sales / max(period_days, 1), 1),
            "current_stock": inv.get("stock", 0),
            "price": prod["price"],
        }
    )


# ---------------------------------------------------------------------------
# Inventory API
# ---------------------------------------------------------------------------

@app.route("/v1/inventory/summary")
def inventory_summary():
    total_skus = len(INVENTORY)
    total_stock = sum(v["stock"] for v in INVENTORY.values())
    low = sum(1 for v in INVENTORY.values() if 0 < v["stock"] < v["threshold"])
    oos = sum(1 for v in INVENTORY.values() if v["stock"] == 0)
    over = sum(1 for v in INVENTORY.values() if v["stock"] > v["threshold"] * 5)

    return jsonify(
        {
            "total_skus": total_skus,
            "total_stock": total_stock,
            "low_stock_count": low,
            "out_of_stock_count": oos,
            "overstock_count": over,
        }
    )


@app.route("/v1/inventory/alerts")
def inventory_alerts():
    alert_type = request.args.get("type", "low_stock")
    threshold_override = request.args.get("threshold", type=int)

    alerts = []
    for sku, inv in INVENTORY.items():
        prod = next((p for p in PRODUCTS if p["sku"] == sku), None)
        if not prod:
            continue
        th = threshold_override if threshold_override else inv["threshold"]
        stock = inv["stock"]
        daily = inv["daily_avg"]

        if alert_type == "out_of_stock" and stock != 0:
            continue
        if alert_type == "low_stock" and (stock == 0 or stock >= th):
            continue
        if alert_type == "overstock" and stock < th * 5:
            continue

        est_days = round(stock / max(daily, 0.1), 1)
        alerts.append(
            {
                "sku": sku,
                "name": prod["name"],
                "current_stock": stock,
                "threshold": th,
                "daily_sales_avg": daily,
                "estimated_stockout_days": est_days,
                "alert_type": alert_type,
                "suggested_restock": max(daily * 14 - stock, 0),
            }
        )

    alerts.sort(key=lambda a: a["current_stock"])
    return jsonify({"alerts": alerts})


@app.route("/v1/inventory/<sku>")
def inventory_sku(sku):
    inv = INVENTORY.get(sku)
    if not inv:
        return jsonify({"code": 40403, "message": f"sku {sku} not found"}), 404
    prod = next((p for p in PRODUCTS if p["sku"] == sku), None)
    return jsonify(
        {
            "sku": sku,
            "name": prod["name"] if prod else sku,
            "current_stock": inv["stock"],
            "threshold": inv["threshold"],
            "daily_sales_avg": inv["daily_avg"],
            "estimated_stockout_days": round(inv["stock"] / max(inv["daily_avg"], 0.1), 1),
            "status": "out_of_stock" if inv["stock"] == 0 else ("low_stock" if inv["stock"] < inv["threshold"] else "normal"),
        }
    )


@app.route("/v1/inventory/restock-suggestions")
def restock_suggestions():
    suggestions = []
    for sku, inv in INVENTORY.items():
        if inv["stock"] < inv["threshold"] and inv["daily_avg"] > 0:
            prod = next((p for p in PRODUCTS if p["sku"] == sku), None)
            restock_qty = max(inv["daily_avg"] * 14 - inv["stock"], 0)
            suggestions.append(
                {
                    "sku": sku,
                    "name": prod["name"] if prod else sku,
                    "current_stock": inv["stock"],
                    "daily_sales_avg": inv["daily_avg"],
                    "suggested_quantity": restock_qty,
                    "estimated_cost": restock_qty * (prod["price"] // 2 if prod else 10000),
                    "urgency": "critical" if inv["stock"] == 0 else "high" if inv["stock"] < 5 else "medium",
                }
            )
    suggestions.sort(key=lambda s: {"critical": 0, "high": 1, "medium": 2}[s["urgency"]])
    return jsonify({"suggestions": suggestions})


# ---------------------------------------------------------------------------
# Customers API
# ---------------------------------------------------------------------------

@app.route("/v1/customers/summary")
def customers_summary():
    return jsonify(
        {
            "total_customers": 3256,
            "new_customers_30d": 487,
            "repeat_rate": 0.42,
            "avg_order_value": 35600,
            "top_cities": CUSTOMERS_DATA[:5],
        }
    )


@app.route("/v1/customers/retention")
def customers_retention():
    period = request.args.get("period", "month")
    return jsonify(
        {
            "period": period,
            "new_customers": 487,
            "repeat_customers": 352,
            "repeat_rate": 0.42,
            "lost_customers": 89,
            "avg_orders_per_customer": 1.8,
            "cohort": [
                {"month": _date_n_days_ago(30)[:7], "acquired": 520, "retained_pct": 0.38},
                {"month": _date_n_days_ago(60)[:7], "acquired": 498, "retained_pct": 0.35},
                {"month": _date_n_days_ago(90)[:7], "acquired": 465, "retained_pct": 0.32},
            ],
        }
    )


@app.route("/v1/customers/geo")
def customers_geo():
    return jsonify({"regions": CUSTOMERS_DATA})


@app.route("/v1/customers")
def list_customers():
    sort = request.args.get("sort", "last_order_desc")
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)

    rng = random.Random(99)
    customers = []
    for i in range(50):
        city = rng.choice(CUSTOMERS_DATA)
        orders_count = rng.randint(1, 12)
        customers.append(
            {
                "customer_id": f"C{10000 + i}",
                "name": CUSTOMER_NAMES[i % len(CUSTOMER_NAMES)],
                "city": city["city"],
                "total_orders": orders_count,
                "total_spent": orders_count * rng.randint(20000, 60000),
                "last_order_date": _date_n_days_ago(rng.randint(0, 30)),
                "level": rng.choice(["普通", "银牌", "金牌", "钻石"]),
            }
        )

    if sort == "total_spent_desc":
        customers.sort(key=lambda c: c["total_spent"], reverse=True)
    elif sort == "order_count_desc":
        customers.sort(key=lambda c: c["total_orders"], reverse=True)
    else:  # last_order_desc
        customers.sort(key=lambda c: c["last_order_date"], reverse=True)

    total = len(customers)
    start_idx = (page - 1) * page_size
    return jsonify(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "customers": customers[start_idx : start_idx + page_size],
        }
    )


# ---------------------------------------------------------------------------
# Analytics API
# ---------------------------------------------------------------------------

@app.route("/v1/analytics/revenue")
def analytics_revenue():
    period = request.args.get("period", "daily")

    today = _today()
    yesterday = _date_n_days_ago(1)

    today_orders = _gen_orders(today, today)
    yesterday_orders = _gen_orders(yesterday, yesterday)

    t_rev = sum(o["total_amount"] for o in today_orders)
    y_rev = sum(o["total_amount"] for o in yesterday_orders)

    t_avg = t_rev // max(len(today_orders), 1)
    y_avg = y_rev // max(len(yesterday_orders), 1)

    return jsonify(
        {
            "period": period,
            "current": {
                "date": today,
                "revenue": t_rev,
                "orders": len(today_orders),
                "avg_order_value": t_avg,
            },
            "previous": {
                "date": yesterday,
                "revenue": y_rev,
                "orders": len(yesterday_orders),
                "avg_order_value": y_avg,
            },
            "change": {
                "revenue_pct": round((t_rev - y_rev) / max(y_rev, 1) * 100, 1),
                "orders_pct": round(
                    (len(today_orders) - len(yesterday_orders))
                    / max(len(yesterday_orders), 1)
                    * 100,
                    1,
                ),
                "avg_order_value_pct": round((t_avg - y_avg) / max(y_avg, 1) * 100, 1),
            },
        }
    )


@app.route("/v1/analytics/trend")
def analytics_trend():
    days = int(request.args.get("days", 7))
    metric = request.args.get("metric", "revenue")

    points = []
    for i in range(days - 1, -1, -1):
        d = _date_n_days_ago(i)
        day_orders = _gen_orders(d, d)
        if metric == "revenue":
            value = sum(o["total_amount"] for o in day_orders)
        elif metric == "orders":
            value = len(day_orders)
        else:  # customers
            value = len(set(o["customer_name"] for o in day_orders))
        points.append({"date": d, "value": value})

    return jsonify({"metric": metric, "points": points})


@app.route("/v1/analytics/refunds")
def analytics_refunds():
    period = request.args.get("period", "week")
    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)

    start = _date_n_days_ago(period_days - 1)
    end = _today()
    orders = _gen_orders(start, end)
    total = len(orders)
    refunded = sum(1 for o in orders if o["status"] == "refunded")
    refund_amount = sum(
        o["total_amount"] for o in orders if o["status"] == "refunded"
    )

    return jsonify(
        {
            "period": period,
            "total_orders": total,
            "refunded_orders": refunded,
            "refund_rate": round(refunded / max(total, 1), 4),
            "refund_amount": refund_amount,
            "top_refund_reasons": [
                {"reason": "尺码不合适", "count": max(refunded * 40 // 100, 1)},
                {"reason": "质量问题", "count": max(refunded * 25 // 100, 1)},
                {"reason": "不想要了", "count": max(refunded * 20 // 100, 1)},
                {"reason": "发错货", "count": max(refunded * 15 // 100, 1)},
            ],
        }
    )


@app.route("/v1/analytics/category")
def analytics_category():
    rng = random.Random(77)
    categories = {}
    for p in PRODUCTS:
        if p["status"] != "on_sale":
            continue
        cat = p["category"]
        if cat not in categories:
            categories[cat] = {"sales_count": 0, "revenue": 0, "product_count": 0}
        sales = rng.randint(50, 200)
        categories[cat]["sales_count"] += sales
        categories[cat]["revenue"] += sales * p["price"]
        categories[cat]["product_count"] += 1

    total_rev = sum(c["revenue"] for c in categories.values())
    result = []
    for cat, data in categories.items():
        result.append(
            {
                "category": cat,
                "product_count": data["product_count"],
                "sales_count": data["sales_count"],
                "revenue": data["revenue"],
                "revenue_pct": round(data["revenue"] / max(total_rev, 1), 4),
            }
        )
    result.sort(key=lambda c: c["revenue"], reverse=True)
    return jsonify({"categories": result})


@app.route("/v1/analytics/avg-order-value")
def analytics_aov():
    days = int(request.args.get("days", 7))
    points = []
    for i in range(days - 1, -1, -1):
        d = _date_n_days_ago(i)
        day_orders = _gen_orders(d, d)
        total = sum(o["total_amount"] for o in day_orders)
        avg = total // max(len(day_orders), 1)
        points.append({"date": d, "avg_order_value": avg})

    return jsonify({"days": days, "points": points})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)