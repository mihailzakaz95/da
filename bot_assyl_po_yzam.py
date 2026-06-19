import asyncio
import logging
import os
import re
from pathlib import Path

from telethon import TelegramClient, events, Button
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    FloodWaitError,
)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
ADMIN_IDS = set(int(x) for x in os.environ["ADMIN_IDS"].split(","))

SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

bot = TelegramClient("bot_session", API_ID, API_HASH)

active_clients: dict[str, TelegramClient] = {}
auth_sessions: dict[int, dict] = {}
broadcast_sessions: dict[int, dict] = {}


# ═══ УТИЛИТЫ ════════════════════════════════════════════════════

def is_admin(uid): return uid in ADMIN_IDS

def extract_usernames(text: str) -> list[str]:
    return list(set(u.lower() for u in re.findall(r"@([a-zA-Z][a-zA-Z0-9_]{4,31})", text)))

async def load_saved_sessions():
    for f in SESSIONS_DIR.glob("*.session"):
        phone = f.stem
        try:
            c = TelegramClient(str(f.with_suffix("")), API_ID, API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                active_clients[phone] = c
                me = await c.get_me()
                logger.info(f"Загружен: {phone} @{me.username}")
            else:
                await c.disconnect()
        except Exception as e:
            logger.error(f"Ошибка загрузки {phone}: {e}")

def main_menu_buttons():
    return [
        [Button.inline("📱 Аккаунты", data="menu_accounts")],
        [Button.inline("📨 Рассылка", data="menu_send")],
    ]


# ═══ КОМАНДЫ ════════════════════════════════════════════════════

@bot.on(events.NewMessage(pattern="/start"))
async def cmd_start(event):
    if not is_admin(event.sender_id):
        return await event.respond("⛔ Нет доступа.")
    await event.respond("👋 **Broadcast Manager**\nВыбери действие:", buttons=main_menu_buttons(), parse_mode="md")

@bot.on(events.NewMessage(pattern="/cancel"))
async def cmd_cancel(event):
    uid = event.sender_id
    auth_sessions.pop(uid, None)
    broadcast_sessions.pop(uid, None)
    await event.respond("❌ Отменено.", buttons=main_menu_buttons())


# ═══ CALLBACK ═══════════════════════════════════════════════════

@bot.on(events.CallbackQuery())
async def callback_handler(event):
    uid = event.sender_id
    data = event.data.decode()

    if not is_admin(uid):
        return await event.answer("⛔ Нет доступа.", alert=True)

    if data == "menu_accounts":
        await show_accounts_menu(event)

    elif data == "menu_send":
        await start_send(event)

    elif data == "add_account":
        await begin_add_account(event)

    elif data == "cancel":
        auth_sessions.pop(uid, None)
        broadcast_sessions.pop(uid, None)
        await event.edit("❌ Отменено.", buttons=main_menu_buttons())

    elif data == "back_main":
        await event.edit("Главное меню:", buttons=main_menu_buttons())

    elif data.startswith("acc_"):
        await show_account_actions(event, data[4:])

    elif data.startswith("del_"):
        await confirm_delete(event, data[4:])

    elif data.startswith("delok_"):
        await do_delete_account(event, data[6:])

    elif data.startswith("send_acc_"):
        await select_account_for_send(event, data[9:])

    elif data == "send_confirm":
        await run_broadcast(event)

    elif data == "send_cancel":
        broadcast_sessions.pop(uid, None)
        await event.edit("❌ Рассылка отменена.", buttons=main_menu_buttons())


# ═══ АККАУНТЫ ═══════════════════════════════════════════════════

async def show_accounts_menu(event):
    if not active_clients:
        return await event.edit(
            "📭 Нет подключённых аккаунтов.",
            buttons=[
                [Button.inline("➕ Добавить аккаунт", data="add_account")],
                [Button.inline("◀️ Назад", data="back_main")],
            ],
        )

    lines = []
    for phone, client in active_clients.items():
        try:
            me = await client.get_me()
            lines.append(f"• `{phone}` — @{me.username or me.first_name}")
        except:
            lines.append(f"• `{phone}` — ошибка")

    buttons = [[Button.inline(f"⚙️ {p}", data=f"acc_{p}")] for p in active_clients]
    buttons.append([Button.inline("➕ Добавить аккаунт", data="add_account")])
    buttons.append([Button.inline("◀️ Назад", data="back_main")])

    await event.edit(
        "📋 **Аккаунты:**\n\n" + "\n".join(lines),
        buttons=buttons, parse_mode="md",
    )

async def show_account_actions(event, phone: str):
    if phone not in active_clients:
        return await event.answer("Аккаунт не найден.", alert=True)
    try:
        me = await active_clients[phone].get_me()
        name = f"@{me.username or me.first_name}"
    except:
        name = "?"
    await event.edit(
        f"📱 **{phone}**\n👤 {name}",
        buttons=[
            [Button.inline("🗑 Удалить", data=f"del_{phone}")],
            [Button.inline("◀️ Назад", data="menu_accounts")],
        ],
        parse_mode="md",
    )

async def confirm_delete(event, phone: str):
    await event.edit(
        f"❓ Удалить аккаунт `{phone}`?",
        buttons=[
            [Button.inline("✅ Да", data=f"delok_{phone}"),
             Button.inline("❌ Нет", data="menu_accounts")],
        ],
        parse_mode="md",
    )

async def do_delete_account(event, phone: str):
    client = active_clients.pop(phone, None)
    if client:
        await client.disconnect()
    (SESSIONS_DIR / f"{phone}.session").unlink(missing_ok=True)
    await event.edit(
        f"✅ Аккаунт `{phone}` удалён.",
        buttons=[[Button.inline("◀️ К аккаунтам", data="menu_accounts")]],
        parse_mode="md",
    )

async def begin_add_account(event):
    auth_sessions[event.sender_id] = {"step": "phone"}
    await event.edit(
        "📱 Введи номер телефона:\n`+79001234567`\n\n/cancel — отмена",
        buttons=None, parse_mode="md",
    )


# ═══ РАССЫЛКА ═══════════════════════════════════════════════════

async def start_send(event):
    uid = event.sender_id
    if not active_clients:
        return await event.edit(
            "❗ Нет подключённых аккаунтов.",
            buttons=[
                [Button.inline("➕ Добавить аккаунт", data="add_account")],
                [Button.inline("◀️ Назад", data="back_main")],
            ],
        )
    broadcast_sessions[uid] = {"step": "source"}
    await event.edit(
        "📨 Отправь или перешли сообщение с @упоминаниями.\n\n/cancel — отмена",
        buttons=None,
    )

async def select_account_for_send(event, phone: str):
    uid = event.sender_id
    session = broadcast_sessions.get(uid)
    if not session:
        return await event.answer("Сессия истекла, начни заново.", alert=True)

    session["phone"] = phone
    usernames = session["usernames"]
    text = session["broadcast_text"]

    preview = "\n".join(f"• @{u}" for u in usernames[:20])
    if len(usernames) > 20:
        preview += f"\n… и ещё {len(usernames)-20}"

    await event.edit(
        f"📋 **Подтверди рассылку**\n\n"
        f"👥 Получатели: **{len(usernames)}**\n{preview}\n\n"
        f"📝 Текст:\n_{text}_\n\n"
        f"📱 Аккаунт: `{phone}`",
        buttons=[
            [Button.inline("🚀 Отправить", data="send_confirm"),
             Button.inline("❌ Отмена", data="send_cancel")],
        ],
        parse_mode="md",
    )

async def run_broadcast(event):
    uid = event.sender_id
    session = broadcast_sessions.pop(uid, None)
    if not session:
        return await event.answer("Сессия истекла.", alert=True)

    phone = session["phone"]
    usernames = session["usernames"]
    text = session["broadcast_text"]
    client = active_clients.get(phone)

    if not client:
        return await event.edit("❌ Аккаунт не найден.", buttons=main_menu_buttons())

    msg = await event.edit(f"⏳ Запускаю рассылку...\n0 / {len(usernames)}", buttons=None)
    asyncio.create_task(do_broadcast(uid, msg, client, usernames, text))

async def do_broadcast(uid, msg, client, usernames, text):
    success, failed = [], []

    for i, username in enumerate(usernames):
        try:
            await client.send_message(username, text)
            success.append(username)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            try:
                await client.send_message(username, text)
                success.append(username)
            except Exception as e2:
                failed.append((username, str(e2)))
        except Exception as e:
            failed.append((username, str(e)))

        await asyncio.sleep(2)

        if (i + 1) % 5 == 0 or (i + 1) == len(usernames):
            try:
                await msg.edit(
                    f"⏳ Рассылка...\n"
                    f"✅ {len(success)}  ❌ {len(failed)}  📤 {i+1}/{len(usernames)}"
                )
            except:
                pass

    fail_text = ""
    if failed:
        lines = "\n".join(f"• @{u} — {e[:40]}" for u, e in failed[:10])
        if len(failed) > 10:
            lines += f"\n… и ещё {len(failed)-10}"
        fail_text = f"\n\n❌ **Не доставлено:**\n{lines}"

    try:
        await msg.edit(
            f"✅ **Рассылка завершена!**\n\n"
            f"📤 Успешно: **{len(success)}**\n"
            f"❌ Ошибок: **{len(failed)}**" + fail_text,
            buttons=main_menu_buttons(), parse_mode="md",
        )
    except:
        await bot.send_message(uid, f"✅ Готово! Успешно: {len(success)}, ошибок: {len(failed)}", buttons=main_menu_buttons())


# ═══ ТЕКСТОВЫЕ СООБЩЕНИЯ (авторизация + шаги рассылки) ══════════

@bot.on(events.NewMessage(incoming=True))
async def message_handler(event):
    if event.text and event.text.startswith("/"):
        return
    uid = event.sender_id
    if not is_admin(uid):
        return

    if uid in auth_sessions:
        await handle_auth(event)
    elif uid in broadcast_sessions:
        await handle_broadcast_input(event)

async def handle_auth(event):
    uid = event.sender_id
    session = auth_sessions[uid]
    step = session["step"]
    text = event.raw_text.strip()

    if step == "phone":
        phone = text
        if not re.match(r"^\+\d{7,15}$", phone):
            return await event.respond("❗ Формат: `+79001234567`\n\n/cancel", parse_mode="md")
        session_path = str(SESSIONS_DIR / phone)
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        try:
            result = await client.send_code_request(phone)
            session.update({"step": "code", "phone": phone, "client": client, "hash": result.phone_code_hash})
            await event.respond(f"📲 Код отправлен на `{phone}`\nВведи код:\n\n/cancel", parse_mode="md")
        except FloodWaitError as e:
            await client.disconnect(); auth_sessions.pop(uid)
            await event.respond(f"⏳ Flood wait {e.seconds} сек.")
        except Exception as e:
            await client.disconnect(); auth_sessions.pop(uid)
            await event.respond(f"❌ Ошибка: {e}")

    elif step == "code":
        client = session["client"]
        phone = session["phone"]
        try:
            await client.sign_in(phone, text.replace(" ", ""), phone_code_hash=session["hash"])
            await finish_auth(event, uid, phone, client)
        except SessionPasswordNeededError:
            session["step"] = "2fa"
            await event.respond("🔐 Введи пароль двухфакторки:\n\n/cancel")
        except PhoneCodeExpiredError:
            auth_sessions.pop(uid); await client.disconnect()
            await event.respond("❗ Код истёк. Начни заново /start")
        except PhoneCodeInvalidError:
            await event.respond("❗ Неверный код, попробуй ещё:")
        except Exception as e:
            auth_sessions.pop(uid); await client.disconnect()
            await event.respond(f"❌ Ошибка: {e}")

    elif step == "2fa":
        client = session["client"]
        phone = session["phone"]
        try:
            await client.sign_in(password=text)
            await finish_auth(event, uid, phone, client)
        except Exception as e:
            auth_sessions.pop(uid); await client.disconnect()
            await event.respond(f"❌ Ошибка 2FA: {e}")

async def finish_auth(event, uid, phone, client):
    active_clients[phone] = client
    auth_sessions.pop(uid)
    me = await client.get_me()
    await event.respond(
        f"✅ **Аккаунт подключён!**\n👤 @{me.username or me.first_name}\n📱 `{phone}`",
        buttons=main_menu_buttons(), parse_mode="md",
    )

async def handle_broadcast_input(event):
    uid = event.sender_id
    session = broadcast_sessions[uid]
    step = session["step"]

    if step == "source":
        text = event.raw_text or ""
        usernames = extract_usernames(text)
        if not usernames:
            return await event.respond("❗ Не нашёл @юзернеймов. Попробуй ещё.\n\n/cancel")

        session["usernames"] = usernames
        session["step"] = "text"

        preview = "\n".join(f"• @{u}" for u in usernames[:30])
        if len(usernames) > 30:
            preview += f"\n… и ещё {len(usernames)-30}"

        await event.respond(
            f"✅ Найдено **{len(usernames)}** юзернеймов:\n\n{preview}\n\nТеперь напиши текст для рассылки:\n\n/cancel",
            parse_mode="md",
        )

    elif step == "text":
        session["broadcast_text"] = event.raw_text
        session["step"] = "account"

        buttons = [[Button.inline(f"📱 {p}", data=f"send_acc_{p}")] for p in active_clients]
        buttons.append([Button.inline("❌ Отмена", data="send_cancel")])

        await event.respond("👇 Выбери аккаунт для рассылки:", buttons=buttons)


# ═══ ЗАПУСК ═════════════════════════════════════════════════════

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    await load_saved_sessions()
    logger.info(f"Бот запущен. Аккаунтов: {len(active_clients)}")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
