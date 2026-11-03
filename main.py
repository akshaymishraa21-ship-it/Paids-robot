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

conn = sqlite3.connect("market_stats.db", check_same_thread=False)
cursor = conn.cursor()

# Database Setup
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
    cursor.execute("SELECT chat_id, message_id FROM live_messages")
    messages = cursor.fetchall()
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
                cursor.execute(
                    "DELETE FROM live_messages WHERE chat_id=? AND message_id=?",
                    (chat_id, msg_id),
                )
                conn.commit()
        except Exception:
            pass


# --- GENERAL COMMANDS ---
@dp.message(Command("connection"))
async def cmd_connection(message: Message):
    if not is_admin(message.from_user): return
    cursor.execute("SELECT chat_id, title, username FROM connected_groups")
    groups = cursor.fetchall()
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
    if not is_admin(message.from_user): return
    await state.set_state(MakePost.waiting_for_paragraph)
    await message.reply("📝 **Broadcast Mode**\n\nSend the paragraph you want to broadcast.")

@dp.message(MakePost.waiting_for_paragraph)
async def process_make(message: Message, state: FSMContext):
    text = apply_premium_emojis(message.html_text or message.text)
    cursor.execute("SELECT chat_id FROM connected_groups")
    groups = cursor.fetchall()
    bot_info = await bot.get_me()
    success = 0
    for group in groups:
        chat_id = group[0]
        try:
            member = await bot.get_chat_member(chat_id, bot_info.id)
            if member.status == "administrator":
                await bot.send_message(chat_id, text, parse_mode="HTML")
                success += 1
        except Exception: 
            pass
    await state.clear()
    await message.reply(f"✅ Custom broadcast successfully sent to {success} admin groups!")

@dp.message(Command("getemoji"))
async def get_emoji_id(message: Message):
    if not is_admin(message.from_user): return
    entities = message.entities or []
    if message.reply_to_message and message.reply_to_message.entities:
        entities.extend(message.reply_to_message.entities)
    custom_emojis = [e.custom_emoji_id for e in entities if e.type == 'custom_emoji']
    if custom_emojis:
        res = "\n".join([f"`{eid}`" for eid in set(custom_emojis)])
        await message.reply(f"🔍 **Premium Emoji IDs found:**\n{res}", parse_mode="Markdown")
    else:
        await message.reply("⚠️ No premium emojis detected.")

@dp.message(Command("count"))
async def cmd_count(message: Message):
    if not is_admin(message.from_user):
        return
    if message.chat.type not in ["group", "supergroup"]:
        return await message.reply("⚠️ Use this command in a group.")
    cursor.execute("SELECT 1 FROM counted_groups WHERE chat_id=?", (message.chat.id,))
    if cursor.fetchone():
        cursor.execute("DELETE FROM counted_groups WHERE chat_id=?", (message.chat.id,))
        await message.reply("⏸️ Bot stopped counting messages in this group.")
    else:
        cursor.execute("INSERT INTO counted_groups (chat_id) VALUES (?)", (message.chat.id,))
        await message.reply("▶️ Bot is now officially counting messages and giving stars in this group!")
    conn.commit()


