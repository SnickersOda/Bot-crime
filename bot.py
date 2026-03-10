"""
CriminalCity RPG Bot — production-ready Telegram RPG
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("rpg_bot")

# ─── Router ────────────────────────────────────────────────────────────────────
router = Router()

# ─── States ────────────────────────────────────────────────────────────────────
class AdminStates(StatesGroup):
    waiting_give_money_uid   = State()
    waiting_give_money_amt   = State()
    waiting_take_money_uid   = State()
    waiting_take_money_amt   = State()
    waiting_give_item_uid    = State()
    waiting_give_item_name   = State()
    waiting_ban_uid          = State()
    waiting_unban_uid        = State()
    waiting_view_profile_uid = State()
    waiting_delete_gang_id   = State()


class GangStates(StatesGroup):
    waiting_gang_name   = State()
    waiting_gang_invite = State()


class PvpStates(StatesGroup):
    waiting_target = State()


class CasinoStates(StatesGroup):
    waiting_coinflip_bet    = State()
    waiting_dice_bet        = State()
    waiting_slots_bet       = State()
    waiting_roulette_bet    = State()
    waiting_roulette_choice = State()
    waiting_blackjack_bet   = State()


class BusinessStates(StatesGroup):
    waiting_buy_confirm = State()


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════
pool: asyncpg.Pool | None = None


async def init_db() -> None:
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      BIGINT PRIMARY KEY,
                username     TEXT,
                full_name    TEXT,
                cash         INTEGER NOT NULL DEFAULT 0,
                xp           INTEGER NOT NULL DEFAULT 0,
                level        INTEGER NOT NULL DEFAULT 1,
                wins         INTEGER NOT NULL DEFAULT 0,
                losses       INTEGER NOT NULL DEFAULT 0,
                robberies    INTEGER NOT NULL DEFAULT 0,
                season_xp    INTEGER NOT NULL DEFAULT 0,
                gang_id      INTEGER,
                in_jail_until BIGINT NOT NULL DEFAULT 0,
                is_banned    BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
            );

            CREATE TABLE IF NOT EXISTS inventory (
                id      SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                UNIQUE (user_id, item_id)
            );

            CREATE TABLE IF NOT EXISTS items (
                item_id     TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                price       INTEGER NOT NULL,
                pvp_bonus   INTEGER NOT NULL DEFAULT 0,
                def_bonus   INTEGER NOT NULL DEFAULT 0,
                rob_bonus   FLOAT NOT NULL DEFAULT 0.0,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS gangs (
                id         SERIAL PRIMARY KEY,
                name       TEXT UNIQUE NOT NULL,
                owner_id   BIGINT NOT NULL,
                bank       INTEGER NOT NULL DEFAULT 0,
                created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
            );

            CREATE TABLE IF NOT EXISTS gang_members (
                gang_id INTEGER NOT NULL REFERENCES gangs(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                role    TEXT NOT NULL DEFAULT 'member',
                PRIMARY KEY (gang_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id  BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                action   TEXT NOT NULL,
                expires  BIGINT NOT NULL,
                PRIMARY KEY (user_id, action)
            );

            CREATE TABLE IF NOT EXISTS bans (
                user_id    BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                reason     TEXT,
                banned_at  BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                banned_by  BIGINT
            );

            CREATE TABLE IF NOT EXISTS season (
                id         SERIAL PRIMARY KEY,
                started_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                number     INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS businesses (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                biz_id       TEXT NOT NULL,
                level        INTEGER NOT NULL DEFAULT 1,
                last_collect BIGINT NOT NULL DEFAULT 0,
                UNIQUE (user_id, biz_id)
            );

            CREATE TABLE IF NOT EXISTS casino_stats (
                user_id      BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                total_won    INTEGER NOT NULL DEFAULT 0,
                total_lost   INTEGER NOT NULL DEFAULT 0,
                games_played INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_businesses_uid ON businesses (user_id);
        """)

        # Seed items table
        for item_id, it in config.SHOP_ITEMS.items():
            await conn.execute("""
                INSERT INTO items (item_id, name, price, pvp_bonus, def_bonus, rob_bonus, description)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (item_id) DO UPDATE
                    SET name=$2, price=$3, pvp_bonus=$4, def_bonus=$5, rob_bonus=$6, description=$7
            """,
                item_id,
                it["name"],
                it["price"],
                it.get("pvp_bonus", 0),
                it.get("defense_bonus", 0),
                it.get("rob_bonus", 0.0),
                it.get("desc", ""),
            )

        # Ensure at least one season row
        season_exists = await conn.fetchval("SELECT COUNT(*) FROM season")
        if season_exists == 0:
            await conn.execute("INSERT INTO season (number) VALUES (1)")

    log.info("Database initialised ✅")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER UTILITIES
# ══════════════════════════════════════════════════════════════════════════════
def now_ts() -> int:
    return int(time.time())


def xp_for_level(level: int) -> int:
    return int(config.XP_BASE * (level ** config.XP_EXPONENT))


def level_from_xp(xp: int) -> int:
    level = 1
    while xp >= xp_for_level(level):
        xp -= xp_for_level(level)
        level += 1
    return level


def fmt_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}с"
    if seconds < 3600:
        return f"{seconds // 60}м {seconds % 60}с"
    return f"{seconds // 3600}ч {(seconds % 3600) // 60}м"


async def get_or_create_user(user_id: int, username: str, full_name: str) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
        if row is None:
            await conn.execute(
                """INSERT INTO users (user_id, username, full_name, cash)
                   VALUES ($1,$2,$3,$4)
                   ON CONFLICT DO NOTHING""",
                user_id, username, full_name, config.STARTING_CASH,
            )
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
        else:
            # Update name/username if changed
            await conn.execute(
                "UPDATE users SET username=$2, full_name=$3 WHERE user_id=$1",
                user_id, username, full_name,
            )
        return row


async def get_user(user_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)


async def update_user(user_id: int, **kwargs) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(kwargs))
    vals = list(kwargs.values())
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {sets} WHERE user_id=$1", user_id, *vals)


async def add_xp(user_id: int, amount: int) -> tuple[int, int, bool]:
    """Returns (new_xp, new_level, leveled_up)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT xp, level FROM users WHERE user_id=$1", user_id)
        xp = row["xp"] + amount
        old_level = row["level"]
        new_level = level_from_xp(xp)
        leveled_up = new_level > old_level
        await conn.execute(
            "UPDATE users SET xp=$2, level=$3, season_xp=season_xp+$4 WHERE user_id=$1",
            user_id, xp, new_level, amount,
        )
        return xp, new_level, leveled_up


async def get_cooldown(user_id: int, action: str) -> int:
    """Returns seconds remaining, or 0 if not on cooldown."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT expires FROM cooldowns WHERE user_id=$1 AND action=$2",
            user_id, action,
        )
        if row is None:
            return 0
        remaining = row["expires"] - now_ts()
        return max(0, remaining)


