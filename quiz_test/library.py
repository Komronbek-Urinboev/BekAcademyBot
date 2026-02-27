# library.py

import time
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

router = Router()

# ================== CONFIG ==================

BOOKS = [
    {"title": "Insta English", "message_id": 7},
    {"title": "4000 Essential books", "message_id": 26},
{"title": "New round", "message_id": 28},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
# {"title": " ", "message_id": 26},
]

CHANNEL_ID = -1003748858680
RATE_LIMIT_SECONDS = 10

user_timers: dict[int, float] = {}


# ================== KEYBOARDS ==================

def get_books_markup(page: int = 0, per_page: int = 9) -> InlineKeyboardMarkup:
    start = page * per_page
    end = start + per_page
    books_on_page = BOOKS[start:end]

    keyboard = []

    # Книги
    for book in books_on_page:
        keyboard.append([
            InlineKeyboardButton(
                text=book["title"],
                callback_data=f"book_{book['message_id']}"
            )
        ])

    # Навигация
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="<<<<<",
                callback_data=f"prev_{page - 1}"
            )
        )

    if end < len(BOOKS):
        navigation.append(
            InlineKeyboardButton(
                text=">>>>>",
                callback_data=f"next_{page + 1}"
            )
        )

    if navigation:
        keyboard.append(navigation)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_inline_book_button(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Download",
                    callback_data=f"book_{message_id}"
                )
            ]
        ]
    )


# ================== COMMAND HANDLERS ==================

@router.message(F.text.in_({"/library", "📚 Library"}))
async def show_library(message: Message):
    user_id = message.from_user.id
    current_time = time.time()

    # Rate limit
    last_time = user_timers.get(user_id)
    if last_time and current_time - last_time < RATE_LIMIT_SECONDS:
        return

    user_timers[user_id] = current_time

    await message.answer(
        "📚 Library:",
        reply_markup=get_books_markup()
    )


# ================== CALLBACK HANDLERS ==================

@router.callback_query(F.data.startswith("book_"))
async def send_book(call: CallbackQuery):
    message_id = int(call.data.split("_")[1])

    await call.bot.forward_message(
        chat_id=call.from_user.id,
        from_chat_id=CHANNEL_ID,
        message_id=message_id
    )

    await call.answer()


@router.callback_query(F.data.startswith("next_"))
async def next_page(call: CallbackQuery):
    page = int(call.data.split("_")[1])

    await call.message.edit_reply_markup(
        reply_markup=get_books_markup(page)
    )

    await call.answer()


@router.callback_query(F.data.startswith("prev_"))
async def prev_page(call: CallbackQuery):
    page = int(call.data.split("_")[1])

    await call.message.edit_reply_markup(
        reply_markup=get_books_markup(page)
    )

    await call.answer()


# ================== INLINE SEARCH ==================

@router.inline_query(F.query.len() > 2)
async def inline_search(query: InlineQuery):
    search_text = query.query.lower()
    results = []

    for book in BOOKS:
        if search_text in book["title"].lower():
            results.append(
                InlineQueryResultArticle(
                    id=str(book["message_id"]),
                    title=book["title"],
                    input_message_content=InputTextMessageContent(
                        message_text=f"📖 {book['title']}"
                    ),
                    reply_markup=get_inline_book_button(book["message_id"])
                )
            )

    if not results:
        results.append(
            InlineQueryResultArticle(
                id="not_found",
                title="Not found",
                input_message_content=InputTextMessageContent(
                    message_text="Book not found 📚"
                )
            )
        )

    await query.answer(results, cache_time=1)


print("✅ Library router loaded successfully")