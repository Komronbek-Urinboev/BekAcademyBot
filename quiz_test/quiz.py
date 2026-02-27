import random

# Если у тебя есть файл quiz_questions.py, используй:
# from quiz_questions import questions
# Для примера работы я оставлю тестовый вопрос здесь:
from quiz_questions import questions

# Состояние активной викторины
quiz_state = {
    "active_question": None,
    "correct_count": 0,
    "answered_users": set()
}