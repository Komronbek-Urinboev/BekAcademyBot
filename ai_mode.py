import difflib
import google.generativeai as genai
import time
import random
from bot import bot
user_timers = {}

# Укажи свой API-ключ Gemini
GEMINI_API_KEY = "key-2y3NTU"
genai.configure(api_key=GEMINI_API_KEY)


# Локальная база вопросов и ответов
FAQ = {
    "Bek Academy": "Bek Academy.",
    "Who created bekacademy": "Someone.",
}

# Функция поиска наиболее похожего вопроса
def find_best_match(user_question: str):
    questions = list(FAQ.keys())
    matches = difflib.get_close_matches(user_question, questions, n=1, cutoff=0.7)
    return matches[0] if matches else None



# Функция разбиения длинного сообщения на части
def split_message(text, max_length=4000):
    return [text[i:i+max_length] for i in range(0, len(text), max_length)]

# Обработчик команды /ask
@bot.message_handler(commands=['ask'])
def ask_ai(message):
    user_id = message.from_user.id
    current_time = time.time()

    if user_id in user_timers and current_time - user_timers[user_id] < 10:
        return  # Просто игнорируем сообщение

    user_timers[user_id] = current_time
    user_text = message.text.replace("/ask", "").strip().lower()
    if not user_text:
        bot.reply_to(message, "Вы не ввели вопрос. Используйте: `/ask ваш вопрос`")
        return

    # Показываем progress bar
    progress_msg = bot.send_message(message.chat.id, "⌛ Генерация ответа: [0%]")
    progress_steps = [
        "▱▱▱▱▱▱▱▱▱▱▱ 0%",
        "▰▱▱▱▱▱▱▱▱▱▱ 5%",
        "▰▰▱▱▱▱▱▱▱▱▱ 14%",
        "▰▰▰▱▱▱▱▱▱▱▱ 16%",
        "▰▰▰▰▱▱▱▱▱▱▱ 22%",
        "▰▰▰▰▱▱▱▱▱▱▱ 28%",
        "▰▰▰▰▱▱▱▱▱▱▱ 37%",
        "▰▰▰▰▰▱▱▱▱▱▱ 39%",
        "▰▰▰▰▰▱▱▱▱▱▱ 43%",
        "▰▰▰▰▰▰▱▱▱▱▱ 48%",
        "▰▰▰▰▰▰▰▰▱▱▱ 55%",
        "▰▰▰▰▰▰▰▰▱▱▱ 63%",
        "▰▰▰▰▰▰▰▰▱▱▱ 69%",
        "▰▰▰▰▰▰▰▰▱▱▱ 75%",
        "▰▰▰▰▰▰▰▰▰▱▱ 79%",
        "▰▰▰▰▰▰▰▰▰▱▱ 80%",
        "▰▰▰▰▰▰▰▰▰▰▱▱ 85%",
        "▰▰▰▰▰▰▰▰▰▰▰▱ 92%",
        "▰▰▰▰▰▰▰▰▰▰▰▰▱ 94%",
        "▰▰▰▰▰▰▰▰▰▰▰▰▱ 99%",
        "▰▰▰▰▰▰▰▰▰▰▰▰▰▰ 100%"
    ]


    for step in progress_steps:
        time.sleep(random.uniform(0.1, 0.3))  # Случайная задержка от 0.1 до 1 секунды
        bot.edit_message_text(f"⌛ Генерация ответа: {step}", chat_id=message.chat.id,
                              message_id=progress_msg.message_id)

    # Ищем ответ в FAQ
    best_match = find_best_match(user_text)
    if best_match:
        bot.edit_message_text(f"✅ Ответ найден:\n {FAQ[best_match]}", chat_id=message.chat.id,
                              message_id=progress_msg.message_id)
        return

    # Если вопрос не найден — отправляем в ИИ
    try:
        model = genai.GenerativeModel("gemini-3-flash-preview")
        response = model.generate_content(user_text)
        response_text = response.text

        # Если ответ длиннее 4000 символов, разбиваем его на части и отправляем поочередно
        parts = split_message(response_text)
        bot.edit_message_text(f"✅ Ответ: {parts[0]}", chat_id=message.chat.id,
                              message_id=progress_msg.message_id)
        for part in parts[1:]:
            bot.send_message(message.chat.id, part)

    except Exception as e:
        bot.edit_message_text("❌ Ошибка при обработке запроса. Попробуйте позже.", chat_id=message.chat.id,
                              message_id=progress_msg.message_id)
        print(f"Ошибка: {e}")
print("Модуль ai_feature загружен")