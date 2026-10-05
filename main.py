import asyncio
import sqlite3
import logging
import os
import html
import random
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ErrorEvent, ReactionTypeEmoji
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from keep_alive import keep_alive

BOT_TOKEN = "8488349023:AAFVoToAqkdH0hkF3qYqhtJhPNnXMeD5L5c"
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "6146191046"))
STARS_PER_POST = int(os.environ.get("STARS_PER_POST", "1000")) 
ADMIN_USERNAMES = ["disturbor"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

conn = sqlite3.connect("market_stats.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS spenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        stars INTEGER,
        timestamp DATETIME
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS live_messages (
        chat_id INTEGER,
        message_id INTEGER
    )
""")
conn.commit()

class MakePost(StatesGroup):
    waiting_for_paragraph = State()

def is_admin(user):
    if not user: return False
    if user.id == OWNER_ID: return True
    if user.username and user.username.lower() in ADMIN_USERNAMES: return True
    return False

def apply_premium_emojis(text):
    emoji_replacements = {
        "🎉": "5461151367559141950",
        "✅": "5850633746583129853",
        "❗️": "5274099962655816924",
        "🔥": "5397751602956239123",
        "📢": "5197304993920616826",
        "⚠️": "5420323339723881652"
    }
    for standard_emoji, premium_id in emoji_replacements.items():
        premium_html = f'<tg-emoji emoji-id="{premium_id}">{standard_emoji}</tg-emoji>'
        text = text.replace(standard_emoji, premium_html)
    return text

def format_stars(amount):
    if amount >= 1000:
        if amount % 1000 == 0: return f"{amount // 1000}k"
        return f"{amount / 1000:.1f}k"
    return str(amount)

def generate_leaderboard_text():
    cursor.execute("SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 10")
    all_time = cursor.fetchall()
    
    one_week_ago = datetime.now() - timedelta(days=7)
    cursor.execute("SELECT username, SUM(stars) as total FROM spenders WHERE timestamp >= ? GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1", (one_week_ago,))
    this_week = cursor.fetchone()

    msg = "❗️Top spenders ⚠\n@Paidsrobot to check your rank.📢\n\nAll time:\n"
    if not all_time:
        msg += "Nobody yet!\n"
    else:
        for index, row in enumerate(all_time, start=1):
            uname = html.escape(str(row[0]))
            stars = format_stars(row[1])
            emoji = ' <tg-emoji emoji-id="5008457489528652800">⭐️</tg-emoji>🔥' if index == 1 else ""
            msg += f'{index}. @{uname} ({stars} Stars <tg-emoji emoji-id="5030538831225422917">⭐</tg-emoji>){emoji}\n'
        
    msg += "\nThis week #1. "
    if this_week:
        msg += f"@{html.escape(str(this_week[0]))} ({format_stars(this_week[1])} Stars)\n\n"
    else:
        msg += "Nobody yet!\n\n"
        
    msg += "⚠️ Every week the #1 spender gets a free Advertisement in @Joiwi ✅ ⚠️"
    return apply_premium_emojis(msg)

async def update_live_messages():
    cursor.execute("SELECT chat_id, message_id FROM live_messages")
    messages = cursor.fetchall()
    if not messages: return
    new_text = generate_leaderboard_text()
    
    for chat_id, msg_id in messages:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=new_text, parse_mode="HTML")
        except TelegramBadRequest as e:
            error_msg = str(e).lower()
            if "message to edit not found" in error_msg or "message can't be edited" in error_msg:
                cursor.execute("DELETE FROM live_messages WHERE chat_id=? AND message_id=?", (chat_id, msg_id))
                conn.commit()
        except Exception:
            pass

@dp.message(Command("resetall"))
async def reset_database(message: Message):
    if not is_admin(message.from_user): return
    cursor.execute("DELETE FROM spenders")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='spenders'")
    conn.commit()
    await update_live_messages()
    await message.reply("✅ All old stats and top members have been permanently deleted! Starting fresh from today.", parse_mode="HTML")

@dp.message(Command("edit"))
async def edit_spenders(message: Message):
    if not is_admin(message.from_user): return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("⚠️ Usage: /edit @username new_amount")
    username = parts[1].replace("@", "")
    try:
        new_amount = int(parts[2])
    except ValueError:
        return await message.reply("⚠️ The amount must be a number.")