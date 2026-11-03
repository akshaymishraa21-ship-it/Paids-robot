import asyncio
import sqlite3
import logging
import os
import html
import random
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message,
    ErrorEvent,
    ReactionTypeEmoji,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from keep_alive import keep_alive

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "8488349023:AAGsfuqYahfdBCkn6E-i6uUXWOS2Mu-Aib4"
)
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "6146191046"))
STARS_PER_POST = 400
ADMIN_USERNAMES = ["disturbor"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

def get_db():
    conn = sqlite3.connect("market_stats.db", check_same_thread=False)
    return conn, conn.cursor()

# Initialize Database Tables
def init_db():
    conn, cursor = get_db()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS spenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            stars INTEGER,
            timestamp DATETIME
        );
        CREATE TABLE IF NOT EXISTS live_messages (
            chat_id INTEGER,
            message_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS counted_groups (
            chat_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS connected_groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            username TEXT
        );
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT,
            description TEXT,
            end_time DATETIME,
            max_participants INTEGER,
            max_winners INTEGER,
            button_text TEXT,
            status TEXT,
            message_data TEXT,
            created_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER,
            user_id INTEGER,
            username TEXT,
            tickets INTEGER DEFAULT 1,
            referrer_id INTEGER,
            UNIQUE(giveaway_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS user_balances (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0
        );
    """)
    try:
        cursor.execute("ALTER TABLE giveaways ADD COLUMN sponsor_channel TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE giveaways ADD COLUMN created_at DATETIME")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()


# FSM States
class MakePost(StatesGroup):
    waiting_for_paragraph = State()

class MakeGiveaway(StatesGroup):
    photo = State()
    desc = State()
    duration = State()
    participants = State()
    winners = State()
    button_name = State()
    button_color = State()
    sponsor = State()
    target_group = State()

class RepostGiveaway(StatesGroup):
    waiting_for_target = State()

class EditGiveaway(StatesGroup):
    waiting_for_id = State()
    waiting_for_field = State()
    waiting_for_new_value = State()

class TopupAdmin(StatesGroup):
    waiting_for_amount = State()

class WithdrawFlow(StatesGroup):
    waiting_for_wallet = State()

class ManageBalance(StatesGroup):
    waiting_for_target = State()
    waiting_for_new_balance = State()

class DiceGameFlow(StatesGroup):
    waiting_for_amount = State()


def is_admin(user):
    if not user:
        return False
    if user.id == OWNER_ID:
        return True
    if user.username and user.username.lower() in ADMIN_USERNAMES:
        return True
    return False

def apply_premium_emojis(text):
    emoji_replacements = {
        "🎉": "5461151367559141950",
        "✅": "5850633746583129853",
        "❗️": "5274099962655816924",
        "🔥": "5397751602956239123",
        "📢": "5197304993920616826",
        "⚠️": "5420323339723881652",
    }
    for standard_emoji, premium_id in emoji_replacements.items():
        premium_html = f'<tg-emoji emoji-id="{premium_id}">{standard_emoji}</tg-emoji>'
        text = text.replace(standard_emoji, premium_html)
    return text

def format_stars(amount):
    if amount >= 1000:
        if amount % 1000 == 0:
            return f"{amount // 1000}k"
        return f"{amount / 1000:.1f}k"
    return str(amount)

def generate_leaderboard_text():
    conn, cursor = get_db()
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 10"
    )
    all_time = cursor.fetchall()
    one_week_ago = datetime.now() - timedelta(days=7)
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders WHERE timestamp >= ? GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1",
        (one_week_ago,),
    )
    this_week = cursor.fetchone()
    conn.close()

    msg = "❗️Top spenders ⚠️\n@Paidsrobot to check your rank.📢\n\nAll time:\n"
    if not all_time:
        msg += "Nobody yet!\n"
    else:
        for index, row in enumerate(all_time, start=1):
            uname = html.escape(str(row[0]))
            stars = format_stars(row[1])
            emoji = (
                ' <tg-emoji emoji-id="5008457489528652800">⭐️</tg-emoji>🔥'
                if index == 1
                else ""
            )
            msg += f'{index}. @{uname} ({stars} Stars <tg-emoji emoji-id="5030538831225422917">⭐️</tg-emoji>){emoji}\n'

    msg += "\nThis week #1. "
    if this_week:
        msg += f"@{html.escape(str(this_week[0]))} ({format_stars(this_week[1])} Stars)\n\n"
    else:
        msg += "Nobody yet!\n\n"
    msg += "⚠ Every week the #1 spender gets a free Advertisement in @Joiwi ✅ ⚠️"
    return apply_premium_emojis(msg)


async def update_live_messages():
    conn, cursor = get_db()
    cursor.execute("SELECT chat_id, message_id FROM live_messages")
    messages = cursor.fetchall()
    conn.close()
    if not messages:
        return
    new_text = generate_leaderboard_text()
    for chat_id, msg_id in messages:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=new_text, parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if (
                "message to edit not found" in str(e).lower()
                or "message can't be edited" in str(e).lower()
            ):
                c_conn, c_cursor = get_db()
                c_cursor.execute(
                    "DELETE FROM live_messages WHERE chat_id=? AND message_id=?",
                    (chat_id, msg_id),
                )
                c_conn.commit()
                c_conn.close()
        except Exception:
            pass


# --- GENERAL COMMANDS ---
@dp.message(Command("connection"))
async def cmd_connection(message: Message):
    if not is_admin(message.from_user): return
    conn, cursor = get_db()
    cursor.execute("SELECT chat_id, title, username FROM connected_groups")
    groups = cursor.fetchall()
    conn.close()
    bot_info = await bot.get_me()
    
    admin_groups = []
    for g in groups:
        chat_id, title, username = g[0], g[1], g[2]
        try:
            member = await bot.get_chat_member(chat_id, bot_info.id)
            if member.status in ["administrator"]:
                name = f"@{username}" if username else (title or f"Group {chat_id}")
                admin_groups.append(f"{name} (ID: `{chat_id}`)")
        except Exception:
            pass
            
    if not admin_groups:
        return await message.reply("⚠️ Bot is not an admin in any connected groups yet.")
        
    res = "✅ **Connected & Admin Groups:**\n\n" + "\n".join([f"🔹 {name}" for name in admin_groups])
    await message.reply(res, parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cancel_fsm(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.reply("🚫 Operation cancelled.")

@dp.message(Command("make"))
async def cmd_make(message: Message, state: FSMContext):
    if not is_admin(message.from_
