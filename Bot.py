import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# Sozlamalar Railway'dagi Variables'dan olinadi (kodga token yozilmaydi)
TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------------------- BAZA ----------------------------
db = sqlite3.connect(os.getenv("DB_PATH", "kino.db"))
db.execute(
    """CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        poster TEXT,
        link TEXT
    )"""
)
db.execute(
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY
    )"""
)
db.commit()


def get_movie(code: str):
    return db.execute(
        "SELECT code, title, description, poster, link FROM movies WHERE code = ?",
        (code,),
    ).fetchone()


def save_user(user_id: int):
    db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    db.commit()


# ------------------------ MAJBURIY OBUNA ------------------------
async def not_subscribed(user_id: int) -> list[str]:
    missing = []
    for ch in CHANNELS:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:
            logging.warning("Kanal tekshirib bo'lmadi %s: %s (bot kanalda admin emasmi?)", ch, e)
    return missing


def sub_keyboard(missing: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"➕ {ch}", url=f"https://t.me/{ch.lstrip('@')}")]
        for ch in missing
    ]
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ensure_subscribed(message: Message) -> bool:
    missing = await not_subscribed(message.from_user.id)
    if missing:
        await message.answer(
            "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
            reply_markup=sub_keyboard(missing),
        )
        return False
    return True


@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    missing = await not_subscribed(call.from_user.id)
    if missing:
        await call.answer("Hali hamma kanalga obuna bo'lmadingiz!", show_alert=True)
    else:
        await call.message.answer("Rahmat! Endi kino kodini yuboring 🎬")
        await call.answer()


# ---------------------------- ADMIN: /add ----------------------------
class AddMovie(StatesGroup):
    code = State()
    title = State()
    description = State()
    poster = State()
    link = State()


def is_admin(message: Message) -> bool:
    return message.from_user.id == ADMIN_ID


@dp.message(Command("add"))
async def add_start(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.set_state(AddMovie.code)
    await message.answer("Kino kodini yuboring (masalan: 145). Bekor qilish: /cancel")


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.clear()
    await message.answer("Bekor qilindi.")


@dp.message(AddMovie.code, F.text)
async def add_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    await state.set_state(AddMovie.title)
    await message.answer("Kino nomini yuboring:")


@dp.message(AddMovie.title, F.text)
async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddMovie.description)
    await message.answer("Tavsif yuboring (janr, yil, qisqacha mazmun):")


@dp.message(AddMovie.description, F.text)
async def add_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddMovie.poster)
    await message.answer("Muqova rasmini yuboring (yoki /skip):")


@dp.message(AddMovie.poster, F.photo)
async def add_poster(message: Message, state: FSMContext):
    await state.update_data(poster=message.photo[-1].file_id)
    await state.set_state(AddMovie.link)
    await message.answer("Havola yuboring (masalan, qonuniy ko'rish joyi) yoki /skip:")


@dp.message(AddMovie.poster, Command("skip"))
async def skip_poster(message: Message, state: FSMContext):
    await state.update_data(poster=None)
    await state.set_state(AddMovie.link)
    await message.answer("Havola yuboring yoki /skip:")


async def finish_add(message: Message, state: FSMContext, link):
    data = await state.get_data()
    db.execute(
        "INSERT OR REPLACE INTO movies (code, title, description, poster, link) VALUES (?, ?, ?, ?, ?)",
        (data["code"], data["title"], data.get("description"), data.get("poster"), link),
    )
    db.commit()
    await state.clear()
    await message.answer(f"✅ Saqlandi: {data['code']} — {data['title']}")


@dp.message(AddMovie.link, Command("skip"))
async def skip_link(message: Message, state: FSMContext):
    await finish_add(message, state, None)


@dp.message(AddMovie.link, F.text)
async def add_link(message: Message, state: FSMContext):
    await finish_add(message, state, message.text.strip())


@dp.message(Command("del"))
async def delete_movie(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Foydalanish: /del 145")
        return
    db.execute("DELETE FROM movies WHERE code = ?", (parts[1].strip(),))
    db.commit()
    await message.answer("O'chirildi (agar bunday kod bo'lgan bo'lsa).")


@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message):
        return
    users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    movies = db.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    await message.answer(f"👥 Foydalanuvchilar: {users}\n🎬 Kinolar: {movies}")


# ---------------------------- FOYDALANUVCHI ----------------------------
@dp.message(CommandStart())
async def start(message: Message):
    save_user(message.from_user.id)
    if not await ensure_subscribed(message):
        return
    await message.answer("Salom! 🎬 Kino kodini yuboring, men ma'lumotini topib beraman.")


@dp.message(F.text, ~F.text.startswith("/"))
async def get_code(message: Message):
    save_user(message.from_user.id)
    if not await ensure_subscribed(message):
        return

    movie = get_movie(message.text.strip())
    if not movie:
        await message.answer("❌ Bunday kodli kino topilmadi.")
        return

    code, title, description, poster, link = movie
    caption = f"🎬 <b>{title}</b>\n\n{(description or '')[:700]}\n\n🔢 Kod: {code}"
    keyboard = None
    if link:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="▶️ Ko'rish", url=link)]]
        )

    if poster:
        await message.answer_photo(poster, caption=caption, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
