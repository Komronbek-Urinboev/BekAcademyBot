# Final aiogram v3 bot per updated TZ
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, UTC
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import FSInputFile, KeyboardButton
import pandas as pd
import uuid
from textss import *
import random
from dotenv import load_dotenv
import os
from quiz_test.library import get_books_markup
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {7393504121, 664463661}

# --- DB SETUP ---
conn = sqlite3.connect("bot.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    reg_time TEXT,
    language TEXT,
    referral_code TEXT,
    referrer_id INTEGER,
    referrals INTEGER DEFAULT 0,
    token TEXT,
    phone TEXT,
    verify_code TEXT,
    code_expires TEXT
)
"""
)
conn.commit()

# --- Rate limit storage ---
rate_limits = {}

# --- Broadcast state ---
broadcast_state = {}


bot = Bot(API_TOKEN)
dp = Dispatcher()

# --- Helpers ---
def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()


def add_user(user: types.User, lang, referrer=None):
    referral_code = str(uuid.uuid4())[:8]
    token = str(uuid.uuid4())
    cursor.execute(
        "INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user.id,
            user.first_name,
            user.username,
            datetime.now(UTC).isoformat(),
            lang,
            referral_code,
            referrer,
            0,
            token,
            None,
            None,
            None,
        ),
    )
    if referrer:
        cursor.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id=?", (referrer,))
    conn.commit()


def set_language(user_id, lang):
    cursor.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    conn.commit()


def save_phone(user_id, phone):
    cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    conn.commit()


def generate_code(user_id):
    code = f"{random.randint(0, 999999):06d}"
    expires = datetime.now(UTC) + timedelta(seconds=60)
    cursor.execute(
        "UPDATE users SET verify_code=?, code_expires=? WHERE user_id=?",
        (code, expires.isoformat(), user_id),
    )
    conn.commit()
    return code


async def expire_code_task(sent_message: types.Message, user_id: int, lang: str):
    """Задача, которая отредактирует сообщение через 60 секунд"""
    await asyncio.sleep(60)  # Ждем минуту

    # Проверяем в БД, действителен ли еще код
    cursor.execute("SELECT verify_code FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if row and row[0]:  # Если в базе все еще лежит код (значит, юзер не залогинился)
        # Очищаем код в БД
        cursor.execute("UPDATE users SET verify_code=NULL, code_expires=NULL WHERE user_id=?", (user_id,))
        conn.commit()

        # Текст об истечении времени
        expiry_info = {
            "uz": "🔒 Kod muddati tugadi. Login tugmasini bosib, yangi kod oling.",
            "ru": "🔒 Срок кода истёк. Нажмите Login для нового кода.",
            "en": "🔒 Code expired. Request a new code by pressing Login button."
        }
        expire_text = expiry_info.get(lang, expiry_info["en"])

        # Редактируем сообщение: заменяем {code} на текст об истечении
        # Используем ту же структуру из TEXTS, но подставляем туда сообщение об ошибке
        try:
            # Выбираем ключ в зависимости от того, откуда был вызван (login или code_sent)
            # В данном случае структура текста одинаковая
            new_text = TEXTS[lang]["login"].format(code=expire_text)
            await sent_message.edit_text(new_text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to edit message: {e}")


def check_rate_limit(user_id, command):
    now = datetime.now(UTC)
    key = (user_id, command)
    history = rate_limits.get(key, [])
    history = [t for t in history if now - t < timedelta(seconds=5)]
    history.append(now)
    rate_limits[key] = history
    if len(history) >= 3:
        rate_limits[key] = []
        return False
    return True


def main_keyboard(lang):
    kb = ReplyKeyboardBuilder()

    buttons = [
        "🔐 Login",
        "👥 Referral",
        "🚀 Projects",
        "📚 Library",   # ← НОВАЯ КНОПКА
        "💖 Donation",
        "ℹ️ Help",
        "⚙️ Settings",
    ]

    for b in buttons:
        kb.button(text=b)

    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)
def phone_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.add(KeyboardButton(text="☎️ Send Contact", request_contact=True))
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=True)


@dp.message(F.text == "📚 Library")
async def open_library(message: types.Message):
    await message.answer(
        "📚 Library:",
        reply_markup=get_books_markup()
    )


# --- Handlers ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    args = message.text.split()
    referrer = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    user = get_user(message.from_user.id)

    if not user:
        add_user(message.from_user, "ru", referrer)
        user = get_user(message.from_user.id)

    lang = user[4]

    # If no phone yet → ask phone
    if not user[9]:
        kb = InlineKeyboardBuilder()
        kb.button(text="Uzbek", callback_data="lang_uz")
        kb.button(text="Русский", callback_data="lang_ru")
        kb.button(text="English", callback_data="lang_en")
        await message.answer(TEXTS[lang]["welcome"], reply_markup=kb.as_markup())
    else:
        await message.answer(TEXTS[lang]["commands"], reply_markup=main_keyboard(lang))


@dp.callback_query(lambda c: c.data.startswith("lang_"))
async def set_lang(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    set_language(callback.from_user.id, lang)
    user = get_user(callback.from_user.id)

    # Ask phone only if not saved
    if not user[9]:
        await callback.message.answer(TEXTS[lang]["send_phone"], reply_markup=phone_keyboard())
    else:
        await callback.message.answer(TEXTS[lang]["commands"], reply_markup=main_keyboard(lang))


@dp.message(lambda m: m.contact is not None)
async def phone_received(message: types.Message):
    user = get_user(message.from_user.id)
    lang = user[4]

    if user[9]:  # already have phone
        await message.answer(TEXTS[lang]["commands"], reply_markup=main_keyboard(lang))
        return

    save_phone(message.from_user.id, message.contact.phone_number)
    code = generate_code(message.from_user.id)

    # 1. Отправляем сообщение и сохраняем результат в переменную
    sent_msg = await message.answer(
        TEXTS[lang]["code_sent"].format(code=code),
        reply_markup=main_keyboard(lang),
        parse_mode="Markdown"
    )

    # 2. Запускаем фоновый таймер на 60 секунд для этого сообщения
    asyncio.create_task(expire_code_task(sent_msg, message.from_user.id, lang))


@dp.message(Command("login"))
@dp.message(lambda m: m.text.startswith("🔐"))
async def login_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "login"):
        return

    user = get_user(message.from_user.id)
    lang = user[4]

    # If no phone yet
    if not user[9]:
        await message.answer(TEXTS[lang]["send_phone"], reply_markup=phone_keyboard())
        return

    code = generate_code(message.from_user.id)

    # 1. Отправляем сообщение и сохраняем результат
    sent_msg = await message.answer(
        TEXTS[lang]["login"].format(code=code),
        parse_mode="Markdown"
    )

    # 2. Запускаем фоновый таймер
    asyncio.create_task(expire_code_task(sent_msg, message.from_user.id, lang))

@dp.message(lambda m: m.text.startswith("👥"))
async def referral_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "referral"):
        return

    user = get_user(message.from_user.id)
    lang = user[4]
    link = f"https://t.me/bekacademyuzbot?start={user[5]}"
    txt = TEXTS[lang]["referral"].format(refs=user[7], link=link)

    share_kb = InlineKeyboardBuilder()
    share_kb.button(text="📤 Share", switch_inline_query=link)

    await message.answer(txt, reply_markup=share_kb.as_markup())


@dp.message(lambda m: m.text.startswith("ℹ️"))
async def help_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "help"):
        return

    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Please restart the bot with /start")
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍💻 Admin", url="https://t.me/bekadmn")

    await message.answer(
        TEXTS[user[4]]["help"],
        reply_markup=kb.as_markup()
    )


@dp.message(lambda m: m.text.startswith("⚙️"))
async def settings_cmd(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="Uzbek", callback_data="lang_uz")
    kb.button(text="Русский", callback_data="lang_ru")
    kb.button(text="English", callback_data="lang_en")
    await message.answer("Choose language:", reply_markup=kb.as_markup())


@dp.message(lambda m: m.text.startswith("💖"))
async def donation_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "donation"):
        return

    user = get_user(message.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="Donate", url="https://donate.example.com")
    await message.answer(TEXTS[user[4]]["donation"], reply_markup=kb.as_markup())


@dp.message(lambda m: m.text.startswith("🚀"))
async def projects_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "projects"):
        return

    user = get_user(message.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="BEK Academy | English", url="https://t.me/bekacademy_english")
    kb.button(text="BEK Academy | News", url="https://t.me/bekacademy_news")
    kb.button(text="BEK Academy | Library", url="https://t.me/bekacademy_library")
    kb.adjust(1)
    await message.answer(TEXTS[user[4]]["projects"], reply_markup=kb.as_markup())


# --- Admin ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="📥 Download DB", callback_data="download_db")
    kb.button(text="📢 Broadcast", callback_data="broadcast")
    kb.button(text="🧩 BS", callback_data="bs")

    await message.answer("Admin Panel", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data == "download_db")
async def download_db(callback: types.CallbackQuery):
    df = pd.read_sql_query("SELECT * FROM users", conn)
    file = "users.xlsx"
    df.to_excel(file, index=False)
    await callback.message.answer_document(FSInputFile(file))


@dp.callback_query(lambda c: c.data == "broadcast")
async def broadcast_start(callback: types.CallbackQuery):
    broadcast_state[callback.from_user.id] = True
    user = get_user(callback.from_user.id)
    lang = user[4]
    await callback.message.answer(TEXTS[lang]["broadcast_prompt"])


@dp.message(Command("deny"))
async def broadcast_cancel(message: types.Message):
    if broadcast_state.get(message.from_user.id):
        broadcast_state.pop(message.from_user.id, None)
        user = get_user(message.from_user.id)
        lang = user[4]
        await message.answer(TEXTS[lang]["broadcast_cancel"])


@dp.message()
async def broadcast_send(message: types.Message):
    if not broadcast_state.get(message.from_user.id):
        return

    broadcast_state.pop(message.from_user.id, None)

    cursor.execute("SELECT user_id FROM users WHERE phone IS NOT NULL")
    users = cursor.fetchall()

    sent = 0
    for (uid,) in users:
        try:
            await message.copy_to(uid)
            sent += 1
        except:
            pass

    user = get_user(message.from_user.id)
    lang = user[4]
    await message.answer(TEXTS[lang]["broadcast_done"])


@dp.callback_query(lambda c: c.data == "bs")
async def bs_stub(callback: types.CallbackQuery):
    await callback.message.answer("BS integration stub. Tokens & phone verification ready.")


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())