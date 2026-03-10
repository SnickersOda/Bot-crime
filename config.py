import os
from dotenv import load_dotenv

load_dotenv()

# ─── Bot ───────────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

# ─── Game balance ──────────────────────────────────────────────────────────────
STARTING_CASH: int = 500

# Work
WORK_COOLDOWN: int = 300          # seconds
WORK_MIN: int = 50
WORK_MAX: int = 200
WORK_XP: int = 10

# Robbery cooldown
ROB_COOLDOWN: int = 600
ROB_XP: int = 25

# Bank heist
BANK_COOLDOWN: int = 3600
BANK_MIN: int = 500
BANK_MAX: int = 2000
BANK_XP: int = 50

# PvP
PVP_COOLDOWN: int = 300
PVP_XP_WIN: int = 40
PVP_XP_LOSS: int = 10

# Daily
DAILY_REWARD_MIN: int = 200
DAILY_REWARD_MAX: int = 600
DAILY_XP: int = 20

# Jail
JAIL_DURATION: int = 600          # seconds

# XP per level: level_xp = BASE * level^EXPONENT
XP_BASE: int = 100
XP_EXPONENT: float = 1.5

# ─── Districts ─────────────────────────────────────────────────────────────────
DISTRICTS: dict = {
    "poor": {
        "name": "🏚 Бедный район",
        "min": 100,
        "max": 300,
        "success_base": 0.75,
        "xp": 20,
    },
    "center": {
        "name": "🏙 Центр города",
        "min": 300,
        "max": 700,
        "success_base": 0.55,
        "xp": 35,
    },
    "rich": {
        "name": "💎 Богатый район",
        "min": 700,
        "max": 2000,
        "success_base": 0.35,
        "xp": 60,
    },
}

# ─── Items ─────────────────────────────────────────────────────────────────────
SHOP_ITEMS: dict = {
    "knife": {
        "name": "🔪 Нож",
        "price": 300,
        "pvp_bonus": 5,
        "rob_bonus": 0.05,
        "desc": "Небольшой бонус в PvP и ограблениях",
    },
    "pistol": {
        "name": "🔫 Пистолет",
        "price": 1200,
        "pvp_bonus": 20,
        "rob_bonus": 0.10,
        "desc": "Серьёзное оружие — бонус в PvP и ограблениях",
    },
    "armor": {
        "name": "🛡 Броня",
        "price": 800,
        "defense_bonus": 15,
        "desc": "Снижает урон в PvP",
    },
    "lockpick": {
        "name": "🗝 Отмычка",
        "price": 500,
        "rob_bonus": 0.15,
        "desc": "Повышает шанс успеха ограбления",
    },
}

# ─── Season ────────────────────────────────────────────────────────────────────
SEASON_DURATION_DAYS: int = 30
SEASON_TOP_REWARDS: dict = {
    1: 10000,
    2: 5000,
    3: 2500,
}

# ─── Gang ──────────────────────────────────────────────────────────────────────
GANG_CREATE_COST: int = 2000
GANG_MAX_MEMBERS: int = 20

# ─── Casino ────────────────────────────────────────────────────────────────────
CASINO_MAX_BET: int = 10_000       # maximum single bet

