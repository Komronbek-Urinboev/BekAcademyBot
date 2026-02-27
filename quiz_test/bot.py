import asyncio
import logging
import sqlite3
import random
import os
import sys
import uuid
from datetime import datetime, timedelta, UTC
import difflib
import google.generativeai as genai
import time
####################################
###################################
##############################################
import redis
import pandas as pd
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import FSInputFile, KeyboardButton
from library import get_books_markup
from quiz import *
# --- 1. CONFIGURATION ---
load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

CODE_TTL = 60
ADMIN_IDS = {7393504121}

if not API_TOKEN:
    print("BOT_TOKEN missing")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 2. INIT BOT & DISPATCHER (ВАЖНО: раньше router подключения) ---

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- 3. ПОДКЛЮЧАЕМ ROUTERS ПОСЛЕ СОЗДАНИЯ dp ---

from library import router as library_router

dp.include_router(library_router)

# --- 4. REDIS ---
try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=0,
        decode_responses=True
    )
    redis_client.ping()
except redis.ConnectionError:
    logger.error("Redis not running")
    sys.exit(1)

# --- 5. DATABASE ---
# --- 5. DATABASE ---
conn = sqlite3.connect("bot_quiz.db")
cursor = conn.cursor()

# Создаем таблицу. Убедись, что здесь ровно 11 колонок.
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
    score INTEGER DEFAULT 0 
)
""")
conn.commit()

# Безопасная миграция: если база старая и колонки score нет — добавим её
try:
    cursor.execute("ALTER TABLE users ADD COLUMN score INTEGER DEFAULT 0")
    conn.commit()
    print("Column 'score' added successfully.")
except sqlite3.OperationalError:
    pass  # Колонка уже существует

##
def add_user(user: types.User, lang, referrer=None):
    referral_code = str(uuid.uuid4())[:8]
    token = str(uuid.uuid4())

    sql = """
    INSERT OR IGNORE INTO users 
    (user_id, first_name, username, reg_time, language, referral_code,
     referrer_id, referrals, token, phone, score)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    params = (
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
        0
    )

    cursor.execute(sql, params)

    if referrer and referrer != user.id:
        cursor.execute(
            "UPDATE users SET referrals = referrals + 1 WHERE user_id=?",
            (referrer,)
        )

    conn.commit()


# ================== QUIZ SYSTEM ==================

