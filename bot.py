import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

# ======================
# PUT YOUR TOKEN HERE
# ======================
import os

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# Your Telegram user id(s) who can manage the bot
ADMINS = {714658983}

DB = "bot.db"
awaiting_photo = set()


def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in ADMINS


async def init_db() -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_counts (
            chat_id INTEGER PRIMARY KEY,
            count INTEGER NOT NULL
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
        await db.execute("INSERT OR IGNORE INTO settings VALUES ('enabled','1')")
        await db.execute("INSERT OR IGNORE INTO settings VALUES ('every','10')")
        await db.execute("INSERT OR IGNORE INTO settings VALUES ('text','Ad message')")
        await db.execute("INSERT OR IGNORE INTO settings VALUES ('photo','')")
        await db.commit()


async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else ""


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        await db.commit()


async def inc_count(chat_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        # Create row if not exists
        await db.execute(
            "INSERT OR IGNORE INTO chat_counts(chat_id, count) VALUES (?, 0)",
            (chat_id,)
        )
        # Atomic increment
        await db.execute(
            "UPDATE chat_counts SET count = count + 1 WHERE chat_id = ?",
            (chat_id,)
        )
        cur = await db.execute(
            "SELECT count FROM chat_counts WHERE chat_id = ?",
            (chat_id,)
        )
        row = await cur.fetchone()
        await db.commit()
        return int(row[0])


async def main() -> None:
    await init_db()

    bot = Bot(TOKEN)
    dp = Dispatcher()

    # ======================
    # GROUP: auto-post logic
    # ======================
    @dp.message(F.chat.type.in_({"group", "supergroup"}))
    async def group_message(message: Message) -> None:
        if message.from_user and message.from_user.is_bot:
            return

        if await get_setting("enabled") != "1":
            return

        every_raw = await get_setting("every")
        try:
            every = int(every_raw)
            if every <= 0:
                every = 10
        except Exception:
            every = 10

        count = await inc_count(message.chat.id)

        if count % every == 0:
            text = await get_setting("text")
            photo = await get_setting("photo")

            if photo.strip():
                await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=text)
            else:
                await bot.send_message(chat_id=message.chat.id, text=text)

    # ======================
    # PRIVATE: admin commands
    # ======================
    @dp.message(F.chat.type == "private", Command("status"))
    async def status(message: Message) -> None:
        if not is_admin(message):
            return
        enabled = await get_setting("enabled")
        every = await get_setting("every")
        photo = await get_setting("photo")
        await message.answer(
            f"Status: {'ON' if enabled == '1' else 'OFF'}\n"
            f"Every: {every}\n"
            f"Photo: {'YES' if photo.strip() else 'NO'}"
        )

    @dp.message(F.chat.type == "private", Command("on"))
    async def cmd_on(message: Message) -> None:
        if not is_admin(message):
            return
        await set_setting("enabled", "1")
        await message.answer("Enabled ✅")

    @dp.message(F.chat.type == "private", Command("off"))
    async def cmd_off(message: Message) -> None:
        if not is_admin(message):
            return
        await set_setting("enabled", "0")
        await message.answer("Disabled ⛔️")

    @dp.message(F.chat.type == "private", Command("set"))
    async def cmd_set(message: Message) -> None:
        if not is_admin(message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await message.answer("Use: /set 10")
            return
        try:
            n = int(parts[1].strip())
            if n <= 0 or n > 1000:
                raise ValueError()
        except Exception:
            await message.answer("Number must be 1..1000. Example: /set 10")
            return

        await set_setting("every", str(n))
        await message.answer(f"Now every {n}-th message ✅")

    @dp.message(F.chat.type == "private", Command("text"))
    async def cmd_text(message: Message) -> None:
        if not is_admin(message):
            return
        t = (message.text or "").replace("/text", "", 1).strip()
        if not t:
            await message.answer("Use: /text your new ad text here")
            return
        await set_setting("text", t)
        await message.answer("Text updated ✅")

    @dp.message(F.chat.type == "private", Command("photo"))
    async def cmd_photo(message: Message) -> None:
        if not is_admin(message):
            return
        awaiting_photo.add(message.from_user.id)
        await message.answer("Send ONE photo now (or send image as a file).")

    @dp.message(F.chat.type == "private", Command("clearphoto"))
    async def cmd_clear_photo(message: Message) -> None:
        if not is_admin(message):
            return
        await set_setting("photo", "")
        await message.answer("Photo cleared. Ads will be text-only ✅")

    # Photo sent as photo
    @dp.message(F.chat.type == "private", F.photo)
    async def on_photo(message: Message) -> None:
        if not is_admin(message):
            return

        if message.from_user.id not in awaiting_photo:
            await message.answer("First send /photo, then send the photo.")
            return

        file_id = message.photo[-1].file_id
        await set_setting("photo", file_id)
        awaiting_photo.discard(message.from_user.id)
        await message.answer("Photo saved ✅")

    # Photo sent as file (document)
    @dp.message(F.chat.type == "private", F.document)
    async def on_document(message: Message) -> None:
        if not is_admin(message):
            return

        if message.from_user.id not in awaiting_photo:
            return

        mime = (message.document.mime_type or "").lower()
        if not mime.startswith("image/"):
            await message.answer("This is not an image. Send an image file.")
            return

        file_id = message.document.file_id
        await set_setting("photo", file_id)
        awaiting_photo.discard(message.from_user.id)
        await message.answer("Image file saved ✅")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
import os
import asyncio
from aiohttp import web

# -------------------------
# HEALTHCHECK ДЛЯ RENDER
# -------------------------
async def healthcheck_server():
    port = int(os.getenv("PORT", "10000"))

    app = web.Application()

    async def ok(request):
        return web.Response(text="OK")

    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    while True:
        await asyncio.sleep(3600)


# -------------------------
# MAIN
# -------------------------
async def main():
    await init_db()

    await asyncio.gather(
        dp.start_polling(bot),
        healthcheck_server(),
    )

if __name__ == "__main__":
    asyncio.run(main())
