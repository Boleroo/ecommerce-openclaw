import random
from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request

from business import BusinessError, inventory, orders_between, products, refund_reasons, tenant
from models import Order
from seed_data import STATUSES

reads = Blueprint("reads", __name__)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _date_n_days_ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def query_integer(name, default, maximum=None):
    try:
        value = int(request.args.get(name, default))
    except (ValueError, TypeError):
        raise BusinessError("INVALID_ARGUMENT", f"{name} must be a positive integer")
    if value < 1 or (maximum is not None and value > maximum):
        raise BusinessError("INVALID_ARGUMENT", f"{name} is outside the permitted range")
    return value


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@reads.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Orders API
# ---------------------------------------------------------------------------

@reads.route("/v1/orders")
def list_orders():
    start = request.args.get("start_date", _today())
    end = request.args.get("end_date", _today())
    status = request.args.get("status")
    keyword = request.args.get("keyword")
    sort = request.args.get("sort", "created_desc")
    page = query_integer("page", 1)
    page_size = min(query_integer("page_size", 20), 100)

    orders = orders_between(start, end, status, keyword)

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


@reads.route("/v1/orders/<order_id>")
def get_order(order_id):
    order = g.db.get(Order, (g.tenant_id, order_id))
    if order is not None:
        return jsonify(order.to_dict())
    return jsonify({"code": 40401, "message": f"order {order_id} not found"}), 404


@reads.route("/v1/orders/stats")
def order_stats():
    start = request.args.get("start_date", _today())
    end = request.args.get("end_date", _today())
    orders = orders_between(start, end)

    by_status = {}
    for s in STATUSES:
        by_status[s] = sum(1 for o in orders if o["status"] == s)

    total_amount = sum(o["total_amount"] for o in orders)

    # Yesterday comparison
    yd = _date_n_days_ago(1)
    yesterday_orders = orders_between(yd, yd)
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

@reads.route("/v1/products")
def list_products():
    status = request.args.get("status", "all")
    category = request.args.get("category")
    keyword = request.args.get("keyword")
    page = query_integer("page", 1)
    page_size = min(query_integer("page_size", 20), 100)

    prods = products()
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


@reads.route("/v1/products/ranking")
def product_ranking():
    sort = request.args.get("sort", "sales_desc")
    period = request.args.get("period", "week")
    filt = request.args.get("filter", "all")
    limit = query_integer("limit", 10)

    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)
    rng = random.Random(42)

    ranking = []
    for p in products():
        if p["status"] != "on_sale":
            continue
        if filt == "new":
            listed = datetime.strptime(p["listed_at"], "%Y-%m-%d")
            if (datetime.now() - listed).days > 30:
                continue
        sales = rng.randint(20, 200) * period_days // 7
        inv = inventory().get(p["sku"], {})
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


@reads.route("/v1/products/<sku>/stats")
def product_stats(sku):
    prod = next((p for p in products() if p["sku"] == sku), None)
    if not prod:
        return jsonify({"code": 40402, "message": f"product {sku} not found"}), 404

    period = request.args.get("period", "week")
    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)
    rng = random.Random(hash(sku))
    sales = rng.randint(30, 180) * period_days // 7
    inv = inventory().get(sku, {})

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

@reads.route("/v1/inventory/summary")
def inventory_summary():
    total_skus = len(inventory())
    total_stock = sum(v["stock"] for v in inventory().values())
    low = sum(1 for v in inventory().values() if 0 < v["stock"] < v["threshold"])
    oos = sum(1 for v in inventory().values() if v["stock"] == 0)
    over = sum(1 for v in inventory().values() if v["stock"] > v["threshold"] * 5)

    return jsonify(
        {
            "total_skus": total_skus,
            "total_stock": total_stock,
            "low_stock_count": low,
            "out_of_stock_count": oos,
            "overstock_count": over,
        }
    )


@reads.route("/v1/inventory/alerts")
def inventory_alerts():
    alert_type = request.args.get("type", "low_stock")
    threshold_override = request.args.get("threshold", type=int)

    alerts = []
    for sku, inv in inventory().items():
        prod = next((p for p in products() if p["sku"] == sku), None)
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


@reads.route("/v1/inventory/<sku>")
def inventory_sku(sku):
    inv = inventory().get(sku)
    if not inv:
        return jsonify({"code": 40403, "message": f"sku {sku} not found"}), 404
    prod = next((p for p in products() if p["sku"] == sku), None)
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


@reads.route("/v1/inventory/restock-suggestions")
def restock_suggestions():
    suggestions = []
    for sku, inv in inventory().items():
        if inv["stock"] < inv["threshold"] and inv["daily_avg"] > 0:
            prod = next((p for p in products() if p["sku"] == sku), None)
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

@reads.route("/v1/customers/summary")
def customers_summary():
    return jsonify(tenant().customer_profile["summary"])


@reads.route("/v1/customers/retention")
def customers_retention():
    period = request.args.get("period", "month")
    return jsonify({"period": period, **tenant().customer_profile["retention"]})


@reads.route("/v1/customers/geo")
def customers_geo():
    return jsonify({"regions": tenant().customer_profile["regions"]})


@reads.route("/v1/customers")
def list_customers():
    sort = request.args.get("sort", "last_order_desc")
    page = query_integer("page", 1)
    page_size = min(query_integer("page_size", 20), 100)

    customers = list(tenant().customers)

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

@reads.route("/v1/analytics/revenue")
def analytics_revenue():
    period = request.args.get("period", "daily")

    today = _today()
    yesterday = _date_n_days_ago(1)

    today_orders = orders_between(today, today)
    yesterday_orders = orders_between(yesterday, yesterday)

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


@reads.route("/v1/analytics/trend")
def analytics_trend():
    days = query_integer("days", 7, maximum=366)
    metric = request.args.get("metric", "revenue")

    points = []
    for i in range(days - 1, -1, -1):
        d = _date_n_days_ago(i)
        day_orders = orders_between(d, d)
        if metric == "revenue":
            value = sum(o["total_amount"] for o in day_orders)
        elif metric == "orders":
            value = len(day_orders)
        else:  # customers
            value = len(set(o["customer_name"] for o in day_orders))
        points.append({"date": d, "value": value})

    return jsonify({"metric": metric, "points": points})


@reads.route("/v1/analytics/refunds")
def analytics_refunds():
    period = request.args.get("period", "week")
    period_days = {"today": 1, "week": 7, "month": 30}.get(period, 7)

    start = _date_n_days_ago(period_days - 1)
    end = _today()
    orders = orders_between(start, end)
    total = len(orders)
    refunded = sum(1 for o in orders if o["refunded_amount"] > 0)
    refund_amount = sum(
        o["refunded_amount"] for o in orders
    )

    return jsonify(
        {
            "period": period,
            "total_orders": total,
            "refunded_orders": refunded,
            "refund_rate": round(refunded / max(total, 1), 4),
            "refund_amount": refund_amount,
            "top_refund_reasons": refund_reasons([order["order_id"] for order in orders]),
        }
    )


@reads.route("/v1/analytics/category")
def analytics_category():
    rng = random.Random(77)
    categories = {}
    for p in products():
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


@reads.route("/v1/analytics/avg-order-value")
def analytics_aov():
    days = query_integer("days", 7, maximum=366)
    points = []
    for i in range(days - 1, -1, -1):
        d = _date_n_days_ago(i)
        day_orders = orders_between(d, d)
        total = sum(o["total_amount"] for o in day_orders)
        avg = total // max(len(day_orders), 1)
        points.append({"date": d, "avg_order_value": avg})

    return jsonify({"days": days, "points": points})