async def set_cooldown(user_id: int, action: str, duration: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO cooldowns (user_id, action, expires) VALUES ($1,$2,$3)
               ON CONFLICT (user_id, action) DO UPDATE SET expires=$3""",
            user_id, action, now_ts() + duration,
        )


async def get_inventory(user_id: int) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT item_id FROM inventory WHERE user_id=$1", user_id)
        return [r["item_id"] for r in rows]


async def get_item_bonuses(user_id: int) -> dict:
    inv = await get_inventory(user_id)
    pvp = 0
    defense = 0
    rob = 0.0
    for item_id in inv:
        it = config.SHOP_ITEMS.get(item_id, {})
        pvp += it.get("pvp_bonus", 0)
        defense += it.get("defense_bonus", 0)
        rob += it.get("rob_bonus", 0.0)
    return {"pvp": pvp, "defense": defense, "rob": rob}


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="👤 Профиль",          callback_data="profile"),
            InlineKeyboardButton(text="💰 Работать",          callback_data="work"),
        ],
        [
            InlineKeyboardButton(text="🔫 Ограбить игрока",   callback_data="rob_player"),
            InlineKeyboardButton(text="🏦 Ограбить банк",     callback_data="rob_bank"),
        ],
        [
            InlineKeyboardButton(text="⚔️ PvP бой",           callback_data="pvp"),
            InlineKeyboardButton(text="🗺 Районы",            callback_data="districts"),
        ],
        [
            InlineKeyboardButton(text="🎒 Инвентарь",         callback_data="inventory"),
            InlineKeyboardButton(text="🛒 Магазин",           callback_data="shop"),
        ],
        [
            InlineKeyboardButton(text="🏴 Банда",             callback_data="gang"),
            InlineKeyboardButton(text="🏆 Рейтинг",          callback_data="rating"),
        ],
        [
            InlineKeyboardButton(text="🎰 Казино",            callback_data="casino"),
            InlineKeyboardButton(text="🏢 Бизнесы",          callback_data="business"),
        ],
        [
            InlineKeyboardButton(text="🎁 Daily награда",     callback_data="daily"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])


def districts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏚 Бедный район",  callback_data="district_poor")],
        [InlineKeyboardButton(text="🏙 Центр города",  callback_data="district_center")],
        [InlineKeyboardButton(text="💎 Богатый район", callback_data="district_rich")],
        [InlineKeyboardButton(text="🏠 Главное меню",  callback_data="main_menu")],
    ])


def shop_kb() -> InlineKeyboardMarkup:
    rows = []
    for item_id, it in config.SHOP_ITEMS.items():
        rows.append([InlineKeyboardButton(
            text=f"{it['name']} — {it['price']}💵",
            callback_data=f"buy_{item_id}",
        )])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gang_kb(is_member: bool, is_owner: bool) -> InlineKeyboardMarkup:
    rows = []
    if not is_member:
        rows.append([InlineKeyboardButton(text="➕ Создать банду",    callback_data="gang_create")])
    else:
        rows.append([InlineKeyboardButton(text="📋 Мои бандиты",      callback_data="gang_members")])
        rows.append([InlineKeyboardButton(text="📨 Пригласить игрока", callback_data="gang_invite")])
        if is_owner:
            rows.append([InlineKeyboardButton(text="💸 Казна банды",   callback_data="gang_bank")])
        rows.append([InlineKeyboardButton(text="🚪 Покинуть банду",   callback_data="gang_leave")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Игроки",     callback_data="adm_players"),
            InlineKeyboardButton(text="💰 Экономика",  callback_data="adm_economy"),
        ],
        [
            InlineKeyboardButton(text="🎒 Предметы",   callback_data="adm_items"),
            InlineKeyboardButton(text="🏴 Банды",      callback_data="adm_gangs"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
            InlineKeyboardButton(text="🚫 Баны",       callback_data="adm_bans"),
        ],
        [
            InlineKeyboardButton(text="🔄 Сбросить сезон", callback_data="adm_reset_season"),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  GUARDS
# ══════════════════════════════════════════════════════════════════════════════
async def guard(message_or_cb, user_id: int) -> asyncpg.Record | None:
    """Returns user record or sends error and returns None."""
    user = await get_user(user_id)
    if user is None:
        return None
    if user["is_banned"]:
        text = "🚫 Вы заблокированы в игре."
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer(text, show_alert=True)
        else:
            await message_or_cb.answer(text)
        return None
    ts = now_ts()
    if user["in_jail_until"] > ts:
        remaining = user["in_jail_until"] - ts
        text = f"🚔 Вы в тюрьме! Освобождение через {fmt_time(remaining)}."
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer(text, show_alert=True)
        else:
            await message_or_cb.answer(text)
        return None
    return user


# ══════════════════════════════════════════════════════════════════════════════
#  /START
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.chat.type != "private":
        return
    user = message.from_user
    await get_or_create_user(user.id, user.username or "", user.full_name)
    await message.answer(
        "🌆 <b>Добро пожаловать в CriminalCity RPG!</b>\n\n"
        "Стань криминальным авторитетом — грабь, дерись, властвуй.\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "🌆 <b>CriminalCity RPG</b> — Главное меню",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "profile")
async def cb_profile(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name)
    inv = await get_inventory(uid)
    items_str = " ".join(config.SHOP_ITEMS[i]["name"] for i in inv) if inv else "пусто"

    # Gang info
    gang_name = "—"
    if user["gang_id"]:
        async with pool.acquire() as conn:
            gang = await conn.fetchrow("SELECT name FROM gangs WHERE id=$1", user["gang_id"])
            if gang:
                gang_name = gang["name"]

    lvl = user["level"]
    cur_xp = user["xp"]
    xp_needed = xp_for_level(lvl)

    jail_text = ""
    if user["in_jail_until"] > now_ts():
        jail_text = f"\n🚔 В тюрьме: {fmt_time(user['in_jail_until'] - now_ts())}"

    text = (
        f"👤 <b>{cb.from_user.full_name}</b>\n"
        f"─────────────────\n"
        f"⭐ Уровень: <b>{lvl}</b>\n"
        f"📊 XP: <b>{cur_xp}</b> / {xp_needed}\n"
        f"💵 Деньги: <b>{user['cash']}$</b>\n"
        f"─────────────────\n"
        f"⚔️ Победы: <b>{user['wins']}</b>  |  💀 Поражения: <b>{user['losses']}</b>\n"
        f"🔫 Ограблений: <b>{user['robberies']}</b>\n"
        f"🏴 Банда: <b>{gang_name}</b>\n"
        f"🎒 Инвентарь: {items_str}"
        f"{jail_text}"
    )
    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  WORK
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "work")
async def cb_work(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await guard(cb, uid)
    if not user:
        return

    cd = await get_cooldown(uid, "work")
    if cd > 0:
        await cb.answer(f"⏳ Работа доступна через {fmt_time(cd)}", show_alert=True)
        return

    bonus_level = 1 + (user["level"] - 1) * 0.05
    earned = int(random.randint(config.WORK_MIN, config.WORK_MAX) * bonus_level)
    xp, lvl, leveled = await add_xp(uid, config.WORK_XP)
    await update_user(uid, cash=user["cash"] + earned)
    await set_cooldown(uid, "work", config.WORK_COOLDOWN)

    level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
    await cb.message.edit_text(
        f"💼 Вы поработали и заработали <b>{earned}$</b>!\n"
        f"📊 +{config.WORK_XP} XP{level_up_text}\n\n"
        f"⏳ Следующая работа через {fmt_time(config.WORK_COOLDOWN)}",
        reply_markup=back_kb(),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  DISTRICTS ROBBERY
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "districts")
async def cb_districts(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "🗺 <b>Районы города</b>\n\nВыберите район для ограбления:",
        reply_markup=districts_kb(),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("district_"))
async def cb_district_rob(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    district_key = cb.data.split("_", 1)[1]
    district = config.DISTRICTS.get(district_key)
    if not district:
        await cb.answer("Неизвестный район", show_alert=True)
        return

    user = await guard(cb, uid)
    if not user:
        return

    cd = await get_cooldown(uid, f"district_{district_key}")
    if cd > 0:
        await cb.answer(f"⏳ Грабёж доступен через {fmt_time(cd)}", show_alert=True)
        return

    bonuses = await get_item_bonuses(uid)
    level_bonus = (user["level"] - 1) * 0.02
    success_chance = min(0.95, district["success_base"] + level_bonus + bonuses["rob"])

    if random.random() < success_chance:
        earned = random.randint(district["min"], district["max"])
        xp, lvl, leveled = await add_xp(uid, district["xp"])
        await update_user(uid, cash=user["cash"] + earned, robberies=user["robberies"] + 1)
        await set_cooldown(uid, f"district_{district_key}", config.ROB_COOLDOWN)

        level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
        text = (
            f"✅ <b>Ограбление в {district['name']} прошло успешно!</b>\n\n"
            f"💵 Добыча: <b>{earned}$</b>\n"
            f"📊 +{district['xp']} XP{level_up_text}"
        )
    else:
        jail_time = config.JAIL_DURATION
        await update_user(uid, in_jail_until=now_ts() + jail_time)
        await set_cooldown(uid, f"district_{district_key}", config.ROB_COOLDOWN)
        text = (
            f"❌ <b>Ограбление провалилось!</b>\n\n"
            f"🚔 Вы пойманы и отправлены в тюрьму на {fmt_time(jail_time)}!"
        )

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  ROB PLAYER
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "rob_player")
async def cb_rob_player_menu(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await guard(cb, uid)
    if not user:
        return

    cd = await get_cooldown(uid, "rob_player")
    if cd > 0:
        await cb.answer(f"⏳ Доступно через {fmt_time(cd)}", show_alert=True)
        return

    # Pick a random victim
    async with pool.acquire() as conn:
        victim = await conn.fetchrow(
            """SELECT user_id, username, full_name, cash FROM users
               WHERE user_id != $1 AND cash > 50 AND is_banned=FALSE
                 AND in_jail_until < $2
               ORDER BY RANDOM() LIMIT 1""",
            uid, now_ts(),
        )

    if not victim:
        await cb.answer("😔 Нет доступных жертв.", show_alert=True)
        return

    bonuses = await get_item_bonuses(uid)
    level_bonus = (user["level"] - 1) * 0.02
    success_chance = min(0.85, 0.45 + level_bonus + bonuses["rob"])

    if random.random() < success_chance:
        stolen = int(victim["cash"] * random.uniform(0.1, 0.3))
        stolen = max(50, min(stolen, victim["cash"]))
        xp, lvl, leveled = await add_xp(uid, config.ROB_XP)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET cash=cash-$1 WHERE user_id=$2", stolen, victim["user_id"])
            await conn.execute("UPDATE users SET cash=cash+$1, robberies=robberies+1 WHERE user_id=$2", stolen, uid)
        await set_cooldown(uid, "rob_player", config.ROB_COOLDOWN)

        level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
        text = (
            f"🔫 <b>Ограбление прошло успешно!</b>\n\n"
            f"Жертва: {victim['full_name']}\n"
            f"💵 Украдено: <b>{stolen}$</b>\n"
            f"📊 +{config.ROB_XP} XP{level_up_text}"
        )
    else:
        await update_user(uid, in_jail_until=now_ts() + config.JAIL_DURATION)
        await set_cooldown(uid, "rob_player", config.ROB_COOLDOWN)
        text = (
            f"❌ <b>Вас поймали при ограблении {victim['full_name']}!</b>\n\n"
            f"🚔 Тюрьма на {fmt_time(config.JAIL_DURATION)}!"
        )

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  BANK HEIST
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "rob_bank")
async def cb_rob_bank(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await guard(cb, uid)
    if not user:
        return

    cd = await get_cooldown(uid, "bank")
    if cd > 0:
        await cb.answer(f"⏳ Банк недоступен ещё {fmt_time(cd)}", show_alert=True)
        return

    bonuses = await get_item_bonuses(uid)
    level_bonus = (user["level"] - 1) * 0.025
    success_chance = min(0.75, 0.25 + level_bonus + bonuses["rob"])

    if random.random() < success_chance:
        earned = random.randint(config.BANK_MIN, config.BANK_MAX)
        xp, lvl, leveled = await add_xp(uid, config.BANK_XP)
        await update_user(uid, cash=user["cash"] + earned, robberies=user["robberies"] + 1)
        await set_cooldown(uid, "bank", config.BANK_COOLDOWN)

        level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
        text = (
            f"🏦 <b>Ограбление банка УСПЕШНО!</b>\n\n"
            f"💵 Добыча: <b>{earned}$</b>\n"
            f"📊 +{config.BANK_XP} XP{level_up_text}"
        )
    else:
        await update_user(uid, in_jail_until=now_ts() + config.JAIL_DURATION * 2)
        await set_cooldown(uid, "bank", config.BANK_COOLDOWN)
        text = (
            f"❌ <b>Ограбление банка провалилось!</b>\n\n"
            f"🚔 Тяжкое преступление — тюрьма на {fmt_time(config.JAIL_DURATION * 2)}!"
        )

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  PvP
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "pvp")
async def cb_pvp_menu(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.from_user.id
    user = await guard(cb, uid)
    if not user:
        return

    cd = await get_cooldown(uid, "pvp")
    if cd > 0:
        await cb.answer(f"⏳ PvP доступен через {fmt_time(cd)}", show_alert=True)
        return

    # Random opponent
    async with pool.acquire() as conn:
        opponent = await conn.fetchrow(
            """SELECT user_id, username, full_name, level FROM users
               WHERE user_id != $1 AND is_banned=FALSE AND in_jail_until < $2
               ORDER BY RANDOM() LIMIT 1""",
            uid, now_ts(),
        )

    if not opponent:
        await cb.answer("😔 Нет доступных противников.", show_alert=True)
        return

    my_bonuses = await get_item_bonuses(uid)
    opp_bonuses = await get_item_bonuses(opponent["user_id"])

    my_power = user["level"] * 10 + my_bonuses["pvp"] + random.randint(1, 20)
    opp_power = opponent["level"] * 10 + opp_bonuses["pvp"] - my_bonuses["defense"] + random.randint(1, 20)

    win = my_power > opp_power

    if win:
        steal = random.randint(50, 200)
        async with pool.acquire() as conn:
            opp_cash = await conn.fetchval("SELECT cash FROM users WHERE user_id=$1", opponent["user_id"])
        steal = min(steal, opp_cash)
        xp, lvl, leveled = await add_xp(uid, config.PVP_XP_WIN)
        await add_xp(opponent["user_id"], config.PVP_XP_LOSS)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET cash=cash-$1, losses=losses+1 WHERE user_id=$2", steal, opponent["user_id"])
            await conn.execute("UPDATE users SET cash=cash+$1, wins=wins+1 WHERE user_id=$2", steal, uid)
        await set_cooldown(uid, "pvp", config.PVP_COOLDOWN)

        level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
        text = (
            f"⚔️ <b>PvP БОЙ — ПОБЕДА!</b>\n\n"
            f"Противник: <b>{opponent['full_name']}</b> (ур. {opponent['level']})\n"
            f"💪 Ваша сила: {my_power} vs {opp_power}\n"
            f"💵 Трофеи: <b>{steal}$</b>\n"
            f"📊 +{config.PVP_XP_WIN} XP{level_up_text}"
        )
    else:
        lose_amount = random.randint(30, 100)
        async with pool.acquire() as conn:
            my_cash = await conn.fetchval("SELECT cash FROM users WHERE user_id=$1", uid)
        lose_amount = min(lose_amount, my_cash)
        xp, lvl, leveled = await add_xp(uid, config.PVP_XP_LOSS)
        await add_xp(opponent["user_id"], config.PVP_XP_WIN)
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET cash=cash-$1, losses=losses+1 WHERE user_id=$2", lose_amount, uid)
            await conn.execute("UPDATE users SET cash=cash+$1, wins=wins+1 WHERE user_id=$2", lose_amount, opponent["user_id"])
        await set_cooldown(uid, "pvp", config.PVP_COOLDOWN)

        text = (
            f"⚔️ <b>PvP БОЙ — ПОРАЖЕНИЕ!</b>\n\n"
            f"Противник: <b>{opponent['full_name']}</b> (ур. {opponent['level']})\n"
            f"💪 Ваша сила: {my_power} vs {opp_power}\n"
            f"💸 Потеряно: <b>{lose_amount}$</b>\n"
            f"📊 +{config.PVP_XP_LOSS} XP"
        )

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  INVENTORY
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "inventory")
async def cb_inventory(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    inv = await get_inventory(uid)

    if not inv:
        text = "🎒 <b>Инвентарь пуст</b>\n\nПосетите магазин, чтобы купить предметы."
    else:
        lines = ["🎒 <b>Ваш инвентарь:</b>\n"]
        for item_id in inv:
            it = config.SHOP_ITEMS.get(item_id, {})
            bonuses = []
            if it.get("pvp_bonus"):
                bonuses.append(f"⚔️+{it['pvp_bonus']}")
            if it.get("defense_bonus"):
                bonuses.append(f"🛡+{it['defense_bonus']}")
            if it.get("rob_bonus"):
                bonuses.append(f"🔫+{int(it['rob_bonus']*100)}%")
            bonus_str = "  ".join(bonuses)
            lines.append(f"• {it.get('name', item_id)} — {bonus_str}")
        text = "\n".join(lines)

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  SHOP
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "shop")
async def cb_shop(cb: CallbackQuery) -> None:
    lines = ["🛒 <b>Магазин</b>\n"]
    for item_id, it in config.SHOP_ITEMS.items():
        bonuses = []
        if it.get("pvp_bonus"):
            bonuses.append(f"⚔️+{it['pvp_bonus']}")
        if it.get("defense_bonus"):
            bonuses.append(f"🛡+{it['defense_bonus']}")
        if it.get("rob_bonus"):
            bonuses.append(f"🔫+{int(it['rob_bonus']*100)}%")
        bonus_str = "  ".join(bonuses)
        lines.append(f"• {it['name']} — <b>{it['price']}$</b>  {bonus_str}\n  <i>{it['desc']}</i>")
    text = "\n\n".join(lines)
    await cb.message.edit_text(text, reply_markup=shop_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data.startswith("buy_"))
async def cb_buy_item(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    item_id = cb.data.split("_", 1)[1]
    it = config.SHOP_ITEMS.get(item_id)
    if not it:
        await cb.answer("Предмет не найден.", show_alert=True)
        return

    user = await get_user(uid)
    if user is None:
        return
    if user["is_banned"]:
        await cb.answer("🚫 Вы заблокированы.", show_alert=True)
        return

    inv = await get_inventory(uid)
    if item_id in inv:
        await cb.answer("У вас уже есть этот предмет!", show_alert=True)
        return

    if user["cash"] < it["price"]:
        await cb.answer(f"💸 Недостаточно денег! Нужно {it['price']}$", show_alert=True)
        return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET cash=cash-$1 WHERE user_id=$2", it["price"], uid)
        await conn.execute("INSERT INTO inventory (user_id, item_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", uid, item_id)

    await cb.answer(f"✅ Куплено: {it['name']}!", show_alert=True)
    await cb_shop(cb)


# ══════════════════════════════════════════════════════════════════════════════
#  DAILY
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "daily")
async def cb_daily(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name)

    if user["is_banned"]:
        await cb.answer("🚫 Вы заблокированы.", show_alert=True)
        return

    cd = await get_cooldown(uid, "daily")
    if cd > 0:
        await cb.answer(f"🎁 Daily уже получен! Следующий через {fmt_time(cd)}", show_alert=True)
        return

    reward = random.randint(config.DAILY_REWARD_MIN, config.DAILY_REWARD_MAX)
    xp, lvl, leveled = await add_xp(uid, config.DAILY_XP)
    await update_user(uid, cash=user["cash"] + reward)
    await set_cooldown(uid, "daily", 86400)

    level_up_text = f"\n🎉 <b>Новый уровень {lvl}!</b>" if leveled else ""
    text = (
        f"🎁 <b>Ежедневная награда!</b>\n\n"
        f"💵 +<b>{reward}$</b>\n"
        f"📊 +{config.DAILY_XP} XP{level_up_text}\n\n"
        f"Возвращайтесь завтра!"
    )
    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  RATING
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "rating")
async def cb_rating(cb: CallbackQuery) -> None:
    async with pool.acquire() as conn:
        top = await conn.fetch(
            """SELECT full_name, level, cash, season_xp, wins
               FROM users WHERE is_banned=FALSE
               ORDER BY season_xp DESC LIMIT 10"""
        )

    lines = ["🏆 <b>Рейтинг игроков (сезон)</b>\n"]
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, row in enumerate(top):
        lines.append(
            f"{medals[i]} {row['full_name']} — ур.{row['level']} "
            f"| XP: {row['season_xp']} | 💵{row['cash']} | ⚔️{row['wins']}"
        )

    await cb.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  GANG
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "gang")
async def cb_gang(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name)

    is_member = bool(user["gang_id"])
    is_owner = False
    gang_info = ""

    if is_member:
        async with pool.acquire() as conn:
            gang = await conn.fetchrow("SELECT * FROM gangs WHERE id=$1", user["gang_id"])
            count = await conn.fetchval("SELECT COUNT(*) FROM gang_members WHERE gang_id=$1", user["gang_id"])
        if gang:
            is_owner = gang["owner_id"] == uid
            gang_info = (
                f"\n\n🏴 <b>{gang['name']}</b>\n"
                f"👥 Участников: {count}/{config.GANG_MAX_MEMBERS}\n"
                f"💰 Казна: {gang['bank']}$\n"
                f"{'👑 Вы — основатель' if is_owner else '🔰 Вы — участник'}"
            )

    text = f"🏴 <b>Банды</b>{gang_info}" if is_member else (
        f"🏴 <b>Банды</b>\n\nВы не состоите в банде.\n"
        f"Создать банду стоит <b>{config.GANG_CREATE_COST}$</b>."
    )
    await cb.message.edit_text(text, reply_markup=gang_kb(is_member, is_owner), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "gang_create")
async def cb_gang_create(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    if user and user["gang_id"]:
        await cb.answer("Вы уже в банде!", show_alert=True)
        return
    if user and user["cash"] < config.GANG_CREATE_COST:
        await cb.answer(f"Нужно {config.GANG_CREATE_COST}$", show_alert=True)
        return
    await state.set_state(GangStates.waiting_gang_name)
    await cb.message.edit_text(
        "🏴 Введите название вашей банды:",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.message(GangStates.waiting_gang_name)
async def msg_gang_name(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    name = message.text.strip()[:32]
    user = await get_user(uid)
    if not user or user["cash"] < config.GANG_CREATE_COST:
        await state.clear()
        await message.answer("Недостаточно средств.", reply_markup=back_kb())
        return

    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM gangs WHERE name=$1", name)
        if existing:
            await message.answer("⚠️ Такое название уже занято. Попробуйте другое:")
            return
        await conn.execute("UPDATE users SET cash=cash-$1 WHERE user_id=$2", config.GANG_CREATE_COST, uid)
        gang_id = await conn.fetchval(
            "INSERT INTO gangs (name, owner_id) VALUES ($1,$2) RETURNING id",
            name, uid,
        )
        await conn.execute("UPDATE users SET gang_id=$1 WHERE user_id=$2", gang_id, uid)
        await conn.execute("INSERT INTO gang_members (gang_id, user_id, role) VALUES ($1,$2,'owner')", gang_id, uid)

    await state.clear()
    await message.answer(
        f"✅ Банда <b>{name}</b> создана!\nПотрачено: {config.GANG_CREATE_COST}$",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


@router.callback_query(F.data == "gang_leave")
async def cb_gang_leave(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    if not user or not user["gang_id"]:
        await cb.answer("Вы не в банде.", show_alert=True)
        return

    async with pool.acquire() as conn:
        gang = await conn.fetchrow("SELECT * FROM gangs WHERE id=$1", user["gang_id"])
        if gang and gang["owner_id"] == uid:
            # Dissolve the gang
            await conn.execute("UPDATE users SET gang_id=NULL WHERE gang_id=$1", gang["id"])
            await conn.execute("DELETE FROM gangs WHERE id=$1", gang["id"])
            text = f"💥 Банда <b>{gang['name']}</b> распущена."
        else:
            await conn.execute("DELETE FROM gang_members WHERE gang_id=$1 AND user_id=$2", user["gang_id"], uid)
            await conn.execute("UPDATE users SET gang_id=NULL WHERE user_id=$1", uid)
            text = "🚪 Вы покинули банду."

    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "gang_members")
async def cb_gang_members(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    if not user or not user["gang_id"]:
        await cb.answer("Вы не в банде.", show_alert=True)
        return

    async with pool.acquire() as conn:
        members = await conn.fetch(
            """SELECT u.full_name, u.level, gm.role FROM gang_members gm
               JOIN users u ON u.user_id = gm.user_id
               WHERE gm.gang_id=$1 ORDER BY u.level DESC""",
            user["gang_id"],
        )

    lines = ["👥 <b>Участники банды:</b>\n"]
    for m in members:
        role_icon = "👑" if m["role"] == "owner" else "🔰"
        lines.append(f"{role_icon} {m['full_name']} — ур.{m['level']}")

    await cb.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "gang_invite")
async def cb_gang_invite(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GangStates.waiting_gang_invite)
    await cb.message.edit_text(
        "📨 Введите @username или ID игрока для приглашения в банду:",
        reply_markup=back_kb(),
    )
    await cb.answer()


@router.message(GangStates.waiting_gang_invite)
async def msg_gang_invite(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    if not user or not user["gang_id"]:
        await state.clear()
        return

    target_input = message.text.strip().lstrip("@")
    async with pool.acquire() as conn:
        if target_input.isdigit():
            target = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", int(target_input))
        else:
            target = await conn.fetchrow("SELECT * FROM users WHERE username=$1", target_input)

    if not target:
        await message.answer("❌ Игрок не найден.")
        return

    if target["gang_id"]:
        await message.answer("⚠️ Этот игрок уже состоит в банде.")
        await state.clear()
        return

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM gang_members WHERE gang_id=$1", user["gang_id"])
        if count >= config.GANG_MAX_MEMBERS:
            await message.answer("⚠️ Банда заполнена.")
            await state.clear()
            return

        gang = await conn.fetchrow("SELECT name FROM gangs WHERE id=$1", user["gang_id"])
        await conn.execute(
            "INSERT INTO gang_members (gang_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            user["gang_id"], target["user_id"],
        )
        await conn.execute("UPDATE users SET gang_id=$1 WHERE user_id=$2", user["gang_id"], target["user_id"])

    await state.clear()
    await message.answer(
        f"✅ <b>{target['full_name']}</b> добавлен в банду <b>{gang['name']}</b>!",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


@router.callback_query(F.data == "gang_bank")
async def cb_gang_bank(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    if not user or not user["gang_id"]:
        await cb.answer("Вы не в банде.", show_alert=True)
        return

    async with pool.acquire() as conn:
        gang = await conn.fetchrow("SELECT * FROM gangs WHERE id=$1", user["gang_id"])

    text = (
        f"💰 <b>Казна банды {gang['name']}</b>\n\n"
        f"Баланс: <b>{gang['bank']}$</b>"
    )
    await cb.message.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN — PANEL
# ══════════════════════════════════════════════════════════════════════════════
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚙️ <b>Административная панель</b>",
        reply_markup=admin_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        banned = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_banned=TRUE")
        gangs = await conn.fetchval("SELECT COUNT(*) FROM gangs")
        total_cash = await conn.fetchval("SELECT COALESCE(SUM(cash),0) FROM users")
        season_row = await conn.fetchrow("SELECT number, started_at FROM season ORDER BY id DESC LIMIT 1")

    season_num = season_row["number"] if season_row else 1
    started = datetime.fromtimestamp(season_row["started_at"], tz=timezone.utc).strftime("%d.%m.%Y") if season_row else "—"

    text = (
        f"📊 <b>Статистика сервера</b>\n\n"
        f"👥 Игроков: {total}\n"
        f"🚫 Заблокировано: {banned}\n"
        f"🏴 Банд: {gangs}\n"
        f"💵 Всего денег: {total_cash}$\n"
        f"🏆 Сезон: №{season_num} (с {started})"
    )
    await cb.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_economy")
async def cb_adm_economy(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать деньги",  callback_data="adm_give_money")],
        [InlineKeyboardButton(text="➖ Забрать деньги", callback_data="adm_take_money")],
        [InlineKeyboardButton(text="🔄 Сбросить экономику", callback_data="adm_reset_eco")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_back")],
    ])
    await cb.message.edit_text("💰 <b>Управление экономикой</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_back")
async def cb_adm_back(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    await cb.message.edit_text("⚙️ <b>Административная панель</b>", reply_markup=admin_menu_kb(), parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_give_money")
async def cb_adm_give_money_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_give_money_uid)
    await cb.message.edit_text("💰 Введите user_id игрока:")
    await cb.answer()


@router.message(AdminStates.waiting_give_money_uid)
async def adm_give_money_uid(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if not message.text.strip().lstrip("-").isdigit():
        await message.answer("❌ Неверный ID")
        return
    await state.update_data(target_uid=int(message.text.strip()))
    await state.set_state(AdminStates.waiting_give_money_amt)
    await message.answer("💵 Введите сумму:")


@router.message(AdminStates.waiting_give_money_amt)
async def adm_give_money_amt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data["target_uid"]
    if not message.text.strip().lstrip("-").isdigit():
        await message.answer("❌ Неверная сумма")
        return
    amount = int(message.text.strip())
    async with pool.acquire() as conn:
        result = await conn.execute("UPDATE users SET cash=cash+$1 WHERE user_id=$2", amount, uid)
    await state.clear()
    await message.answer(
        f"✅ Выдано <b>{amount}$</b> игроку {uid}" if "UPDATE 1" in result else "❌ Игрок не найден",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "adm_take_money")
async def cb_adm_take_money_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_take_money_uid)
    await cb.message.edit_text("💸 Введите user_id игрока:")
    await cb.answer()


@router.message(AdminStates.waiting_take_money_uid)
async def adm_take_money_uid(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(target_uid=int(message.text.strip()))
    await state.set_state(AdminStates.waiting_take_money_amt)
    await message.answer("💵 Введите сумму для изъятия:")


@router.message(AdminStates.waiting_take_money_amt)
async def adm_take_money_amt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data["target_uid"]
    amount = int(message.text.strip())
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET cash=GREATEST(cash-$1,0) WHERE user_id=$2", amount, uid)
    await state.clear()
    await message.answer(f"✅ Изъято до {amount}$ у игрока {uid}", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_items")
async def cb_adm_items(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Выдать предмет",  callback_data="adm_give_item")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_back")],
    ])
    await cb.message.edit_text("🎒 <b>Управление предметами</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_give_item")
async def cb_adm_give_item_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_give_item_uid)
    await cb.message.edit_text("🎁 Введите user_id игрока:")
    await cb.answer()


@router.message(AdminStates.waiting_give_item_uid)
async def adm_give_item_uid(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(target_uid=int(message.text.strip()))
    await state.set_state(AdminStates.waiting_give_item_name)
    item_list = ", ".join(config.SHOP_ITEMS.keys())
    await message.answer(f"📦 Введите item_id ({item_list}):")


@router.message(AdminStates.waiting_give_item_name)
async def adm_give_item_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data["target_uid"]
    item_id = message.text.strip().lower()
    if item_id not in config.SHOP_ITEMS:
        await message.answer("❌ Неверный item_id")
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO inventory (user_id, item_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            uid, item_id,
        )
    await state.clear()
    await message.answer(f"✅ Предмет {item_id} выдан игроку {uid}", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_bans")
async def cb_adm_bans(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Забанить",   callback_data="adm_ban")],
        [InlineKeyboardButton(text="✅ Разбанить",  callback_data="adm_unban")],
        [InlineKeyboardButton(text="◀️ Назад",      callback_data="adm_back")],
    ])
    await cb.message.edit_text("🚫 <b>Управление банами</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_ban")
async def cb_adm_ban_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_ban_uid)
    await cb.message.edit_text("🚫 Введите user_id для бана:")
    await cb.answer()


@router.message(AdminStates.waiting_ban_uid)
async def adm_ban_uid(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    uid = int(message.text.strip())
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_banned=TRUE WHERE user_id=$1", uid)
        await conn.execute(
            "INSERT INTO bans (user_id, banned_by) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            uid, message.from_user.id,
        )
    await state.clear()
    await message.answer(f"🚫 Игрок {uid} заблокирован.", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_unban")
async def cb_adm_unban_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_unban_uid)
    await cb.message.edit_text("✅ Введите user_id для разбана:")
    await cb.answer()


@router.message(AdminStates.waiting_unban_uid)
async def adm_unban_uid(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    uid = int(message.text.strip())
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET is_banned=FALSE WHERE user_id=$1", uid)
        await conn.execute("DELETE FROM bans WHERE user_id=$1", uid)
    await state.clear()
    await message.answer(f"✅ Игрок {uid} разблокирован.", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_players")
async def cb_adm_players(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Профиль игрока", callback_data="adm_view_profile")],
        [InlineKeyboardButton(text="◀️ Назад",          callback_data="adm_back")],
    ])
    await cb.message.edit_text("👥 <b>Управление игроками</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_view_profile")
async def cb_adm_view_profile_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_view_profile_uid)
    await cb.message.edit_text("🔍 Введите user_id игрока:")
    await cb.answer()


@router.message(AdminStates.waiting_view_profile_uid)
async def adm_view_profile(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    uid = int(message.text.strip())
    user = await get_user(uid)
    await state.clear()
    if not user:
        await message.answer("❌ Игрок не найден.", reply_markup=admin_menu_kb())
        return
    text = (
        f"👤 <b>Профиль #{uid}</b>\n"
        f"Имя: {user['full_name']}\n"
        f"Username: @{user['username']}\n"
        f"Уровень: {user['level']} | XP: {user['xp']}\n"
        f"Деньги: {user['cash']}$\n"
        f"Победы: {user['wins']} | Поражения: {user['losses']}\n"
        f"Ограблений: {user['robberies']}\n"
        f"Забанен: {'Да' if user['is_banned'] else 'Нет'}\n"
        f"Gang ID: {user['gang_id']}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_gangs")
async def cb_adm_gangs(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить банду", callback_data="adm_delete_gang")],
        [InlineKeyboardButton(text="◀️ Назад",         callback_data="adm_back")],
    ])
    async with pool.acquire() as conn:
        gangs = await conn.fetch("SELECT id, name FROM gangs ORDER BY id LIMIT 20")
    lines = ["🏴 <b>Все банды:</b>\n"] + [f"#{g['id']} — {g['name']}" for g in gangs]
    await cb.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.HTML)
    await cb.answer()


@router.callback_query(F.data == "adm_delete_gang")
async def cb_adm_delete_gang_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id):
        return
    await state.set_state(AdminStates.waiting_delete_gang_id)
    await cb.message.edit_text("🗑 Введите ID банды для удаления:")
    await cb.answer()


@router.message(AdminStates.waiting_delete_gang_id)
async def adm_delete_gang(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    gang_id = int(message.text.strip())
    async with pool.acquire() as conn:
        gang = await conn.fetchrow("SELECT name FROM gangs WHERE id=$1", gang_id)
        if not gang:
            await message.answer("❌ Банда не найдена.", reply_markup=admin_menu_kb())
            await state.clear()
            return
        await conn.execute("UPDATE users SET gang_id=NULL WHERE gang_id=$1", gang_id)
        await conn.execute("DELETE FROM gangs WHERE id=$1", gang_id)
    await state.clear()
    await message.answer(f"✅ Банда #{gang_id} ({gang['name']}) удалена.", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm_reset_eco")
async def cb_adm_reset_eco(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET cash={config.STARTING_CASH}")
    await cb.answer("✅ Экономика сброшена!", show_alert=True)


@router.callback_query(F.data == "adm_reset_season")
async def cb_adm_reset_season(cb: CallbackQuery) -> None:
    if not is_admin(cb.from_user.id):
        return
    async with pool.acquire() as conn:
        season_num = await conn.fetchval("SELECT COALESCE(MAX(number),0)+1 FROM season")
        await conn.execute("UPDATE users SET season_xp=0")
        await conn.execute("INSERT INTO season (number) VALUES ($1)", season_num)
    await cb.answer(f"✅ Сезон #{season_num} начат!", show_alert=True)
    await cb.message.edit_text(
        f"🔄 <b>Сезон #{season_num} начат!</b>\nРейтинг обнулён.",
        reply_markup=admin_menu_kb(),
        parse_mode=ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CASINO
# ══════════════════════════════════════════════════════════════════════════════
async def casino_update_stats(user_id: int, won: int, lost: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO casino_stats (user_id, total_won, total_lost, games_played)
            VALUES ($1, $2, $3, 1)
            ON CONFLICT (user_id) DO UPDATE
              SET total_won    = casino_stats.total_won    + $2,
                  total_lost   = casino_stats.total_lost   + $3,
                  games_played = casino_stats.games_played + 1
        """, user_id, won, lost)


