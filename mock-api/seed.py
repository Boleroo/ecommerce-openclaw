import random
from datetime import date, timedelta

from sqlalchemy import text

from models import Order, Product, Refund, Tenant, User
from seed_data import CARRIERS, CUSTOMER_NAMES, CUSTOMERS_DATA, INVENTORY, PRODUCTS, STATUSES


def customer_fixture(today, second_tenant):
    regions = ([{"city": "苏州", "customers": 85, "revenue": 2300000}]
               if second_tenant else CUSTOMERS_DATA)
    names = ["林*青", "何*宁", "许*安"] if second_tenant else CUSTOMER_NAMES
    summary = {
        "total_customers": 85 if second_tenant else 3256,
        "new_customers_30d": 20 if second_tenant else 487,
        "repeat_rate": 0.3 if second_tenant else 0.42,
        "avg_order_value": 27000 if second_tenant else 35600,
        "top_cities": regions[:5],
    }
    retention = {
        "new_customers": summary["new_customers_30d"],
        "repeat_customers": 25 if second_tenant else 352,
        "repeat_rate": summary["repeat_rate"],
        "lost_customers": 5 if second_tenant else 89,
        "avg_orders_per_customer": 1.4 if second_tenant else 1.8,
        "cohort": [
            {"month": (today - timedelta(days=days)).isoformat()[:7],
             "acquired": acquired, "retained_pct": retained}
            for days, acquired, retained in (
                [(30, 20, 0.3), (60, 18, 0.28), (90, 15, 0.25)] if second_tenant
                else [(30, 520, 0.38), (60, 498, 0.35), (90, 465, 0.32)]
            )
        ],
    }
    rng = random.Random(199 if second_tenant else 99)
    customers = []
    for index in range(15 if second_tenant else 50):
        city = rng.choice(regions)
        orders_count = rng.randint(1, 12)
        customers.append({
            "customer_id": f"C{(20000 if second_tenant else 10000) + index}",
            "name": names[index % len(names)],
            "city": city["city"],
            "total_orders": orders_count,
            "total_spent": orders_count * rng.randint(20000, 60000),
            "last_order_date": (today - timedelta(days=rng.randint(0, 30))).isoformat(),
            "level": rng.choice(["普通", "银牌", "金牌", "钻石"]),
        })
    return {"regions": regions, "summary": summary, "retention": retention}, customers, names


def seed_database(app):
    today = date.today()
    with app.extensions["session_factory"].begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        for second_tenant, tenant_id, name, user_id in (
            (False, "merchant-12345", "张三运动鞋专营店", "user-a"),
            (True, "merchant-67890", "李四运动用品店", "user-b"),
        ):
            if session.get(Tenant, tenant_id) is not None:
                continue
            profile, customers, names = customer_fixture(today, second_tenant)
            session.add(Tenant(id=tenant_id, name=name, customer_profile=profile, customers=customers))
            session.flush()
            session.add(User(id=user_id, tenant_id=tenant_id, name=name + "操作员"))
            catalog = []
            for position, fixture in enumerate(PRODUCTS):
                product = dict(fixture)
                if second_tenant:
                    product.update(sku="B-" + fixture["sku"], name="李四 " + fixture["name"],
                                   price=fixture["price"] + 1000)
                stock = dict(INVENTORY[fixture["sku"]])
                if second_tenant:
                    stock["stock"] += 7
                session.add(Product(tenant_id=tenant_id, position=position, **product, **stock))
                catalog.append(product)
            session.flush()
            refunds = []
            for offset in range(app.config["SEED_DAYS"]):
                day = today - timedelta(days=offset)
                rng = random.Random(int(day.strftime("%Y%m%d")) + int(second_tenant))
                for index in range(1, rng.randint(35, 55) + 1):
                    product = rng.choice(catalog[:10])
                    quantity = rng.randint(1, 3)
                    status = rng.choices(STATUSES, weights=[8, 12, 35, 40, 4, 1], k=1)[0]
                    hour, minute = rng.randint(0, 23), rng.randint(0, 59)
                    order_id = f"{'B-' if second_tenant else ''}JD{day:%Y%m%d}{index:04d}"
                    amount = product["price"] * quantity
                    created_at = f"{day}T{hour:02d}:{minute:02d}:00+08:00"
                    order = Order(
                        tenant_id=tenant_id, order_id=order_id, created_at=created_at,
                        status=status, customer_name=rng.choice(names), total_amount=amount,
                        refunded_amount=amount if status == "refunded" else 0,
                        items=[{"sku": product["sku"], "name": product["name"],
                                "quantity": quantity, "price": product["price"]}],
                        logistics={"carrier": rng.choice(CARRIERS),
                                   "tracking_no": f"JD{rng.randint(10**9, 10**10 - 1)}"}
                        if status in ("shipped", "delivered") else None,
                    )
                    session.add(order)
                    if status == "refunded":
                        refunds.append(Refund(
                            id=f"seed-{tenant_id}-{order_id}", tenant_id=tenant_id,
                            order_id=order_id, amount=amount, reason="尺码不合适",
                            created_at=created_at,
                        ))
            session.flush()
            session.add_all(refunds)