@dp.message(Command("quiz_start"))
async def start_quiz(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для запуска викторины.")
        return

    # Предполагается, что questions импортирован из quiz_questions
    if not questions:
        await message.answer("❌ Ошибка: Список вопросов пуст.")
        return

    quiz_state["active_question"] = random.choice(questions)
    quiz_state["correct_count"] = 0
    quiz_state["answered_users"].clear()

    active_q = quiz_state["active_question"]
    question_text = f"❓ <b>ВИКТОРИНА!</b>\n\n{active_q['question']}\n\nВыберите правильный вариант:"

    kb = InlineKeyboardBuilder()
    for i, answer in enumerate(active_q["answers"]):
        kb.button(text=answer, callback_data=f"quiz_{i}")
    kb.adjust(1)

    cursor.execute("SELECT user_id FROM users")
    all_users = cursor.fetchall()

    status_msg = await message.answer(f"⏳ Рассылка запущена для {len(all_users)} чел...")

    sent_count = 0
    for (uid,) in all_users:
        try:
            await bot.send_message(uid, question_text, reply_markup=kb.as_markup(), parse_mode="HTML")
            sent_count += 1
            await asyncio.sleep(0.04)  # Чуть быстрее, но безопасно
        except Exception:
            continue

    await status_msg.edit_text(f"✅ Викторина доставлена {sent_count} пользователям.")


@dp.callback_query(F.data.startswith("quiz_"))
async def handle_quiz_answer(callback: types.CallbackQuery):
    if quiz_state["active_question"] is None:
        await callback.answer("❌ Эта викторина уже не активна.", show_alert=True)
        try:
            await callback.message.delete()
        except:
            pass
        return

    user_id = callback.from_user.id
    if user_id in quiz_state["answered_users"]:
        await callback.answer("⚠️ Вы уже давали ответ!", show_alert=True)
        return

    quiz_state["answered_users"].add(user_id)
    answer_idx = int(callback.data.split("_")[1])
    correct_idx = quiz_state["active_question"].get("correct")

    if answer_idx == correct_idx:
        # ПРАВИЛЬНО
        cursor.execute("UPDATE users SET score = score + 1 WHERE user_id=?", (user_id,))
        conn.commit()
        quiz_state["correct_count"] += 1
        await callback.answer("✅ Верно! +1 балл", show_alert=True)

        if quiz_state["correct_count"] >= 8:
            # Оповещаем админа или в общий чат, если нужно
            quiz_state["active_question"] = None
    else:
        # НЕПРАВИЛЬНО
        expl = quiz_state["active_question"].get("explanation", "Неверно!")
        await callback.answer("❌ Ошибка", show_alert=False)
        await callback.message.answer(f"🧐 {expl}")

    await callback.message.delete()


@dp.message(Command("top"))
async def top_players(message: types.Message):
    if not check_rate_limit(message.from_user.id, "top"):
        return

    # Сортируем по score
    cursor.execute("""
        SELECT first_name, username, score 
        FROM users 
        WHERE score > 0 
        ORDER BY score DESC 
        LIMIT 5
    """)
    rows = cursor.fetchall()

    if not rows:
        await message.answer("🏆 Список лидеров пока пуст. Будь первым!")
        return

    text = "🏆 <b>TOP-5 USERS:</b>\n" + "—" * 20 + "\n"
    for i, (name, user, points) in enumerate(rows, 1):
        username = f" (@{user})" if user else ""
        text += f"{i}. {name}{username} — <b>{points}</b> points\n"

    await message.answer(text, parse_mode="HTML")
# --- 3. TEXTS & LOCALIZATION (Mocking textss.py) ---
# Вставил сюда, чтобы код был рабочим из коробки
TEXTS = {
    "uz": {
        "welcome": "Assalomu alaykum! Tilni tanlang:",
        "commands": "Asosiy menyu:",
        "send_phone": "Iltimos, telefon raqamingizni yuboring:",
        "code_sent": "Sizning tasdiqlash kodingiz: `{code}`\n\nU {ttl} soniya davomida amal qiladi.",
        "login": "Kirish kodi: `{code}`\n\nU {ttl} soniya davomida amal qiladi.",
        "referral": "Sizning referallaringiz: {refs}\nHavola: {link}",
        "help": "Yordam bo'limi. Admin bilan bog'lanish:",
        "donation": "💖 Loyihani qo'llab-quvvatlash:",
        "projects": "Bizning loyihalar:",
        "broadcast_prompt": "Xabarni yuboring (barcha foydalanuvchilarga tarqatiladi):",
        "broadcast_cancel": "Tarqatish bekor qilindi.",
        "broadcast_done": "Tarqatish yakunlandi.",
        "expired": "🔒 Kod muddati tugadi. Login tugmasini bosib, yangi kod oling."
    },
    "ru": {
        "welcome": "Здравствуйте! Выберите язык:",
        "commands": "Главное меню:",
        "send_phone": "Пожалуйста, отправьте ваш номер телефона:",
        "code_sent": "Ваш код подтверждения: `{code}`\n\nОн действителен {ttl} секунд.",
        "login": "Ваш код для входа: `{code}`\n\nОн действителен {ttl} секунд.",
        "referral": "Ваши рефералы: {refs}\nСсылка: {link}",
        "help": "Раздел помощи. Связь с админом:",
        "donation": "💖 Поддержать проект:",
        "projects": "Наши проекты:",
        "broadcast_prompt": "Отправьте сообщение для рассылки всем пользователям:",
        "broadcast_cancel": "Рассылка отменена.",
        "broadcast_done": "Рассылка завершена.",
        "expired": "🔒 Срок кода истёк. Нажмите Login для нового кода."
    },
    "en": {
        "welcome": "Hello! Choose your language:",
        "commands": "Main menu:",
        "send_phone": "Please send your phone number:",
        "code_sent": "Your verification code: `{code}`\n\nValid for {ttl} seconds.",
        "login": "Your login code: `{code}`\n\nValid for {ttl} seconds.",
        "referral": "Your referrals: {refs}\nLink: {link}",
        "help": "Help section. Contact admin:",
        "donation": "💖 Support the project:",
        "projects": "Our projects:",
        "broadcast_prompt": "Send the message to broadcast:",
        "broadcast_cancel": "Broadcast cancelled.",
        "broadcast_done": "Broadcast finished.",
        "expired": "🔒 Code expired. Press Login to get a new one."
    }
}

# --- 4. HELPERS & STATE ---
rate_limits = {}
broadcast_state = {}


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()


def set_language(user_id, lang):
    cursor.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    conn.commit()


def save_phone(user_id, phone):
    cursor.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
    conn.commit()


def generate_and_store_code_redis(phone_number: str):
    """
    Логика из Первого кода: Генерируем код и кладем в Redis.
    Ключ: verify_code:{code} -> Значение: phone_number
    TTL: 60 секунд
    """
    code = f"{random.randint(100000, 999999)}"
    # Сохраняем в Redis для бэкенда
    redis_key = f"verify_code:{code}"
    try:
        redis_client.setex(redis_key, CODE_TTL, phone_number)
        logger.info(f"Code {code} stored in Redis for {phone_number}")
        return code
    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")
        return None


async def ui_expire_task(sent_message: types.Message, lang: str):
    """
    Визуальная задача из Второго кода:
    Ждет 60 секунд и редактирует сообщение в Telegram, говоря, что код истек.
    (Сам код удаляется из Redis автоматически благодаря TTL).
    """
    await asyncio.sleep(CODE_TTL)
    try:
        expire_text = TEXTS[lang]["expired"]
        # Редактируем текст, убирая код
        await sent_message.edit_text(expire_text)
    except Exception as e:
        # Сообщение могло быть удалено пользователем
        pass


def check_rate_limit(user_id, command):
    now = datetime.now(UTC)
    key = (user_id, command)
    history = rate_limits.get(key, [])
    # Очищаем старые записи (> 5 сек)
    history = [t for t in history if now - t < timedelta(seconds=5)]
    history.append(now)
    rate_limits[key] = history
    if len(history) >= 3:  # Макс 3 запроса в 5 секунд
        return False
    return True

# --- 5. KEYBOARDS ---
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


# --- 6. HANDLERS ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    args = message.text.split()
    referrer = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    user = get_user(message.from_user.id)

    if not user:
        add_user(message.from_user, "ru", referrer)  # Default ru
        user = get_user(message.from_user.id)

    lang = user[4]

    # 9-й индекс это phone в нашей новой схеме (см. CREATE TABLE)
    if not user[9]:
        kb = InlineKeyboardBuilder()
        kb.button(text="O'zbek", callback_data="lang_uz")
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

    # Если нет телефона - просим
    if not user[9]:
        await callback.message.answer(TEXTS[lang]["send_phone"], reply_markup=phone_keyboard())
    else:
        await callback.message.answer(TEXTS[lang]["commands"], reply_markup=main_keyboard(lang))

    await callback.answer()  # Закрываем часики загрузки


@dp.message(F.text == "📚 Library")
async def open_library(message: types.Message):
    await message.answer(
        "📚 Библиотека:",
        reply_markup=get_books_markup()
    )

@dp.message(F.contact)
async def phone_received(message: types.Message):
    """
    Обработчик контакта.
    1. Сохраняет телефон в SQLite (Код 2).
    2. Генерирует код в Redis (Код 1).
    3. Показывает UI таймер (Код 2).
    """
    user = get_user(message.from_user.id)
    lang = user[4] if user else "en"

    # Валидация "свой ли контакт"
    if message.contact.user_id != message.from_user.id:
        await message.answer("Please send YOUR contact using the button.")
        return

    phone = message.contact.phone_number.replace("+", "")
    save_phone(message.from_user.id, phone)

    # Логика Redis
    code = generate_and_store_code_redis(phone)
    if not code:
        await message.answer("Error connecting to Redis.")
        return

    # Отправка сообщения
    sent_msg = await message.answer(
        TEXTS[lang]["code_sent"].format(code=code, ttl=CODE_TTL),
        reply_markup=main_keyboard(lang),
        parse_mode="Markdown"
    )

    # Визуальный таймер
    asyncio.create_task(ui_expire_task(sent_msg, lang))


@dp.message(Command("login"))
@dp.message(F.text == "🔐 Login")
async def login_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "login"):
        return

    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Please type /start first.")
        return

    lang = user[4]
    phone = user[9]

    # Если телефона нет в базе
    if not phone:
        await message.answer(TEXTS[lang]["send_phone"], reply_markup=phone_keyboard())
        return

    # Генерируем код через Redis
    code = generate_and_store_code_redis(phone)
    if not code:
        await message.answer("Service temporary unavailable (Redis error).")
        return

    sent_msg = await message.answer(
        TEXTS[lang]["login"].format(code=code, ttl=CODE_TTL),
        parse_mode="Markdown"
    )

    asyncio.create_task(ui_expire_task(sent_msg, lang))


