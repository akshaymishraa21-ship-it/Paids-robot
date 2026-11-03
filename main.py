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
    "BOT_TOKEN", "8488349023:AAG75msvlzTzeFbgXe4_8SnHldKEycgfLq8"
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
    if not is_admin(message.from_user): return
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.reply("🚫 Operation cancelled. You exited the setup mode.")

@dp.message(Command("make"))
async def cmd_make(message: Message, state: FSMContext):
    if not is_admin(message.from_user): return
    await state.set_state(MakePost.waiting_for_paragraph)
    await message.reply("📝 **Broadcast Mode**\n\nSend the paragraph you want to broadcast. Standard emojis like 🎉, ✅, 🔥 will automatically convert to Premium ones.")

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
        await message.reply("⚠️ No premium emojis detected. (Make sure you reply to a message containing one, or send the emoji alongside the command).")

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
        cursor.execute(
            "INSERT INTO counted_groups (chat_id) VALUES (?)", (message.chat.id,)
        )
        await message.reply(
            "▶️ Bot is now officially counting messages and giving stars in this group!"
        )
    conn.commit()


@dp.message(Command("live"))
async def cmd_live(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
    groups = cursor.fetchall()
    if not groups:
        return await message.reply(
            "⚠️ The bot is not active anywhere. Use `/invest` to start."
        )

    active_count = len(groups)
    group_list = []
    for group in groups:
        chat_id = group[0]
        try:
            chat = await bot.get_chat(chat_id)
            group_name = (
                f"@{chat.username}" if chat.username else f"{chat.title} (Private)"
            )
            group_list.append(f"🔹 {group_name}")
        except Exception:
            group_list.append(f"🔹 Unknown Group (ID: {chat_id})")

    list_str = "\n".join(group_list)
    await message.reply(
        f"✅ **{active_count} group(s) active:**\n\n{list_str}", parse_mode="Markdown"
    )


@dp.message(Command("resetall"))
async def reset_database(message: Message):
    if not is_admin(message.from_user):
        return
    cursor.execute("DELETE FROM spenders")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='spenders'")
    conn.commit()
    await update_live_messages()
    await message.reply(
        "✅ All old stats and top members have been permanently deleted!"
    )


# --- ORIGINAL LEADERBOARD EDIT COMMAND ---
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


# --- INTERACTIVE GIVEAWAY EDIT COMMAND (/editgw) ---
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
        return await message.reply("⚠️ Active Giveaway ID not found. Try again or type /cancel.")
    
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
        return await message.reply("⚠️ Invalid choice. Please type `time`, `desc`, or `winners`.")
    
    await state.update_data(edit_field=field)
    await state.set_state(EditGiveaway.waiting_for_new_value)
    
    prompts = {
        "time": "⏳ Send the new duration (e.g., `3 hours` or `45 minutes`):",
        "desc": "📝 Send the new description text (HTML supported):",
        "winners": "🏆 Send the new maximum winners number (e.g., `3`):"
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
            return await message.reply("⚠️ Invalid duration format. Try again (e.g., `2 hours`).")
    elif field == "desc":
        cursor.execute("UPDATE giveaways SET description=? WHERE id=?", (message.html_text or val, gw_id))
    elif field == "winners":
        try:
            new_wins = int(val)
            cursor.execute("UPDATE giveaways SET max_winners=? WHERE id=?", (new_wins, gw_id))
        except ValueError:
            return await message.reply("⚠️ Please send a valid number for winners.")
            
    conn.commit()
    await state.clear()
    await update_live_post(gw_id)
    await message.reply(f"✅ Giveaway #{gw_id} successfully updated!")


# --- GIVEAWAYS MANAGEMENT COMMAND (/giveaways) ---
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
            return await message.reply(f"⚠️ Giveaway #{target_id} not found.")

        cursor.execute("DELETE FROM giveaways WHERE id=?", (target_id,))
        cursor.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (target_id,))
        
        cursor.execute("SELECT id FROM giveaways ORDER BY id ASC")
        remaining = cursor.fetchall()
        
        for new_idx, row in enumerate(remaining, start=1):
            old_id = row[0]
            if old_id != new_idx:
                cursor.execute("UPDATE giveaways SET id=? WHERE id=?", (new_idx, old_id))
                cursor.execute("UPDATE giveaway_entries SET giveaway_id=? WHERE giveaway_id=?", (new_idx, old_id))
        
        cursor.execute("SELECT MAX(id) FROM giveaways")
        max_id_res = cursor.fetchone()
        max_id = max_id_res[0] if max_id_res and max_id_res[0] else 0
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='giveaways'")
        cursor.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('giveaways', ?)", (max_id,))
        
        conn.commit()
        return await message.reply(f"🗑 Giveaway #{target_id} deleted successfully, and remaining IDs have been re-indexed!")

    cursor.execute("SELECT id, description, created_at, status FROM giveaways WHERE status='active' ORDER BY id ASC")
    gws = cursor.fetchall()
    
    if not gws:
        return await message.reply("📋 **Active Giveaways:**\n\nNo active giveaways running in the background.")

    text = f"📋 **Running Giveaways ({len(gws)} active):**\n\n"
    for g in gws:
        gw_id, desc, created_at, status = g[0], g[1], g[2], g[3]
        short_desc = (desc[:30] + '...') if len(desc) > 30 else desc
        created_str = created_at[:16].replace('T', ' ') if created_at else "Unknown"
        text += f"🆔 **ID: `{gw_id}`**\n📝 {short_desc}\n🕒 Created: `{created_str}`\n\n"

    text += "💡 To delete/end a giveaway, use:\n`/giveaways end <ID>`"
    await message.reply(text, parse_mode="Markdown")


# --- NEW WALLET, TIP, BALANCE, WITHDRAW & TOPUP COMMANDS ---

@dp.message(Command("tip"))
async def cmd_tip(message: Message):
    if is_admin(message.from_user):
        if not message.reply_to_message:
            return await message.reply("⚠️ Usage: Reply to a user's message with `/tip <amount>` (e.g. `/tip 100$`)")
        
        parts = message.text.split()
        if len(parts) < 2:
            return await message.reply("⚠️ Please specify the amount, e.g. `/tip 100$`")
        
        try:
            amount_val = float("".join(filter(lambda c: c.isdigit() or c == '.', parts[1])))
        except ValueError:
            return await message.reply("⚠️ Invalid amount format.")
            
        target_user = message.reply_to_message.from_user
        target_id = target_user.id
        target_uname = target_user.username or target_user.first_name
        
        cursor.execute("INSERT INTO user_balances (user_id, username, balance) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (target_id, target_uname, amount_val, amount_val))
        conn.commit()
        
        cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (target_id,))
        new_bal = cursor.fetchone()[0]
        
        await message.reply(f"✅ Successfully tipped **${amount_val:.2f}** to @{target_uname}!\nNew Balance: **${new_bal:.2f}**", parse_mode="Markdown")
    else:
        user_id = message.from_user.id
        cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        balance = row[0] if row else 0.0
        await message.reply(f"💰 Your Balance: **${balance:.2f}**", parse_mode="Markdown")


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0.0
    await message.reply(f"💳 Your Current Balance: **${balance:.2f}**", parse_mode="Markdown")


@dp.message(Command("withdraw"))
async def cmd_withdraw(message: Message):
    await message.reply("⚠️ You can only withdraw after **$200** balance!", parse_mode="Markdown")


@dp.message(Command("topup"))
async def cmd_topup(message: Message, state: FSMContext):
    if is_admin(message.from_user):
        await state.set_state(TopupAdmin.waiting_for_amount)
        await message.reply("💵 How much balance would you like to top up for yourself? (Send amount like `200` or `200$`):")
    else:
        await message.reply("💳 **Top Up Instructions:**\nPlease send the exact money within a few seconds to:\n`Disturborme.ton`\n\nOnce received, the admin will verify and credit your balance via `/tip`!", parse_mode="Markdown")

@dp.message(TopupAdmin.waiting_for_amount)
async def process_admin_topup(message: Message, state: FSMContext):
    if not is_admin(message.from_user):
        await state.clear()
        return
        
    val = message.text.strip()
    try:
        amount = float("".join(filter(lambda c: c.isdigit() or c == '.', val)))
    except ValueError:
        return await message.reply("⚠️ Please send a valid number.")
        
    admin_id = message.from_user.id
    admin_uname = message.from_user.username or message.from_user.first_name
    
    cursor.execute("INSERT INTO user_balances (user_id, username, balance) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?", (admin_id, admin_uname, amount, amount))
    conn.commit()
    
    cursor.execute("SELECT balance FROM user_balances WHERE user_id=?", (admin_id,))
    new_bal = cursor.fetchone()[0]
    
    await state.clear()
    await message.reply(f"✅ Your balance has been topped up by **${amount:.2f}**!\nYour New Balance: **${new_bal:.2f}**", parse_mode="Markdown")


@dp.message(Command("addstars"))
async def add_old_spenders(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("⚠️ Usage: /addstars @username amount")
    username = parts[1].replace("@", "")
    try:
        stars_to_add = int(parts[2])
    except ValueError:
        return await message.reply("⚠️ Amount must be a number.")

    cursor.execute(
        "INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)",
        (0, username, stars_to_add, datetime.now()),
    )
    conn.commit()
    await update_live_messages()
    await message.reply(
        apply_premium_emojis(f"✅ Added {stars_to_add} Stars to @{username}!"),
        parse_mode="HTML",
    )


@dp.message(Command("invest"))
@dp.channel_post(Command("invest"))
async def setup_live_leaderboard(message: Message):
    if not is_admin(message.from_user):
        return
    text = generate_leaderboard_text()
    sent_msg = await message.answer(text, parse_mode="HTML")
    cursor.execute(
        "INSERT INTO live_messages (chat_id, message_id) VALUES (?, ?)",
        (message.chat.id, sent_msg.message_id),
    )
    conn.commit()
    try:
        await message.delete()
    except Exception:
        pass


@dp.message(Command("announcewinner"))
async def cmd_announcewinner(message: Message):
    if not is_admin(message.from_user):
        return
    if await send_winner_announcement():
        await message.reply("✅ Winner announcement sent to all connected groups!")
    else:
        await message.reply("⚠️ No spender found this week or no groups connected yet.")


# --- GIVEAWAY CREATION FSM ---
@dp.message(Command("giveaway"))
async def start_giveaway(message: Message, state: FSMContext):
    if not is_admin(message.from_user):
        return
    await state.set_state(MakeGiveaway.photo)
    await message.reply(
        "🎁 **Giveaway Setup [1/9]**\n\nPlease send the **Photo** for the giveaway."
    )


@dp.message(MakeGiveaway.photo, lambda m: m.photo)
async def gw_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo_id=photo_id)
    await state.set_state(MakeGiveaway.desc)
    await message.reply(
        "📝 **Giveaway Setup [2/9]**\n\nNow, send the **Description** for the giveaway (You can use HTML tags or standard emojis)."
    )


@dp.message(MakeGiveaway.photo)
async def gw_photo_invalid(message: Message):
    await message.reply("⚠️ Please send an actual **photo/image** for the giveaway.")


@dp.message(MakeGiveaway.desc)
async def gw_desc(message: Message, state: FSMContext):
    await state.update_data(desc=message.html_text or message.text)
    await state.set_state(MakeGiveaway.duration)
    await message.reply(
        "⏳ **Giveaway Setup [3/9]**\n\nHow long until it ends?\n*(Example: `5 hours` or `30 minutes`)*"
    )


@dp.message(MakeGiveaway.duration)
async def gw_duration(message: Message, state: FSMContext):
    text = message.text.lower().strip()
    try:
        amount = int("".join(filter(str.isdigit, text)))
        if "m" in text:
            delta = timedelta(minutes=amount)
        else:
            delta = timedelta(hours=amount)

        end_time = datetime.now() + delta
        await state.update_data(end_time=end_time.isoformat())
        await state.set_state(MakeGiveaway.participants)
        await message.reply(
            "👥 **Giveaway Setup [4/9]**\n\nWhat is the **Max Participants** limit? (e.g., `5000`)"
        )
    except ValueError:
        return await message.reply(
            "⚠️ Please send a valid duration, like `5 hours` or `30 minutes`."
        )


@dp.message(MakeGiveaway.participants)
async def gw_parts(message: Message, state: FSMContext):
    try:
        await state.update_data(max_parts=int(message.text))
    except ValueError:
        return await message.reply("⚠️ Send a number.")
    await state.set_state(MakeGiveaway.winners)
    await message.reply(
        "🏆 **Giveaway Setup [5/9]**\n\nHow many **Winners** will there be? (e.g., `5`)"
    )


@dp.message(MakeGiveaway.winners)
async def gw_wins(message: Message, state: FSMContext):
    try:
        await state.update_data(max_wins=int(message.text))
    except ValueError:
        return await message.reply("⚠️ Send a number.")
    await state.set_state(MakeGiveaway.button_name)
    await message.reply(
        "🔘 **Giveaway Setup [6/9]**\n\nSend the **Button Name** (You can include premium emojis)."
    )


@dp.message(MakeGiveaway.button_name)
async def gw_btn_name(message: Message, state: FSMContext):
    await state.update_data(btn_name=message.text)
    await state.set_state(MakeGiveaway.button_color)
    await message.reply(
        "🎨 **Giveaway Setup [7/9]**\n\nChoose button color emoji indicator:\n`pink`, `white`, `blue`, `green`, `red`, `yellow`, `orange`"
    )


@dp.message(MakeGiveaway.button_color)
async def gw_btn_color(message: Message, state: FSMContext):
    color_map = {
        "pink": "🌸",
        "white": "⚪️",
        "blue": "🔵",
        "green": "🟢",
        "red": "🔴",
        "yellow": "🟡",
        "orange": "🟠",
    }
    chosen_color = color_map.get(message.text.lower(), "🔵")
    data = await state.get_data()
    btn_text = f"{chosen_color} {data['btn_name']}"
    await state.update_data(final_btn_text=btn_text)

    await state.set_state(MakeGiveaway.sponsor)
    await message.reply(
        "📣 **Giveaway Setup [8/9]**\n\nWhich channel must they join to enter? (Send the @username like `@kwamicat`, or type `none` if not required)."
    )


@dp.message(MakeGiveaway.sponsor)
async def gw_sponsor(message: Message, state: FSMContext):
    await state.update_data(sponsor_channel=message.text.strip())
    await state.set_state(MakeGiveaway.target_group)
    await message.reply(
        "🎯 **Giveaway Setup [9/9]**\n\nWhere should this giveaway be posted?\n• Type **`all`** to post to all connected groups.\n• Or type the specific group username (e.g. `@mygroup`) or Chat ID."
    )


@dp.message(MakeGiveaway.target_group)
async def gw_finalize(message: Message, state: FSMContext):
    target = message.text.strip()
    data = await state.get_data()
    sponsor_channel = data["sponsor_channel"]
    created_at = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO giveaways (photo_id, description, end_time, max_participants, max_winners, button_text, status, message_data, sponsor_channel, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?)
    """,
        (
            data["photo_id"],
            data["desc"],
            data["end_time"],
            data["max_parts"],
            data["max_wins"],
            data["final_btn_text"],
            sponsor_channel,
            created_at,
        ),
    )
    gw_id = cursor.lastrowid
    conn.commit()
    await state.clear()

    target_chats = []
    if target.lower() == "all":
        cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
        target_chats = [row[0] for row in cursor.fetchall()]
    else:
        try:
            target_chats = [int(target)]
        except ValueError:
            cursor.execute(
                "SELECT chat_id FROM connected_groups WHERE LOWER(username)=? OR LOWER(title) LIKE ?",
                (target.replace("@", "").lower(), f"%{target.lower()}%")
            )
            res = cursor.fetchone()
            if res:
                target_chats = [res[0]]

    if not target_chats:
        return await message.reply(f"⚠️ Giveaway #{gw_id} was saved in the database, but no matching group '{target}' was found active! Use `/connection` to check group usernames/IDs.")

    msg_data = {}
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=data["final_btn_text"], callback_data=f"enter_gw_{gw_id}"
                )
            ]
        ]
    )

    time_left = datetime.fromisoformat(data["end_time"]) - datetime.now()
    hours_left = max(0, int(time_left.total_seconds() / 3600))
    caption = f"🎁 **NEW GIVEAWAY** 🎁\n\n{data['desc']}\n\n⏳ Ends in: **{hours_left}h**\n👥 Entries: 0 / {data['max_parts']}\n🏆 Winners: {data['max_wins']}"

    if sponsor_channel.lower() != "none":
        caption += f"\n\n⚠️ **Required:** You must join {sponsor_channel} to enter!"

    for chat_id in target_chats:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=data["photo_id"],
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            msg_data[str(chat_id)] = sent.message_id
        except Exception:
            pass

    cursor.execute(
        "UPDATE giveaways SET message_data=? WHERE id=?", (json.dumps(msg_data), gw_id)
    )
    conn.commit()
    await message.reply(
        f"✅ Giveaway #{gw_id} successfully deployed to {len(target_chats)} group(s)!"
    )


# --- REPOST GIVEAWAY FSM ---
@dp.message(Command("repost"))
async def cmd_repost(message: Message, state: FSMContext):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("⚠️ Usage: `/repost <giveaway_id>`")

    try:
        gw_id = int(parts[1])
    except ValueError:
        return await message.reply("⚠️ Giveaway ID must be a number.")

    cursor.execute("SELECT id, status FROM giveaways WHERE id=?", (gw_id,))
    gw = cursor.fetchone()
    if not gw or gw[1] != "active":
        return await message.reply("⚠️ Giveaway ID not found or already ended.")

    await state.update_data(repost_gw_id=gw_id)
    await state.set_state(RepostGiveaway.waiting_for_target)
    await message.reply(
        f"🔄 **Reposting Giveaway #{gw_id}**\n\nWhere should this be reposted?\n• Type **`all`** to repost to all active groups.\n• Or type the specific group username (e.g. `@mygroup`) or Chat ID."
    )


@dp.message(RepostGiveaway.waiting_for_target)
async def process_repost_target(message: Message, state: FSMContext):
    target = message.text.strip()
    data = await state.get_data()
    gw_id = data["repost_gw_id"]
    await state.clear()

    cursor.execute(
        "SELECT photo_id, description, end_time, max_participants, max_winners, button_text, status, sponsor_channel FROM giveaways WHERE id=?",
        (gw_id,),
    )
    gw = cursor.fetchone()

    cursor.execute(
        "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (gw_id,)
    )
    entries_count = cursor.fetchone()[0]

    time_left = datetime.fromisoformat(gw[2]) - datetime.now()
    hours_left = max(0, int(time_left.total_seconds() / 3600))
    mins_left = max(0, int((time_left.total_seconds() % 3600) / 60))

    caption = f"🎁 **GIVEAWAY REPOST** 🎁\n\n{gw[1]}\n\n⏳ Ends in: **{hours_left}h {mins_left}m**\n👥 Entries: {entries_count} / {gw[3]}\n🏆 Winners: {gw[4]}"
    if gw[7] and gw[7].lower() != "none":
        caption += f"\n\n⚠️ **Required:** You must join {gw[7]} to enter!"

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=gw[5], callback_data=f"enter_gw_{gw_id}")]
        ]
    )

    target_chats = []
    if target.lower() == "all":
        cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
        target_chats = [row[0] for row in cursor.fetchall()]
    else:
        try:
            target_chats = [int(target)]
        except ValueError:
            cursor.execute(
                "SELECT chat_id FROM connected_groups WHERE LOWER(username)=? OR LOWER(title) LIKE ?",
                (target.replace("@", "").lower(), f"%{target.lower()}%")
            )
            res = cursor.fetchone()
            if res:
                target_chats = [res[0]]

    if not target_chats:
        return await message.reply(f"⚠️ No matching active group found for '{target}'. Use `/connection` to check group usernames/IDs.")

    cursor.execute("SELECT message_data FROM giveaways WHERE id=?", (gw_id,))
    existing_msg_data_raw = cursor.fetchone()
    msg_data = json.loads(existing_msg_data_raw[0]) if existing_msg_data_raw and existing_msg_data_raw[0] else {}

    for chat_id in target_chats:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=gw[0],
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
            msg_data[str(chat_id)] = sent.message_id
        except Exception:
            pass

    cursor.execute(
        "UPDATE giveaways SET message_data=? WHERE id=?", (json.dumps(msg_data), gw_id)
    )
    conn.commit()
    await message.reply(
        f"✅ Giveaway #{gw_id} successfully reposted to {len(target_chats)} group(s) with {entries_count} entries retained!"
    )


# --- REDIRECT GIVEAWAY ENTRY TO PM ---
@dp.callback_query(lambda c: c.data and c.data.startswith("enter_gw_"))
async def handle_gw_entry_redirect(callback_query: CallbackQuery):
    gw_id = int(callback_query.data.split("_")[2])
    bot_info = await bot.get_me()
    
    start_payload = f"https://t.me/{bot_info.username}?start=enter_{gw_id}"
    
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Click here to Enter", url=start_payload)],
            [InlineKeyboardButton(text=callback_query.message.reply_markup.inline_keyboard[0][0].text, callback_data=f"enter_gw_{gw_id}")]
        ]
    )
    
    try:
        await callback_query.message.edit_reply_markup(reply_markup=markup)
    except Exception:
        pass

    await callback_query.answer(
        "✨ Tap the button below to register your entry and get your invite link in PM!", 
        show_alert=True
    )


async def update_live_post(gw_id):
    cursor.execute(
        "SELECT photo_id, description, end_time, max_participants, max_winners, button_text, message_data, status, sponsor_channel FROM giveaways WHERE id=?",
        (gw_id,),
    )
    gw = cursor.fetchone()
    if not gw or gw[7] != "active":
        return

    cursor.execute(
        "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (gw_id,)
    )
    entries_count = cursor.fetchone()[0]

    time_left = datetime.fromisoformat(gw[2]) - datetime.now()
    fake_mins = max(0, int(time_left.total_seconds() / 60) + random.randint(-2, 2))
    hours_left = fake_mins // 60
    mins_left = fake_mins % 60

    caption = f"🎁 **NEW GIVEAWAY** 🎁\n\n{gw[1]}\n\n⏳ Ends in: **{hours_left}h {mins_left}m**\n👥 Entries: {entries_count} / {gw[3]}\n🏆 Winners: {gw[4]}"
    if gw[8] and gw[8].lower() != "none":
        caption += f"\n\n⚠️ **Required:** You must join {gw[8]} to enter!"

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=gw[5], callback_data=f"enter_gw_{gw_id}")]
        ]
    )
    msg_data = json.loads(gw[6])

    for chat_id, msg_id in msg_data.items():
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=caption,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception:
            pass


# --- UNIVERSAL /START HANDLER (PM Entry & Referrals) ---
@dp.message(Command("start"))
async def handle_start_referral(message: Message):
    args = message.text.split()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    if len(args) > 1:
        param = args[1]
        
        if param.startswith("enter_"):
            try:
                gw_id = int(param.split("_")[1])
            except ValueError:
                return await message.reply("⚠️ Invalid giveaway payload.")
            
            cursor.execute("SELECT status, sponsor_channel FROM giveaways WHERE id=?", (gw_id,))
            gw = cursor.fetchone()
            if not gw or gw[0] != "active":
                return await message.reply("⚠️ This giveaway has already ended!")

            sponsor_channel = gw[1]
            if sponsor_channel and sponsor_channel.lower() != "none":
                try:
                    member = await bot.get_chat_member(chat_id=sponsor_channel, user_id=user_id)
                    if member.status in ["left", "kicked", "restricted"]:
                        return await message.reply(f"⚠️ You must join {sponsor_channel} first to enter this giveaway!")
                except Exception:
                    return await message.reply(f"⚠️ You must join {sponsor_channel} first! (Bot lacks admin rights there).")

            bot_info = await bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{gw_id}_{user_id}"

            try:
                cursor.execute(
                    "INSERT INTO giveaway_entries (giveaway_id, user_id, username, tickets, referrer_id) VALUES (?, ?, ?, 1, NULL)",
                    (gw_id, user_id, username),
                )
                conn.commit()
                await message.reply(
                    f"✅ **Entry Recorded Successfully!**\n\n"
                    f"Share your unique invite link with friends to get extra tickets and boost your winning chances:\n`{ref_link}`\n\n"
                    f"Current Tickets: **1**",
                    parse_mode="Markdown"
                )
                await update_live_post(gw_id)
            except sqlite3.IntegrityError:
                cursor.execute("SELECT tickets FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (gw_id, user_id))
                t_row = cursor.fetchone()
                current_tickets = t_row[0] if t_row else 1
                await message.reply(
                    f"⚠️ You have already entered this giveaway!\n\nYour invite link:\n`{ref_link}`\n\nCurrent Tickets: **{current_tickets}**",
                    parse_mode="Markdown"
                )

        elif param.startswith("ref_"):
            parts = param.split("_")
            try:
                gw_id = int(parts[1])
                referrer_id = int(parts[2])
            except ValueError:
                return await message.reply("⚠️ Invalid referral link.")

            if user_id == referrer_id:
                return await message.reply("⚠️ You cannot refer yourself!")

            cursor.execute("SELECT sponsor_channel FROM giveaways WHERE id=?", (gw_id,))
            gw = cursor.fetchone()

            if gw and gw[0] and gw[0].lower() != "none":
                try:
                    member = await bot.get_chat_member(chat_id=gw[0], user_id=user_id)
                    if member.status in ["left", "kicked", "restricted"]:
                        return await message.reply(f"⚠️ You must join {gw[0]} first before using this referral link!")
                except Exception:
                    return await message.reply(f"⚠️ You must join {gw[0]} first!")

            try:
                cursor.execute(
                    "INSERT INTO giveaway_entries (giveaway_id, user_id, username, tickets, referrer_id) VALUES (?, ?, ?, 1, ?)",
                    (gw_id, user_id, username, referrer_id),
                )
                cursor.execute(
                    "UPDATE giveaway_entries SET tickets = tickets + 1 WHERE giveaway_id=? AND user_id=?",
                    (gw_id, referrer_id),
                )
                conn.commit()

                await message.reply("✅ You successfully entered the giveaway via a referral link!")
                try:
                    await bot.send_message(referrer_id, "🎉 Someone joined using your link! You earned +1 Ticket.")
                except Exception:
                    pass

                await update_live_post(gw_id)
            except sqlite3.IntegrityError:
                await message.reply("⚠️ You have already entered this giveaway!")
    else:
        await message.reply(
            "👋 Welcome! Use the buttons in the main groups to enter giveaways."
        )


@dp.message(Command("entry"))
async def cmd_entry(message: Message):
    if not is_admin(message.from_user):
        return
    args = message.text.split()
    if len(args) == 1:
        cursor.execute("SELECT id, description, status FROM giveaways")
        gws = cursor.fetchall()
        if not gws:
            return await message.reply("No giveaways found.")
        res = "📋 **Giveaways:**\n" + "\n".join(
            [f"ID: `{g[0]}` | {g[2].upper()} | {g[1][:20]}..." for g in gws]
        )
        return await message.reply(
            res + "\n\nUse `/entry <ID>` for details.", parse_mode="Markdown"
        )

    gw_id = int(args[1])
    cursor.execute(
        "SELECT COUNT(*), SUM(tickets) FROM giveaway_entries WHERE giveaway_id=?",
        (gw_id,),
    )
    stats = cursor.fetchone()
    cursor.execute(
        "SELECT username, tickets FROM giveaway_entries WHERE giveaway_id=? ORDER BY tickets DESC LIMIT 5",
        (gw_id,),
    )
    top_refs = cursor.fetchall()

    txt = f"📊 **Giveaway #{gw_id} Stats:**\n\nTotal People: {stats[0]}\nTotal Tickets: {stats[1] or 0}\n\n**Top Referrers:**\n"
    for u, t in top_refs:
        txt += f"@{u} - {t} Tickets\n"
    await message.reply(txt)


# --- AUTO-WINNER SELECTION & OWNER NOTIFICATION ---
async def auto_select_and_notify_winners(gw_id: int):
    cursor.execute("SELECT user_id, username, tickets FROM giveaway_entries WHERE giveaway_id=?", (gw_id,))
    entries = cursor.fetchall()
    
    if not entries:
        try:
            await bot.send_message(OWNER_ID, f"⚠️ Giveaway #{gw_id} ended, but there were no participants.")
        except Exception:
            pass
        return

    cursor.execute("SELECT max_winners FROM giveaways WHERE id=?", (gw_id,))
    res = cursor.fetchone()
    max_winners = res[0] if res else 1

    pool = []
    for user_id, username, tickets in entries:
        for _ in range(max(1, tickets)):
            pool.append((user_id, username))
            
    random.shuffle(pool)
    
    chosen_winners = []
    seen_users = set()
    for uid, uname in pool:
        if uid not in seen_users:
            seen_users.add(uid)
            chosen_winners.append(f"@{uname}" if uname else f"User {uid}")
            if len(chosen_winners) >= max_winners:
                break

    winners_str = " ".join(chosen_winners)
    
    cursor.execute("UPDATE giveaways SET status='ended' WHERE id=?", (gw_id,))
    conn.commit()

    notification_text = (
        f"🤖 **Auto-Winner Selection for Giveaway #{gw_id}**\n\n"
        f"Selected Winners:\n`{winners_str}`\n\n"
        f"To announce this in groups, use:\n`/winner {gw_id} {winners_str}`"
    )
    try:
        await bot.send_message(OWNER_ID, notification_text, parse_mode="Markdown")
    except Exception:
        pass


@dp.message(Command("winner"))
async def cmd_winner(message: Message):
    if not is_admin(message.from_user):
        return
    parts = message.text.split()
    if len(parts) < 3:
        return await message.reply(
            "⚠️ Usage: `/winner <giveaway_id> @username1 @username2...`",
            parse_mode="Markdown",
        )

    gw_id = int(parts[1])
    winners_list = " ".join(parts[2:])

    cursor.execute("UPDATE giveaways SET status='ended' WHERE id=?", (gw_id,))
    conn.commit()

    cursor.execute(
        "SELECT photo_id, description, message_data FROM giveaways WHERE id=?", (gw_id,),
    )
    gw = cursor.fetchone()
    if not gw:
        return await message.reply("⚠️ Giveaway ID not found.")

    caption = f"🏁 **GIVEAWAY ENDED** 🏁\n\n{gw[1]}\n\n🎉 **WINNERS:**\n{winners_list}\n\nCongratulations! DM the owner to claim your prize."
    msg_data = json.loads(gw[2])

    success = 0
    for chat_id, msg_id in msg_data.items():
        try:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=msg_id, caption=caption, parse_mode="HTML"
            )
            success += 1
        except Exception:
            pass

    await message.reply(
        f"✅ Giveaway #{gw_id} ended. Winners `{winners_list}` successfully announced in {success} groups!",
        parse_mode="Markdown",
    )


# --- CORE TRACKING & MAIN ---
@dp.message()
async def track_stars(message: Message):
    if message.chat.type not in ["group", "supergroup"]:
        return
        
    cursor.execute(
        "INSERT OR REPLACE INTO connected_groups (chat_id, title, username) VALUES (?, ?, ?)",
        (message.chat.id, message.chat.title, message.chat.username)
    )
    conn.commit()

    if message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return

    cursor.execute("SELECT 1 FROM counted_groups WHERE chat_id=?", (message.chat.id,))
    if not cursor.fetchone():
        return

    try:
        member = await bot.get_chat_member(
            chat_id=message.chat.id, user_id=message.from_user.id
        )
        if member.status in ["administrator", "creator"]:
            return
    except Exception:
        pass

    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    cursor.execute(
        "INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, username, STARS_PER_POST, datetime.now()),
    )
    conn.commit()
    await update_live_messages()

    try:
        await message.react(
            [
                ReactionTypeEmoji(emoji="❤"),
                ReactionTypeEmoji(emoji="👍"),
                ReactionTypeEmoji(emoji="🔥"),
            ]
        )
    except Exception:
        try:
            emojis = ["❤", "👍", "🔥", "⚡"]
            chosen = random.choice(emojis)
            await message.react([ReactionTypeEmoji(emoji=chosen)])
        except Exception:
            pass


async def send_winner_announcement():
    cursor.execute("SELECT chat_id FROM live_messages")
    chats = set(row[0] for row in cursor.fetchall())
    if not chats:
        return False

    one_week_ago = datetime.now() - timedelta(days=7)
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders WHERE timestamp >= ? GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1",
        (one_week_ago,),
    )
    winner = cursor.fetchone()

    if not winner:
        cursor.execute(
            "SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1"
        )
        winner = cursor.fetchone()

    if winner:
        msg = f"🎉 WINNER ANNOUNCEMENT! 🎉\n\nCongratulations to @{winner[0]} for being #1 with {format_stars(winner[1])} Stars! 🥇\n\nYou have won a FREE advertisement in @Joiwi ✅ Please contact the owner to redeem your prize."
        formatted_msg = apply_premium_emojis(msg)
        for chat_id in chats:
            try:
                await bot.send_message(chat_id, formatted_msg, parse_mode="HTML")
            except Exception:
                pass
        return True
    return False


async def daily_announcement():
    cursor.execute("SELECT chat_id FROM live_messages")
    chats = set(row[0] for row in cursor.fetchall())
    if not chats:
        return
    cursor.execute(
        "SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1"
    )
    top_user = cursor.fetchone()
    if top_user:
        msg = f"🏆 Daily Top Spender Update! 🎉\n\nOur biggest supporter is @{top_user[0]} with {format_stars(top_user[1])} Stars! 🥇"
        formatted_msg = apply_premium_emojis(msg)
        for chat_id in chats:
            try:
                await bot.send_message(chat_id, formatted_msg, parse_mode="HTML")
            except Exception:
                pass


@dp.errors()
async def global_error_handler(event: ErrorEvent):
    if "Too Many Requests" in str(event.exception) or "Retry in" in str(
        event.exception
    ):
        return
    error_msg = f"⚠️ **Bot Error Alert** ⚠️\n\n`{event.exception}`"
    try:
        await bot.send_message(chat_id=OWNER_ID, text=error_msg)
    except Exception:
        pass


async def main():
    scheduler.add_job(daily_announcement, "cron", hour=12, minute=0)
    scheduler.add_job(
        send_winner_announcement, "cron", day_of_week="sun", hour=12, minute=0
    )
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
