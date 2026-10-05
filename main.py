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

# Updated active bot token
BOT_TOKEN = "8488349023:AAEULckG-HusIfVKAghIcjveyJZqQtqs9Wk"
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

    msg = "❗️Top spenders ⚠️\n@Paidsrobot to check your rank.📢\n\nAll time:\n"
    if not all_time:
        msg += "Nobody yet!\n"
    else:
        for index, row in enumerate(all_time, start=1):
            uname = html.escape(str(row[0]))
            stars = format_stars(row[1])
            emoji = ' <tg-emoji emoji-id="5008457489528652800">⭐️</tg-emoji>🔥' if index == 1 else ""
            msg += f'{index}. @{uname} ({stars} Stars <tg-emoji emoji-id="5030538831225422917">⭐️</tg-emoji>){emoji}\n'
        
    msg += "\nThis week #1. "
    if this_week:
        msg += f"@{html.escape(str(this_week[0]))} ({format_stars(this_week[1])} Stars)\n\n"
    else:
        msg += "Nobody yet!\n\n"
        
    msg += "⚠️️ Every week the #1 spender gets a free Advertisement in @Joiwi ✅ ⚠️"
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
        
    cursor.execute("DELETE FROM spenders WHERE LOWER(username)=?", (username.lower(),))
    if new_amount > 0:
        cursor.execute("INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)", (0, username, new_amount, datetime.now()))
        reply_msg = f"✅ Set @{username}'s total to {new_amount} Stars!"
    else:
        reply_msg = f"🗑 Removed @{username} from the leaderboard entirely."

    conn.commit()
    await update_live_messages()
    await message.reply(apply_premium_emojis(reply_msg), parse_mode="HTML")

@dp.message(Command("addstars"))
async def add_old_spenders(message: Message):
    if not is_admin(message.from_user): return
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("⚠️ Usage: /addstars @username amount")
    username = parts[1].replace("@", "")
    try:
        stars_to_add = int(parts[2])
    except ValueError:
        return await message.reply("⚠️ The amount must be a number.")
        
    cursor.execute("INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)", (0, username, stars_to_add, datetime.now()))
    conn.commit()
    await update_live_messages()
    await message.reply(apply_premium_emojis(f"✅ Added {stars_to_add} Stars to @{username}!"), parse_mode="HTML")

@dp.message(Command("invest"))
@dp.channel_post(Command("invest"))
async def setup_live_leaderboard(message: Message):
    text = generate_leaderboard_text()
    sent_msg = await message.answer(text, parse_mode="HTML")
    if is_admin(message.from_user):
        cursor.execute("INSERT INTO live_messages (chat_id, message_id) VALUES (?, ?)", (message.chat.id, sent_msg.message_id))
        conn.commit()
    try:
        await message.delete()
    except Exception:
        pass

@dp.message(Command("announcewinner"))
async def cmd_announcewinner(message: Message):
    if not is_admin(message.from_user): return
    sent = await send_winner_announcement()
    if sent:
        await message.reply("✅ Winner announcement successfully sent to all connected groups!")
    else:
        await message.reply("⚠️ No spender found this week or no groups connected yet.")

@dp.message(Command("getemoji"))
async def get_emoji_id(message: Message):
    if not is_admin(message.from_user): return
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji":
                return await message.reply(f"Here is the ID for that Premium Emoji:\n`{entity.custom_emoji_id}`")
    await message.reply("⚠️ No premium emoji found in that message. Send `/getemoji` along with a premium emoji.")

@dp.message(Command("make"))
async def cmd_make(message: Message, state: FSMContext):
    if not is_admin(message.from_user): return
    await state.set_state(MakePost.waiting_for_paragraph)
    await message.reply("📝 Please send the paragraph. Any standard emojis mapped in the code will be upgraded to Premium.")

@dp.message(MakePost.waiting_for_paragraph)
async def process_paragraph(message: Message, state: FSMContext):
    await state.clear()
    cursor.execute("SELECT DISTINCT chat_id FROM live_messages")
    groups = cursor.fetchall()
    if not groups:
        return await message.reply("⚠️ No active groups found! Please use /invest in a group first.")

    raw_text = message.html_text if message.html_text else message.text
    formatted_text = apply_premium_emojis(raw_text)
    success_count = 0
    for group in groups:
        chat_id = group[0]
        try:
            await bot.send_message(chat_id=chat_id, text=formatted_text, parse_mode="HTML")
            success_count += 1
        except Exception as e:
            await bot.send_message(OWNER_ID, f"⚠️ Failed to send to group ID {chat_id}. Reason: {str(e)}")
    await message.reply(f"✅ Paragraph successfully posted to {success_count} group(s) with upgraded emojis!")

@dp.message()
async def track_stars(message: Message):
    if message.chat.type not in ["group", "supergroup"]: return
    if message.from_user.is_bot: return 
    if message.text and message.text.startswith('/'): return 
    try:
        member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
        if member.status in ['administrator', 'creator']: return 
    except Exception:
        pass
        
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    cursor.execute("INSERT INTO spenders (user_id, username, stars, timestamp) VALUES (?, ?, ?, ?)", (user_id, username, STARS_PER_POST, datetime.now()))
    conn.commit()
    await update_live_messages()

    try:
        await message.react([
            ReactionTypeEmoji(emoji="❤"),
            ReactionTypeEmoji(emoji="👍"),
            ReactionTypeEmoji(emoji="🔥")
        ])
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
    if not chats: return False

    one_week_ago = datetime.now() - timedelta(days=7)
    cursor.execute("SELECT username, SUM(stars) as total FROM spenders WHERE timestamp >= ? GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1", (one_week_ago,))
    winner = cursor.fetchone()
    
    if not winner:
        cursor.execute("SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1")
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
    if not chats: return
    cursor.execute("SELECT username, SUM(stars) as total FROM spenders GROUP BY LOWER(username) ORDER BY total DESC LIMIT 1")
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
    error_msg = f"⚠️ **Bot Error Alert** ⚠️\n\n`{event.exception}`"
    try:
        await bot.send_message(chat_id=OWNER_ID, text=error_msg)
    except Exception:
        pass

async def main():
    scheduler.add_job(daily_announcement, "cron", hour=12, minute=0)
    scheduler.add_job(send_winner_announcement, "cron", day_of_week="sun", hour=12, minute=0)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