def casino_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🪙 Монетка",   callback_data="casino_coinflip"),
            InlineKeyboardButton(text="🎲 Кости",     callback_data="casino_dice"),
        ],
        [
            InlineKeyboardButton(text="🎰 Слоты",     callback_data="casino_slots"),
            InlineKeyboardButton(text="🎡 Рулетка",   callback_data="casino_roulette"),
        ],
        [
            InlineKeyboardButton(text="🃏 Блэкджек",  callback_data="casino_blackjack"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="casino_stats"),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


@router.callback_query(F.data == "casino")
async def cb_casino(cb: CallbackQuery) -> None:
    user = await get_or_create_user(cb.from_user.id, cb.from_user.username or "", cb.from_user.full_name)
    if user["is_banned"]:
        await cb.answer("🚫 Вы заблокированы.", show_alert=True)
        return
    await cb.message.edit_text(
        "🎰 <b>Подпольное Казино</b>\n\n"
        "Испытай удачу! Выбери игру:\n\n"
        "🪙 <b>Монетка</b> — x2 при угадывании (50%)\n"
        "🎲 <b>Кости</b> — угадай больше/меньше 7 (x1.9)\n"
        "🎰 <b>Слоты</b> — три символа, джекпот x10!\n"
        "🎡 <b>Рулетка</b> — красное/чёрное/число\n"
        "🃏 <b>Блэкджек</b> — набери 21 без перебора",
        reply_markup=casino_kb(),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


# ── Coin Flip ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "casino_coinflip")
async def cb_casino_coinflip(cb: CallbackQuery, state: FSMContext) -> None:
    user = await guard(cb, cb.from_user.id)
    if not user:
        return
    await state.set_state(CasinoStates.waiting_coinflip_bet)
    await state.update_data(game="coinflip")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟡 Орёл",  callback_data="cf_heads"),
            InlineKeyboardButton(text="⚪ Решка", callback_data="cf_tails"),
        ],
        [InlineKeyboardButton(text="◀️ Казино", callback_data="casino")],
    ])
    await cb.message.edit_text(
        f"🪙 <b>Монетка</b>\n\nВаш баланс: <b>{user['cash']}$</b>\n\n"
        "Сначала выберите сторону монеты:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.in_({"cf_heads", "cf_tails"}))
async def cb_coinflip_side(cb: CallbackQuery, state: FSMContext) -> None:
    side = "heads" if cb.data == "cf_heads" else "tails"
    await state.update_data(cf_side=side)
    user = await get_user(cb.from_user.id)
    await cb.message.edit_text(
        f"🪙 Выбрано: {'🟡 Орёл' if side == 'heads' else '⚪ Решка'}\n\n"
        f"Баланс: <b>{user['cash']}$</b>\nВведите сумму ставки:",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.message(CasinoStates.waiting_coinflip_bet)
async def msg_coinflip_bet(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    data = await state.get_data()
    side = data.get("cf_side")
    if not side:
        await message.answer("Сначала выберите сторону монеты через кнопки выше.")
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число.")
        return
    bet = int(message.text.strip())
    if bet <= 0:
        await message.answer("❌ Ставка должна быть > 0.")
        return
    if bet > user["cash"]:
        await message.answer(f"💸 Недостаточно средств. У вас {user['cash']}$")
        return
    if bet > config.CASINO_MAX_BET:
        await message.answer(f"⛔ Максимальная ставка: {config.CASINO_MAX_BET}$")
        return

    result = random.choice(["heads", "tails"])
    result_emoji = "🟡 Орёл" if result == "heads" else "⚪ Решка"
    won = result == side

    if won:
        winnings = bet
        await update_user(uid, cash=user["cash"] + winnings)
        await casino_update_stats(uid, winnings, 0)
        text = (
            f"🪙 Монета выпала: <b>{result_emoji}</b>\n\n"
            f"✅ <b>Вы угадали!</b> +{winnings}$\n"
            f"💵 Баланс: {user['cash'] + winnings}$"
        )
    else:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        text = (
            f"🪙 Монета выпала: <b>{result_emoji}</b>\n\n"
            f"❌ <b>Не угадали!</b> -{bet}$\n"
            f"💵 Баланс: {user['cash'] - bet}$"
        )

    await state.clear()
    await message.answer(text, reply_markup=casino_kb(), parse_mode=ParseMode.HTML)


# ── Dice ───────────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "casino_dice")
async def cb_casino_dice(cb: CallbackQuery, state: FSMContext) -> None:
    user = await guard(cb, cb.from_user.id)
    if not user:
        return
    await state.set_state(CasinoStates.waiting_dice_bet)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬇️ Меньше 7",  callback_data="dice_low"),
            InlineKeyboardButton(text="🎯 Ровно 7",   callback_data="dice_seven"),
            InlineKeyboardButton(text="⬆️ Больше 7",  callback_data="dice_high"),
        ],
        [InlineKeyboardButton(text="◀️ Казино", callback_data="casino")],
    ])
    await cb.message.edit_text(
        f"🎲 <b>Кости</b> (2d6)\n\nБаланс: <b>{user['cash']}$</b>\n\n"
        "Выберите прогноз:\n"
        "⬇️ Меньше 7 → x1.9\n"
        "🎯 Ровно 7  → x4.5\n"
        "⬆️ Больше 7 → x1.9\n\n"
        "Нажмите прогноз, затем введите ставку:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.in_({"dice_low", "dice_seven", "dice_high"}))
async def cb_dice_choice(cb: CallbackQuery, state: FSMContext) -> None:
    choice_map = {"dice_low": "low", "dice_seven": "seven", "dice_high": "high"}
    await state.update_data(dice_choice=choice_map[cb.data])
    await cb.message.edit_text(
        f"🎲 Прогноз выбран. Введите сумму ставки:",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.message(CasinoStates.waiting_dice_bet)
async def msg_dice_bet(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    data = await state.get_data()
    choice = data.get("dice_choice")
    if not choice:
        await message.answer("Сначала выберите прогноз через кнопки.")
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число.")
        return
    bet = int(message.text.strip())
    if bet <= 0 or bet > user["cash"]:
        await message.answer(f"❌ Ставка от 1 до {user['cash']}$")
        return
    if bet > config.CASINO_MAX_BET:
        await message.answer(f"⛔ Максимум: {config.CASINO_MAX_BET}$")
        return

    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    total = d1 + d2
    multipliers = {"low": 1.9, "seven": 4.5, "high": 1.9}
    labels = {"low": "< 7", "seven": "= 7", "high": "> 7"}

    if (choice == "low" and total < 7) or (choice == "seven" and total == 7) or (choice == "high" and total > 7):
        winnings = int(bet * multipliers[choice])
        profit = winnings - bet
        await update_user(uid, cash=user["cash"] + profit)
        await casino_update_stats(uid, profit, 0)
        text = (
            f"🎲 Выпало: <b>{d1} + {d2} = {total}</b>\n\n"
            f"✅ Ваш прогноз ({labels[choice]}) верный!\n"
            f"💵 +{profit}$ (x{multipliers[choice]})\n"
            f"Баланс: {user['cash'] + profit}$"
        )
    else:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        text = (
            f"🎲 Выпало: <b>{d1} + {d2} = {total}</b>\n\n"
            f"❌ Прогноз ({labels[choice]}) неверный!\n"
            f"💸 -{bet}$\nБаланс: {user['cash'] - bet}$"
        )

    await state.clear()
    await message.answer(text, reply_markup=casino_kb(), parse_mode=ParseMode.HTML)


# ── Slots ──────────────────────────────────────────────────────────────────────
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎", "7️⃣"]
SLOT_WEIGHTS  = [30,   25,   20,   12,    7,    4,    2]
SLOT_PAYOUTS  = {      # multiplier on bet for 3-of-a-kind
    "🍒": 2,
    "🍋": 2.5,
    "🍊": 3,
    "🍇": 4,
    "⭐": 5,
    "💎": 8,
    "7️⃣": 10,
}


def spin_slots() -> list[str]:
    return random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)


@router.callback_query(F.data == "casino_slots")
async def cb_casino_slots(cb: CallbackQuery, state: FSMContext) -> None:
    user = await guard(cb, cb.from_user.id)
    if not user:
        return
    await state.set_state(CasinoStates.waiting_slots_bet)
    await cb.message.edit_text(
        f"🎰 <b>Слоты</b>\n\nБаланс: <b>{user['cash']}$</b>\n\n"
        "Выплаты (за 3 одинаковых):\n"
        "🍒🍒🍒 — x2\n🍋🍋🍋 — x2.5\n🍊🍊🍊 — x3\n"
        "🍇🍇🍇 — x4\n⭐⭐⭐ — x5\n💎💎💎 — x8\n7️⃣7️⃣7️⃣ — x10 🎉\n\n"
        "Введите ставку:",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.message(CasinoStates.waiting_slots_bet)
async def msg_slots_bet(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число.")
        return
    bet = int(message.text.strip())
    if bet <= 0 or bet > user["cash"]:
        await message.answer(f"❌ Ставка от 1 до {user['cash']}$")
        return
    if bet > config.CASINO_MAX_BET:
        await message.answer(f"⛔ Максимум: {config.CASINO_MAX_BET}$")
        return

    reels = spin_slots()
    display = " | ".join(reels)

    if reels[0] == reels[1] == reels[2]:
        mult = SLOT_PAYOUTS[reels[0]]
        winnings = int(bet * mult) - bet
        await update_user(uid, cash=user["cash"] + winnings)
        await casino_update_stats(uid, winnings, 0)
        jackpot = "🎉 <b>ДЖЕКПОТ!</b> " if reels[0] == "7️⃣" else ""
        text = (
            f"🎰  {display}\n\n"
            f"{jackpot}✅ Три одинаковых! x{mult}\n"
            f"💵 +{winnings}$\nБаланс: {user['cash'] + winnings}$"
        )
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        # Two of a kind — return bet
        text = (
            f"🎰  {display}\n\n"
            f"🔶 Два совпадения — ставка возвращена.\nБаланс: {user['cash']}$"
        )
        await casino_update_stats(uid, 0, 0)
    else:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        text = (
            f"🎰  {display}\n\n"
            f"❌ Нет совпадений. -{bet}$\nБаланс: {user['cash'] - bet}$"
        )

    await state.clear()
    await message.answer(text, reply_markup=casino_kb(), parse_mode=ParseMode.HTML)


# ── Roulette ───────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "casino_roulette")
async def cb_casino_roulette(cb: CallbackQuery, state: FSMContext) -> None:
    user = await guard(cb, cb.from_user.id)
    if not user:
        return
    await state.set_state(CasinoStates.waiting_roulette_bet)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Красное x2",  callback_data="rl_red"),
            InlineKeyboardButton(text="⚫ Чёрное x2",   callback_data="rl_black"),
        ],
        [
            InlineKeyboardButton(text="🟢 Зеро x14",    callback_data="rl_zero"),
            InlineKeyboardButton(text="🔢 Число x35",   callback_data="rl_number"),
        ],
        [InlineKeyboardButton(text="◀️ Казино", callback_data="casino")],
    ])
    await cb.message.edit_text(
        f"🎡 <b>Рулетка</b> (0–36)\n\nБаланс: <b>{user['cash']}$</b>\n\nВыберите тип ставки:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rl_"))