# ─── Businesses ────────────────────────────────────────────────────────────────
# collect_interval — seconds between income ticks
# max_cycles       — how many uncollected ticks can accumulate (caps earnings)
# levels           — list of dicts: income per tick, upgrade_cost to reach NEXT level
BUSINESSES: dict = {
    "street_food": {
        "icon": "🌮",
        "name": "Уличная еда",
        "buy_price": 500,
        "collect_interval": 300,        # every 5 min
        "max_cycles": 48,               # cap at 4h of income
        "levels": [
            {"income": 30,  "upgrade_cost": 1_000},   # lvl 1
            {"income": 60,  "upgrade_cost": 2_500},   # lvl 2
            {"income": 110, "upgrade_cost": 5_000},   # lvl 3
            {"income": 180, "upgrade_cost": 0},        # lvl 4 (max)
        ],
    },
    "taxi": {
        "icon": "🚕",
        "name": "Таксопарк",
        "buy_price": 2_000,
        "collect_interval": 600,        # every 10 min
        "max_cycles": 24,
        "levels": [
            {"income": 120, "upgrade_cost": 4_000},
            {"income": 230, "upgrade_cost": 9_000},
            {"income": 400, "upgrade_cost": 18_000},
            {"income": 650, "upgrade_cost": 0},
        ],
    },
    "nightclub": {
        "icon": "🎶",
        "name": "Ночной клуб",
        "buy_price": 7_500,
        "collect_interval": 1800,       # every 30 min
        "max_cycles": 16,
        "levels": [
            {"income": 600,  "upgrade_cost": 15_000},
            {"income": 1_100, "upgrade_cost": 30_000},
            {"income": 1_800, "upgrade_cost": 55_000},
            {"income": 2_800, "upgrade_cost": 0},
        ],
    },
    "casino_front": {
        "icon": "🎰",
        "name": "Подпольное казино",
        "buy_price": 25_000,
        "collect_interval": 3600,       # every 1h
        "max_cycles": 12,
        "levels": [
            {"income": 2_500,  "upgrade_cost": 50_000},
            {"income": 5_000,  "upgrade_cost": 100_000},
            {"income": 9_000,  "upgrade_cost": 200_000},
            {"income": 15_000, "upgrade_cost": 0},
        ],
    },
    "drug_lab": {
        "icon": "⚗️",
        "name": "Лаборатория",
        "buy_price": 50_000,
        "collect_interval": 7200,       # every 2h
        "max_cycles": 8,
        "levels": [
            {"income": 8_000,   "upgrade_cost": 100_000},
            {"income": 16_000,  "upgrade_cost": 200_000},
            {"income": 28_000,  "upgrade_cost": 400_000},
            {"income": 45_000,  "upgrade_cost": 0},
        ],
    },
}

# ─── Casino ────────────────────────────────────────────────────────────────────
CASINO_MIN_BET: int = 50
CASINO_MAX_BET: int = 50000

# Slots: символы (emoji, вес, множитель при 3 одинаковых)
SLOT_SYMBOLS: list[tuple] = [
    ("🍋", 30, 2),
    ("🍊", 25, 3),
    ("🍇", 20, 4),
    ("🔔", 12, 6),
    ("💎", 8,  10),
    ("7️⃣", 5,  20),
]

# Рулетка: cooldown (сек)
ROULETTE_COOLDOWN: int = 10
SLOTS_COOLDOWN: int = 10
BLACKJACK_COOLDOWN: int = 10
DICE_COOLDOWN: int = 10

# ─── Businesses ────────────────────────────────────────────────────────────────
# biz_id: { name, buy_price, base_income, collect_interval(сек), max_level,
#            upgrade_cost_mult (цена апгрейда = buy_price * mult * level),
#            income_per_level }
BUSINESSES: dict = {
    "kiosk": {
        "name":             "🏪 Киоск",
        "buy_price":        1000,
        "base_income":      80,
        "collect_interval": 3600,       # 1 час
        "max_level":        5,
        "upgrade_cost":     800,        # базовая цена апгрейда × level
        "income_per_level": 50,
        "desc":             "Маленький киоск. Скромный, но стабильный доход.",
    },
    "cafe": {
        "name":             "☕ Кафе",
        "buy_price":        4000,
        "base_income":      300,
        "collect_interval": 3600,
        "max_level":        5,
        "upgrade_cost":     2500,
        "income_per_level": 150,
        "desc":             "Уютное кафе в центре. Хороший поток клиентов.",
    },
    "garage": {
        "name":             "🔧 Автосервис",
        "buy_price":        10000,
        "base_income":      800,
        "collect_interval": 3600,
        "max_level":        5,
        "upgrade_cost":     6000,
        "income_per_level": 400,
        "desc":             "Нелегальный сервис. Чинит краденые авто за нал.",
    },
    "club": {
        "name":             "🎶 Ночной клуб",
        "buy_price":        30000,
        "base_income":      2000,
        "collect_interval": 3600,
        "max_level":        5,
        "upgrade_cost":     15000,
        "income_per_level": 1000,
        "desc":             "VIP-клуб. Огромные доходы, огромные риски.",
    },
}
