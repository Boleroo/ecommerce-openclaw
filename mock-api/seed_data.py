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