async def cb_roulette_type(cb: CallbackQuery, state: FSMContext) -> None:
    rtype = cb.data.split("_", 1)[1]
    await state.update_data(rl_type=rtype, rl_number=None)
    if rtype == "number":
        await cb.message.edit_text(
            "🔢 Введите число от 0 до 36, затем ставку через пробел.\nПример: <code>17 500</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await cb.message.edit_text("💵 Введите сумму ставки:")
    await cb.answer()


@router.message(CasinoStates.waiting_roulette_bet)
async def msg_roulette_bet(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    data = await state.get_data()
    rtype = data.get("rl_type")
    if not rtype:
        await message.answer("Сначала выберите тип ставки через кнопки.")
        return

    parts = message.text.strip().split()
    if rtype == "number":
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await message.answer("❌ Формат: <число 0-36> <ставка>")
            return
        chosen_num = int(parts[0])
        bet = int(parts[1])
        if chosen_num < 0 or chosen_num > 36:
            await message.answer("❌ Число от 0 до 36")
            return
    else:
        if not parts[0].isdigit():
            await message.answer("❌ Введите число.")
            return
        bet = int(parts[0])
        chosen_num = None

    if bet <= 0 or bet > user["cash"]:
        await message.answer(f"❌ Ставка от 1 до {user['cash']}$")
        return
    if bet > config.CASINO_MAX_BET:
        await message.answer(f"⛔ Максимум: {config.CASINO_MAX_BET}$")
        return

    spin = random.randint(0, 36)
    RED_NUMS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    spin_color = "🟢 Зеро" if spin == 0 else ("🔴 Красное" if spin in RED_NUMS else "⚫ Чёрное")

    win = False
    mult = 1
    if rtype == "red" and spin in RED_NUMS:
        win, mult = True, 2
    elif rtype == "black" and spin != 0 and spin not in RED_NUMS:
        win, mult = True, 2
    elif rtype == "zero" and spin == 0:
        win, mult = True, 14
    elif rtype == "number" and spin == chosen_num:
        win, mult = True, 35

    if win:
        profit = bet * (mult - 1)
        await update_user(uid, cash=user["cash"] + profit)
        await casino_update_stats(uid, profit, 0)
        text = (
            f"🎡 Выпало: <b>{spin}</b> {spin_color}\n\n"
            f"✅ Победа! x{mult}\n💵 +{profit}$\nБаланс: {user['cash'] + profit}$"
        )
    else:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        text = (
            f"🎡 Выпало: <b>{spin}</b> {spin_color}\n\n"
            f"❌ Проигрыш! -{bet}$\nБаланс: {user['cash'] - bet}$"
        )

    await state.clear()
    await message.answer(text, reply_markup=casino_kb(), parse_mode=ParseMode.HTML)


# ── Blackjack ──────────────────────────────────────────────────────────────────
CARD_VALUES = {
    "2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,
    "J":10,"Q":10,"K":10,"A":11,
}
CARD_SUITS = ["♠","♥","♦","♣"]


def new_deck() -> list[str]:
    deck = [f"{v}{s}" for v in CARD_VALUES for s in CARD_SUITS] * 2
    random.shuffle(deck)
    return deck


def hand_value(hand: list[str]) -> int:
    total = sum(CARD_VALUES[c[:-1]] for c in hand)
    aces = sum(1 for c in hand if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def hand_str(hand: list[str]) -> str:
    return " ".join(hand) + f"  ({hand_value(hand)})"


def bj_kb(finished: bool = False) -> InlineKeyboardMarkup:
    if finished:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🃏 Ещё раз",    callback_data="casino_blackjack")],
            [InlineKeyboardButton(text="◀️ Казино",     callback_data="casino")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Ещё карту", callback_data="bj_hit"),
            InlineKeyboardButton(text="✋ Хватит",    callback_data="bj_stand"),
        ],
        [InlineKeyboardButton(text="◀️ Казино", callback_data="casino")],
    ])


@router.callback_query(F.data == "casino_blackjack")
async def cb_casino_bj(cb: CallbackQuery, state: FSMContext) -> None:
    user = await guard(cb, cb.from_user.id)
    if not user:
        return
    await state.set_state(CasinoStates.waiting_blackjack_bet)
    await cb.message.edit_text(
        f"🃏 <b>Блэкджек</b>\n\nБаланс: <b>{user['cash']}$</b>\n\n"
        "Наберите 21 или больше дилера без перебора.\n"
        "Туз = 11 или 1, Картинки = 10\n\n"
        "Введите ставку:",
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.message(CasinoStates.waiting_blackjack_bet)
async def msg_bj_bet(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await get_user(uid)
    if not message.text.strip().isdigit():
        await message.answer("❌ Введите целое число.")
        return
    bet = int(message.text.strip())
    if bet <= 0 or bet > user["cash"]:
        await message.answer(f"❌ Ставка от 1 до {user['cash']}$")
        return
    if bet > config.CASINO_MAX_BET:
        await message.answer(f"⛔ Максимум: {config.CASINO_MAX_BET}$")
        return

    deck = new_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    await state.update_data(bj_player=player, bj_dealer=dealer, bj_deck=deck, bj_bet=bet)

    text = (
        f"🃏 <b>Блэкджек</b> — Ставка: {bet}$\n\n"
        f"Ваши карты: {hand_str(player)}\n"
        f"Дилер: {dealer[0]} 🂠\n\n"
        "Ваш ход:"
    )

    if hand_value(player) == 21:
        await _bj_finish(message, state, uid, user, player, dealer, deck, bet, blackjack=True)
        return

    await message.answer(text, reply_markup=bj_kb(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "bj_hit")
async def cb_bj_hit(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    data = await state.get_data()
    player = data["bj_player"]
    dealer = data["bj_dealer"]
    deck   = data["bj_deck"]
    bet    = data["bj_bet"]

    player.append(deck.pop())
    await state.update_data(bj_player=player, bj_deck=deck)

    pv = hand_value(player)
    if pv > 21:
        await cb.message.edit_text(
            f"🃏 Ваши карты: {hand_str(player)}\n\n💥 <b>Перебор!</b> -{bet}$",
            reply_markup=bj_kb(finished=True),
            parse_mode=ParseMode.HTML,
        )
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        await state.clear()
    elif pv == 21:
        await _bj_finish(cb.message, state, uid, user, player, dealer, deck, bet)
    else:
        await cb.message.edit_text(
            f"🃏 Ваши карты: {hand_str(player)}\nДилер: {dealer[0]} 🂠\n\nВаш ход:",
            reply_markup=bj_kb(),
            parse_mode=ParseMode.HTML,
        )
    await cb.answer()


@router.callback_query(F.data == "bj_stand")
async def cb_bj_stand(cb: CallbackQuery, state: FSMContext) -> None:
    uid = cb.from_user.id
    user = await get_user(uid)
    data = await state.get_data()
    await _bj_finish(cb.message, state, uid, user,
                     data["bj_player"], data["bj_dealer"], data["bj_deck"], data["bj_bet"])
    await cb.answer()


async def _bj_finish(msg, state, uid, user, player, dealer, deck, bet, blackjack=False):
    # Dealer draws to 17
    while hand_value(dealer) < 17:
        dealer.append(deck.pop())

    pv = hand_value(player)
    dv = hand_value(dealer)

    if blackjack:
        profit = int(bet * 1.5)
        await update_user(uid, cash=user["cash"] + profit)
        await casino_update_stats(uid, profit, 0)
        result = f"🃏 <b>БЛЭКДЖЕК!</b> +{profit}$ (x2.5)"
    elif pv > 21:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        result = f"💥 Перебор! -{bet}$"
    elif dv > 21 or pv > dv:
        await update_user(uid, cash=user["cash"] + bet)
        await casino_update_stats(uid, bet, 0)
        result = f"✅ Победа! +{bet}$"
    elif pv == dv:
        result = "🤝 Ничья! Ставка возвращена."
    else:
        await update_user(uid, cash=user["cash"] - bet)
        await casino_update_stats(uid, 0, bet)
        result = f"❌ Дилер победил! -{bet}$"

    text = (
        f"🃏 <b>Блэкджек — Итог</b>\n\n"
        f"Ваши карты: {hand_str(player)}\n"
        f"Дилер: {hand_str(dealer)}\n\n"
        f"{result}"
    )
    await state.clear()
    await msg.edit_text(text, reply_markup=bj_kb(finished=True), parse_mode=ParseMode.HTML)


# ── Casino Stats ────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "casino_stats")
async def cb_casino_stats(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM casino_stats WHERE user_id=$1", uid)
    if not row:
        await cb.answer("Вы ещё не играли в казино!", show_alert=True)
        return
    net = row["total_won"] - row["total_lost"]
    net_str = f"+{net}$" if net >= 0 else f"{net}$"
    await cb.message.edit_text(
        f"📊 <b>Ваша статистика казино</b>\n\n"
        f"🎮 Игр сыграно: {row['games_played']}\n"
        f"💰 Выиграно: {row['total_won']}$\n"
        f"💸 Проиграно: {row['total_lost']}$\n"
        f"📈 Итог: <b>{net_str}</b>",
        reply_markup=casino_kb(),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESSES
# ══════════════════════════════════════════════════════════════════════════════
def business_kb(owned: dict[str, int]) -> InlineKeyboardMarkup:
    rows = []
    for biz_id, biz in config.BUSINESSES.items():
        lvl = owned.get(biz_id, 0)
        if lvl == 0:
            label = f"{biz['icon']} {biz['name']} — купить {biz['buy_price']}$"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"biz_buy_{biz_id}")])
        else:
            max_lvl = len(biz["levels"])
            if lvl < max_lvl:
                upgrade_cost = biz["levels"][lvl]["upgrade_cost"]
                label = f"{biz['icon']} {biz['name']} ур.{lvl} — улучшить {upgrade_cost}$"
            else:
                label = f"{biz['icon']} {biz['name']} ур.{lvl} (макс)"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"biz_upgrade_{biz_id}")])
    rows.append([InlineKeyboardButton(text="💰 Собрать всё",   callback_data="biz_collect_all")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_owned_businesses(user_id: int) -> dict[str, dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT biz_id, level, last_collect FROM businesses WHERE user_id=$1", user_id)
    return {r["biz_id"]: {"level": r["level"], "last_collect": r["last_collect"]} for r in rows}


def calc_pending_income(biz_id: str, level: int, last_collect: int) -> int:
    biz = config.BUSINESSES[biz_id]
    lvl_data = biz["levels"][level - 1]
    elapsed = now_ts() - last_collect
    cycles = elapsed // biz["collect_interval"]
    return int(min(cycles, biz["max_cycles"]) * lvl_data["income"])


@router.callback_query(F.data == "business")
async def cb_business(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await get_or_create_user(uid, cb.from_user.username or "", cb.from_user.full_name)
    if user["is_banned"]:
        await cb.answer("🚫 Вы заблокированы.", show_alert=True)
        return

    owned = await get_owned_businesses(uid)
    lines = ["🏢 <b>Ваши бизнесы</b>\n"]

    total_pending = 0
    for biz_id, biz in config.BUSINESSES.items():
        if biz_id in owned:
            info = owned[biz_id]
            lvl = info["level"]
            lvl_data = biz["levels"][lvl - 1]
            pending = calc_pending_income(biz_id, lvl, info["last_collect"])
            total_pending += pending
            lines.append(
                f"{biz['icon']} <b>{biz['name']}</b> — ур.{lvl}\n"
                f"   💵 Доход: {lvl_data['income']}$ / {biz['collect_interval']//60}мин\n"
                f"   ⏳ Ожидает: <b>{pending}$</b>"
            )
        else:
            lines.append(
                f"{biz['icon']} {biz['name']} — не куплен ({biz['buy_price']}$)"
            )

    if total_pending:
        lines.append(f"\n💰 <b>К получению: {total_pending}$</b>")

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=business_kb({biz_id: info["level"] for biz_id, info in owned.items()}),
        parse_mode=ParseMode.HTML,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("biz_buy_"))
async def cb_biz_buy(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    biz_id = cb.data.split("biz_buy_", 1)[1]
    biz = config.BUSINESSES.get(biz_id)
    if not biz:
        await cb.answer("Неизвестный бизнес", show_alert=True)
        return

    user = await guard(cb, uid)
    if not user:
        return

    owned = await get_owned_businesses(uid)
    if biz_id in owned:
        await cb.answer("Уже куплен!", show_alert=True)
        return
    if user["cash"] < biz["buy_price"]:
        await cb.answer(f"💸 Нужно {biz['buy_price']}$", show_alert=True)
        return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET cash=cash-$1 WHERE user_id=$2", biz["buy_price"], uid)
        await conn.execute(
            "INSERT INTO businesses (user_id, biz_id, level, last_collect) VALUES ($1,$2,1,$3)",
            uid, biz_id, now_ts(),
        )

    await cb.answer(f"✅ {biz['icon']} {biz['name']} куплен!", show_alert=True)
    await cb_business(cb)


@router.callback_query(F.data.startswith("biz_upgrade_"))
async def cb_biz_upgrade(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    biz_id = cb.data.split("biz_upgrade_", 1)[1]
    biz = config.BUSINESSES.get(biz_id)
    if not biz:
        await cb.answer("Неизвестный бизнес", show_alert=True)
        return

    user = await guard(cb, uid)
    if not user:
        return

    owned = await get_owned_businesses(uid)
    if biz_id not in owned:
        await cb.answer("Сначала купите бизнес!", show_alert=True)
        return

    lvl = owned[biz_id]["level"]
    max_lvl = len(biz["levels"])
    if lvl >= max_lvl:
        await cb.answer("Максимальный уровень!", show_alert=True)
        return

    upgrade_cost = biz["levels"][lvl]["upgrade_cost"]
    if user["cash"] < upgrade_cost:
        await cb.answer(f"💸 Нужно {upgrade_cost}$", show_alert=True)
        return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET cash=cash-$1 WHERE user_id=$2", upgrade_cost, uid)
        await conn.execute(
            "UPDATE businesses SET level=level+1 WHERE user_id=$1 AND biz_id=$2",
            uid, biz_id,
        )

    await cb.answer(f"✅ {biz['icon']} {biz['name']} улучшен до ур.{lvl+1}!", show_alert=True)
    await cb_business(cb)


@router.callback_query(F.data == "biz_collect_all")
async def cb_biz_collect_all(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    user = await guard(cb, uid)
    if not user:
        return

    owned = await get_owned_businesses(uid)
    if not owned:
        await cb.answer("У вас нет бизнесов!", show_alert=True)
        return

    total = 0
    ts = now_ts()
    async with pool.acquire() as conn:
        for biz_id, info in owned.items():
            if biz_id not in config.BUSINESSES:
                continue
            pending = calc_pending_income(biz_id, info["level"], info["last_collect"])
            if pending > 0:
                total += pending
                await conn.execute(
                    "UPDATE businesses SET last_collect=$1 WHERE user_id=$2 AND biz_id=$3",
                    ts, uid, biz_id,
                )
        if total > 0:
            await conn.execute("UPDATE users SET cash=cash+$1 WHERE user_id=$2", total, uid)

    if total == 0:
        await cb.answer("⏳ Пока нечего собирать. Подождите!", show_alert=True)
    else:
        await cb.answer(f"💰 Собрано: {total}$!", show_alert=True)

    await cb_business(cb)


# ══════════════════════════════════════════════════════════════════════════════
#  SEASON BACKGROUND TASK
# ══════════════════════════════════════════════════════════════════════════════
async def season_watcher(bot: Bot) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            async with pool.acquire() as conn:
                season = await conn.fetchrow("SELECT * FROM season ORDER BY id DESC LIMIT 1")
                if not season:
                    continue
                elapsed = now_ts() - season["started_at"]
                if elapsed >= config.SEASON_DURATION_DAYS * 86400:
                    # Hand out top rewards
                    top = await conn.fetch(
                        "SELECT user_id, season_xp FROM users ORDER BY season_xp DESC LIMIT 3"
                    )
                    for place, row in enumerate(top, start=1):
                        reward = config.SEASON_TOP_REWARDS.get(place, 0)
                        if reward:
                            await conn.execute(
                                "UPDATE users SET cash=cash+$1 WHERE user_id=$2",
                                reward, row["user_id"],
                            )
                            try:
                                await bot.send_message(
                                    row["user_id"],
                                    f"🏆 Сезон завершён! Вы заняли <b>#{place}</b> место.\n"
                                    f"Награда: <b>{reward}$</b>!",
                                    parse_mode=ParseMode.HTML,
                                )
                            except Exception:
                                pass

                    # Reset
                    new_num = season["number"] + 1
                    await conn.execute("UPDATE users SET season_xp=0")
                    await conn.execute("INSERT INTO season (number) VALUES ($1)", new_num)
                    log.info(f"New season #{new_num} started automatically.")
        except Exception as e:
            log.error(f"Season watcher error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  GROUP EVENT BROADCAST
# ══════════════════════════════════════════════════════════════════════════════
async def broadcast_events(bot: Bot) -> None:
    """Sends random criminal world news to all groups the bot is in."""
    events = [
        "🔫 По городу прокатилась волна ограблений — бандиты не дремлют!",
        "🚓 Полиция усилила патрулирование Богатого района. Будьте осторожны!",
        "💣 Взрыв в центре города — кто-то делает дерзкие дела...",
        "🏦 Сразу два банка ограблены сегодня ночью. Жертвы подсчитывают убытки.",
        "⚔️ Война банд вспыхнула в Центре. Победитель получит всё!",
        "🎰 Подпольное казино открылось в трущобах. Удача улыбается смелым.",
        "🚔 Массовая облава: несколько крупных преступников за решёткой.",
        "💎 Ювелирный магазин ограблен среди бела дня — дерзость зашкаливает!",
    ]
    # Note: bot needs to be added to a group with write permissions.
    # This is a standalone demo background task — configure GROUP_CHAT_ID in .env for production.


# ══════════════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE WEB SERVER (для Render Web Service)
# ══════════════════════════════════════════════════════════════════════════════
from aiohttp import web as aiohttp_web


async def health(request):
    return aiohttp_web.Response(text="OK")


async def run_web_server() -> None:
    app = aiohttp_web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = aiohttp_web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"🌐 Health server started on port {port}")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
async def main() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set!")
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set!")

    await init_db()

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Start background tasks
    asyncio.create_task(season_watcher(bot))

    # Start keep-alive web server for Render
    asyncio.create_task(run_web_server())

    log.info("🤖 CriminalCity RPG Bot starting...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    import os
    asyncio.run(main())