# --- Другие кнопки меню (Код 2) ---

@dp.message(F.text.contains("Referral"))
async def referral_cmd(message: types.Message):
    if not check_rate_limit(message.from_user.id, "referral"): return
    user = get_user(message.from_user.id)
    lang = user[4]

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user[5]}"

    txt = TEXTS[lang]["referral"].format(refs=user[7], link=link)

    share_kb = InlineKeyboardBuilder()
    share_kb.button(text="📤 Share", switch_inline_query=link)

    await message.answer(txt, reply_markup=share_kb.as_markup())


@dp.message(F.text.contains("Help"))
async def help_cmd(message: types.Message):
    user = get_user(message.from_user.id)
    lang = user[4]
    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍💻 Admin", url="https://t.me/bekadmn")
    await message.answer(TEXTS[lang]["help"], reply_markup=kb.as_markup())


@dp.message(F.text.contains("Settings"))
async def settings_cmd(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="O'zbek", callback_data="lang_uz")
    kb.button(text="Русский", callback_data="lang_ru")
    kb.button(text="English", callback_data="lang_en")
    await message.answer("Select language / Tilni tanlang:", reply_markup=kb.as_markup())


@dp.message(F.text.contains("Donation"))
async def donation_cmd(message: types.Message):
    user = get_user(message.from_user.id)
    lang = user[4]
    kb = InlineKeyboardBuilder()
    kb.button(text="Donate", url="https://payme.uz")  # Пример
    await message.answer(TEXTS[lang]["donation"], reply_markup=kb.as_markup())