@dp.message(Command("live"))
async def cmd_live(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
    groups = cursor.fetchall()
    if not groups:
        return await message.reply("⚠️ The bot is not active anywhere. Use `/invest` to start.")
    active_count = len(groups)
    group_list = []
    for group in groups:
        chat_id = group[0]
        try:
            chat = await bot.get_chat(chat_id)
            group_name = f"@{chat.username}" if chat.username else f"{chat.title} (Private)"
            group_list.append(f"🔹 {group_name}")
        except Exception:
            group_list.append(f"🔹 Unknown Group (ID: {chat_id})")
    list_str = "\n".join(group_list)
    await message.reply(f"✅ **{active_count} group(s) active:**\n\n{list_str}", parse_mode="Markdown")


@dp.message(Command("resetall"))
async def reset_database(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("DELETE FROM spenders")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='spenders'")
    conn.commit()
    await update_live_messages()
    await message.reply("✅ All old stats and top members have been permanently deleted!")


@dp.message(Command("edit"))
async def edit_spenders(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("⚠️ Usage: /edit @username new_amount")
    username = parts[1].replace("@", "")
    try:
        new_amount = int(parts[2])
    except ValueError:
        return await message.reply("⚠️ Amount must be a number.")
    cursor.execute("DELETE FROM spenders WHERE LOWER(username)=?", (username.lower(),))
    if new_amount > 0:
        cursor.execute(
            "INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)",
            (0, username, new_amount, datetime.now()),
        )
        reply_msg = f"✅ Set @{username}'s total to {new_amount} Stars!"
    else:
        reply_msg = f"🗑 Removed @{username} from the leaderboard entirely."
    conn.commit()
    await update_live_messages()
    await message.reply(apply_premium_emojis(reply_msg), parse_mode="HTML")


@dp.message(Command("editgw"))
async def cmd_edit_giveaway(message: Message, state: FSMContext):
    if not is_admin(message.from_user): 
        return
    cursor.execute("SELECT id, description, status FROM giveaways WHERE status='active'")
    gws = cursor.fetchall()
    if not gws:
        return await message.reply("⚠️ No active giveaways found to edit.")
    res = "✏️ **Active Giveaways to Edit:**\n" + "\n".join([f"ID: `{g[0]}` | {g[1][:25]}..." for g in gws])
    res += "\n\n**Please send the ID of the giveaway you want to edit:**"
    await state.set_state(EditGiveaway.waiting_for_id)
    await message.reply(res, parse_mode="Markdown")

@dp.message(EditGiveaway.waiting_for_id)
async def process_edit_id(message: Message, state: FSMContext):
    try:
        gw_id = int(message.text.strip())
    except ValueError:
        return await message.reply("⚠️ Please send a valid numeric Giveaway ID.")
    cursor.execute("SELECT id FROM giveaways WHERE id=? AND status='active'", (gw_id,))
    if not cursor.fetchone():
        return await message.reply("⚠️ Active Giveaway ID not found.")
    await state.update_data(edit_gw_id=gw_id)
    await state.set_state(EditGiveaway.waiting_for_field)
    await message.reply(
        f"🛠 **Editing Giveaway #{gw_id}**\n\nWhat would you like to change?\n"
        "• Type `time` to extend/change duration\n"
        "• Type `desc` to change description\n"
        "• Type `winners` to change max winners count"
    )

@dp.message(EditGiveaway.waiting_for_field)
async def process_edit_field(message: Message, state: FSMContext):
    field = message.text.lower().strip()
    if field not in ["time", "desc", "winners"]:
        return await message.reply("⚠️ Invalid choice. Type `time`, `desc`, or `winners`.")
    await state.update_data(edit_field=field)
    await state.set_state(EditGiveaway.waiting_for_new_value)
    prompts = {
        "time": "⏳ Send the new duration (e.g., `3 hours`):",
        "desc": "📝 Send the new description text:",
        "winners": "🏆 Send the new maximum winners number:"
    }
    await message.reply(prompts[field])

@dp.message(EditGiveaway.waiting_for_new_value)
async def process_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    gw_id = data["edit_gw_id"]
    field = data["edit_field"]
    val = message.text.strip()
    if field == "time":
        try:
            amount = int("".join(filter(str.isdigit, val.lower())))
            delta = timedelta(minutes=amount) if "m" in val.lower() else timedelta(hours=amount)
            new_end = (datetime.now() + delta).isoformat()
            cursor.execute("UPDATE giveaways SET end_time=? WHERE id=?", (new_end, gw_id))
        except ValueError:
            return await message.reply("⚠️ Invalid duration format.")
    elif field == "desc":
        cursor.execute("UPDATE giveaways SET description=? WHERE id=?", (message.html_text or val, gw_id))
    elif field == "winners":
        try:
            new_wins = int(val)
            cursor.execute("UPDATE giveaways SET max_winners=? WHERE id=?", (new_wins, gw_id))
        except ValueError:
            return await message.reply("⚠️ Please send a valid number.")
    conn.commit()
    await state.clear()
    await update_live_post(gw_id)
    await message.reply(f"✅ Giveaway #{gw_id} successfully updated!")


@dp.message(Command("giveaways"))
async def cmd_manage_giveaways(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) >= 3 and parts[1].lower() == "end":
        try:
            target_id = int(parts[2])
        except ValueError:
            return await message.reply("⚠️ Invalid Giveaway ID format.")
        cursor.execute("SELECT id, status FROM giveaways WHERE id=?", (target_id,))
        gw = cursor.fetchone()
        if not gw:
return await message.reply(f"Giveaway #{target_id} not found.")