@dp.message(F.text.contains("Projects"))
async def projects_cmd(message: types.Message):
    user = get_user(message.from_user.id)
    lang = user[4]
    kb = InlineKeyboardBuilder()
    kb.button(text="BEK Academy | English", url="https://t.me/bekacademy_english")
    kb.button(text="BEK Academy | News", url="https://t.me/bekacademy_news")
    kb.button(text="BEK Academy | Library", url="https://t.me/bekacademy_library")
    kb.adjust(1)
    await message.answer(TEXTS[lang]["projects"], reply_markup=kb.as_markup())


# --- ADMIN PANEL (Код 2) ---

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="📥 Download DB", callback_data="download_db")
    kb.button(text="📢 Broadcast", callback_data="broadcast")
    await message.answer("Admin Panel", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "download_db")
async def download_db(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    try:
        # Используем pandas для экспорта
        df = pd.read_sql_query("SELECT * FROM users", conn)
        file_path = "users_export.xlsx"
        df.to_excel(file_path, index=False)

        await callback.message.answer_document(FSInputFile(file_path))
    except Exception as e:
        await callback.message.answer(f"Error exporting DB: {e}")


@dp.callback_query(F.data == "broadcast")
async def broadcast_start(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    broadcast_state[callback.from_user.id] = True
    await callback.message.answer(TEXTS["en"]["broadcast_prompt"] + "\n(/deny to cancel)")

FAQ = {
    "bek academy": "Bek Academy is an educational project.",
    "who created bekacademy": "BekAcademy was created by the BEK team."
}

genai.configure(api_key=GEMINI_API_KEY)

# ================== GEMINI ==================
def find_best_match(text):

    matches = difflib.get_close_matches(
        text.lower(),
        FAQ.keys(),
        n=1,
        cutoff=0.7
    )

    return matches[0] if matches else None


def split_message(text, size=4000):

    return [
        text[i:i+size]
        for i in range(0, len(text), size)
    ]


# ================== PROGRESS ==================

async def show_progress(chat_id, msg_id):

    steps = [
        "▱▱▱▱▱ 0%",
        "▰▱▱▱▱ 20%",
        "▰▰▱▱▱ 40%",
        "▰▰▰▱▱ 60%",
        "▰▰▰▰▱ 80%",
        "▰▰▰▰▰ 100%"
    ]

    for s in steps:

        await asyncio.sleep(0.4)

        try:
            await bot.edit_message_text(
                f"⌛ {s}",
                chat_id,
                msg_id
            )
        except:
            break


# ---------- ASK AI ----------

@dp.message(Command("ask"))
@dp.message(F.text == "🤖 Ask AI")
async def ask_ai(message: types.Message):

    if not check_rate_limit(message.from_user.id, "ask"):
        return

    text = message.text.replace("/ask", "").replace("🤖 Ask AI", "").strip()

    if not text:
        await message.answer("❗ Example: /ask What is Python?")
        return

    progress = await message.answer("⌛ 0%")

    task = asyncio.create_task(
        show_progress(message.chat.id, progress.message_id)
    )

    # FAQ
    best = find_best_match(text)

    if best:

        task.cancel()

        await progress.edit_text(
            f"✅ {FAQ[best]}"
        )
        return

    # Gemini
    try:

        model = genai.GenerativeModel("gemini-3-flash-preview")

        response = await asyncio.to_thread(
            model.generate_content,
            text
        )

        answer = response.text

        parts = split_message(answer)

        task.cancel()

        await progress.edit_text(
            f"✅ {parts[0]}"
        )

        for part in parts[1:]:
            await message.answer(part)

    except Exception as e:

        task.cancel()

        logger.error(e)

        await progress.edit_text("❌ AI error")



@dp.message(Command("deny"))
async def broadcast_cancel(message: types.Message):
    if broadcast_state.get(message.from_user.id):
        broadcast_state.pop(message.from_user.id, None)
        await message.answer("Cancelled.")


@dp.message(lambda m: m.from_user.id in ADMIN_IDS and broadcast_state.get(m.from_user.id))
async def broadcast_send(message: types.Message):
    broadcast_state.pop(message.from_user.id, None)

    # Берем всех, у кого есть телефон (или вообще всех, по желанию)
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    msg_to_send = await message.answer(f"Starting broadcast to {len(users)} users...")

    count = 0
    for (uid,) in users:
        try:
            await message.copy_to(uid)
            count += 1
            await asyncio.sleep(0.05)  # Anti-flood
        except Exception:
            pass  # Пользователь заблокировал бота

    await msg_to_send.edit_text(f"Broadcast finished. Sent to {count} users.")

IELTS_PROMPT = """
You are a professional IELTS examiner.

Analyze the following essay.

Give a detailed report in English with these sections:

1. Estimated IELTS Band Score (0-9)
2. Grammar mistakes (with corrections)
3. Vocabulary issues and repetitions
4. Coherence and structure feedback
5. Task response evaluation
6. Suggestions for improvement
7. Rewritten improved version (optional)

Be strict and objective.
"""

@dp.message(Command("check"))
async def check_essay(message: types.Message):

    if not check_rate_limit(message.from_user.id, "check"):
        return

    essay = message.text.replace("/check", "").strip()

    if not essay or len(essay) < 50:
        await message.answer(
            "❗ Please send a full essay (at least 50 words).\n\nExample:\n/check Your essay here..."
        )
        return

    progress = await message.answer("📝 Checking essay... 0%")

    task = asyncio.create_task(
        show_progress(message.chat.id, progress.message_id)
    )

    prompt = f"""
{IELTS_PROMPT}

ESSAY:
----------------
{essay}
----------------
"""

    try:

        model = genai.GenerativeModel("gemini-3-flash-preview")

        response = await asyncio.to_thread(
            model.generate_content,
            prompt
        )

        result = response.text

        parts = split_message(result)

        task.cancel()

        await progress.edit_text(
            f"✅ Essay Report:\n\n{parts[0]}"
        )

        for part in parts[1:]:
            await message.answer(part)

    except Exception as e:

        task.cancel()

        logger.error(f"IELTS Check error: {e}")

        await progress.edit_text(
            "❌ Error while checking essay. Try later."
        )


# --- MAIN ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        conn.close()
        redis_client.close()