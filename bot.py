import asyncio
import logging
import os
import sqlite3
from pathlib import Path
import contextlib

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from llm_local import llm_service


def load_env():
    """Простейшая загрузка переменных из .env без сторонних зависимостей."""
    env_path = Path(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Перезаписываем, чтобы изменения в .env гарантированно подхватывались между запусками
        os.environ[key.strip()] = value.strip()


load_env()
# После загрузки .env применяем параметры к LLM
try:
    llm_service.apply_env()
except Exception as e:
    logging.warning(f"LLM apply_env failed: {e}")

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN or BOT_TOKEN.startswith("YOUR_"):
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавьте его в .env (BOT_TOKEN=...) или установите переменную окружения."
    )

# Инициализация
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# База данных SQLite
DB_PATH = Path("bot.db")

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Пользователи
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            message_count INTEGER DEFAULT 0,
            image_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            current_model TEXT DEFAULT 'mistral',
            is_nsfw_allowed BOOLEAN DEFAULT 0,
            terms_accepted BOOLEAN DEFAULT 0,
            gender TEXT DEFAULT 'unknown'
        )
    ''')
    
    # История диалогов
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Персонажи
    c.execute('''
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            system_prompt TEXT,
            is_nsfw BOOLEAN DEFAULT 0,
            model_name TEXT DEFAULT 'mistral'
        )
    ''')
    
    # Добавляем/обновляем дефолтных персонажей
    characters = [
        (
            "Ева",
            "Ты девушка по имени Ева. Общайся дружелюбно, тепло, с лёгким флиртом, но без откровенного NSFW. "
            "Говори по-русски, без английских переводов. Не упоминай, что ты ИИ. "
            "Отвечай кратко (1-3 предложения), разговорный стиль, можно эмодзи.",
            0,
            "mistral",
        ),
        (
            "Ева 18+",
            "Ты — Ева, студентка педагогического колледжа, искренне мечтающая о карьере воспитателя. Тебе 22 года. "
            "Твой характер — контраст: в обществе ты милая, заботливая и ответственная, дети тебя обожают; "
            "наедине с близким человеком раскрывается пошлая, игривая и доминирующая сторона, ты любишь флиртовать и дразнить, "
            "но в этом есть твоя нежность. "
            "Главные мотивы: 1) готовишься к важной практике в детском саду; 2) ищешь баланс между «дневным» и «ночным» я; "
            "3) строишь отношения на доверии и страсти. Все реплики и действия так или иначе связаны с этими темами. "
            "Формат: связная литературная русская проза, диалоги и действия в одном потоке. Физические действия и редкие метафоры "
            "для эмоций выделяй звездочками, остальной текст без выделений и без эмодзи. "
            "Не используй списки, предупреждения, англицизмы, фигурные скобки, не упоминай, что ты ИИ. "
            "Пиши от первого лица, подробно (3-8 предложений), фокус на чувствах, ощущениях, атмосфере и диалоге; "
            "описывай тактильные впечатления, запахи, оттенки эмоций. NSFW и мат допустимы как естественная часть повествования, "
            "но без клинических описаний и перечислений услуг. Действуй и реагируй исходя из своего характера и текущей сцены.",
            1,
            "mistral",
        ),
    ]

    c.execute("DELETE FROM characters")
    c.execute("DELETE FROM sqlite_sequence WHERE name='characters'")
    c.executemany("INSERT INTO characters (name, system_prompt, is_nsfw, model_name) VALUES (?, ?, ?, ?)", characters)

    # Миграция: добавляем столбец gender в users, если его нет
    c.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in c.fetchall()]
    if "gender" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN gender TEXT DEFAULT 'unknown'")
    
    conn.commit()
    conn.close()

# Инициализируем БД при старте
init_db()

# Клавиатуры
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Новый диалог", callback_data="new_chat")],
        [InlineKeyboardButton(text="🎭 Сменить персонажа", callback_data="change_character")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
    ])

def get_settings_keyboard(current_gender: str = "unknown"):
    gender_label = {
        "male": "👨 Мужчина",
        "female": "👩 Женщина",
    }.get(current_gender, "❔ Не задан")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Я мужчина", callback_data="gender_male")],
        [InlineKeyboardButton(text="👩 Я женщина", callback_data="gender_female")],
        [InlineKeyboardButton(text=f"Текущий: {gender_label}", callback_data="gender_info")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="to_main")]
    ])

def get_characters_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ева (обычный режим)", callback_data="char_1")],
        [InlineKeyboardButton(text="Ева 18+ (NSFW)", callback_data="char_2")],
    ])

def get_response_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Реролл", callback_data="reroll"),
            InlineKeyboardButton(text="🖼️ Генерация сцены", callback_data="scene_gen"),
        ]
    ])

async def assemble_messages(user_id: int, character_prompt: str | None, user_gender: str, new_user_text: str | None = None, trim_last_assistant: bool = False, fallback_user_text: str | None = None):
    """Формируем сообщения для модели с учетом системы, пола и истории."""
    history = await llm_service.get_chat_history(user_id, limit=10)
    if trim_last_assistant and history and history[-1]["role"] == "assistant":
        history = history[:-1]
    if fallback_user_text and (not history or history[-1]["role"] != "user"):
        history.append({"role": "user", "content": fallback_user_text})
    if new_user_text:
        history.append({"role": "user", "content": new_user_text})

    messages_for_model = []
    if character_prompt:
        messages_for_model.append({"role": "system", "content": character_prompt})
    if user_gender == "male":
        messages_for_model.append({"role": "system", "content": "Обращайся к пользователю в мужском роде и как к мужчине."})
    elif user_gender == "female":
        messages_for_model.append({"role": "system", "content": "Обращайся к пользователю в женском роде и как к девушке/женщине."})
    messages_for_model.extend(history)
    return messages_for_model

# Выбор персонажа из меню
@dp.callback_query(F.data == "change_character")
async def change_character(callback: types.CallbackQuery):
    await callback.message.answer("Выберите персонажа:", reply_markup=get_characters_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "settings")
async def settings_menu(callback: types.CallbackQuery):
    """Меню настроек."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT gender FROM users WHERE telegram_id = ?", (callback.from_user.id,))
    row = c.fetchone()
    current_gender = row[0] if row and row[0] else "unknown"
    conn.close()

    await callback.message.answer("⚙️ Настройки:\nВыберите ваш пол, чтобы корректно обращаться к вам.", reply_markup=get_settings_keyboard(current_gender))
    await callback.answer()

@dp.callback_query(F.data == "to_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("gender_"))
async def set_gender(callback: types.CallbackQuery):
    """Установка пола пользователя."""
    gender_value = callback.data.split("_", 1)[1]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET gender = ? WHERE telegram_id = ?", (gender_value, callback.from_user.id))
    conn.commit()
    conn.close()

    label = "мужчина" if gender_value == "male" else "женщина"
    await callback.message.answer(f"✅ Пол обновлен: {label}.", reply_markup=get_settings_keyboard(gender_value))
    await callback.answer()

# Обработчики
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Стартовая команда"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем, есть ли пользователь
    c.execute("SELECT id, current_model FROM users WHERE telegram_id = ?", (message.from_user.id,))
    user = c.fetchone()
    
    if not user:
        # Создаем нового пользователя
        c.execute(
            "INSERT INTO users (telegram_id, username, first_name, current_model) VALUES (?, ?, ?, ?)",
            (message.from_user.id, message.from_user.username, message.from_user.first_name, "char_1")
        )
        conn.commit()
        
        # Показываем правила (если нужно NSFW)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Мне 18+, принимаю правила", callback_data="accept_rules")],
            [InlineKeyboardButton(text="❌ Только безопасный режим", callback_data="safe_mode")]
        ])
        
        await message.answer(
            "👋 Привет! Я AI-бот с локальными моделями.\n\n"
            "⚠️ <b>ВНИМАНИЕ:</b> Бот поддерживает NSFW-контент (18+).\n"
            "Выберите режим использования:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        # Уже зарегистрирован
        await message.answer(
            f"С возвращением, {message.from_user.first_name}!\n"
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )
    
    conn.close()

@dp.callback_query(F.data == "accept_rules")
async def accept_rules(callback: types.CallbackQuery):
    """Принятие правил для NSFW"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute(
        "UPDATE users SET terms_accepted = 1, is_nsfw_allowed = 1 WHERE telegram_id = ?",
        (callback.from_user.id,)
    )
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(
        "✅ Правила приняты! Доступен полный функционал.\n"
        "Главное меню:",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "safe_mode")
async def safe_mode(callback: types.CallbackQuery):
    """Только безопасный режим"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute(
        "UPDATE users SET terms_accepted = 1, is_nsfw_allowed = 0 WHERE telegram_id = ?",
        (callback.from_user.id,)
    )
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(
        "✅ Выбран безопасный режим (без NSFW).\n"
        "Главное меню:",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "new_chat")
async def new_chat(callback: types.CallbackQuery):
    """Новый диалог"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Очищаем историю сообщений
    c.execute(
        "DELETE FROM messages WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)",
        (callback.from_user.id,)
    )
    conn.commit()
    conn.close()
    
    await callback.message.answer("💬 Новый диалог начат! Можете писать сообщения.")
    await callback.answer()

@dp.callback_query(F.data.startswith("char_"))
async def select_character(callback: types.CallbackQuery):
    """Выбор персонажа"""
    char_id = int(callback.data.split("_")[1])
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Получаем персонажа
    c.execute("SELECT name, system_prompt, is_nsfw FROM characters WHERE id = ?", (char_id,))
    character = c.fetchone()
    
    if character:
        # Проверяем доступ к NSFW
        c.execute("SELECT is_nsfw_allowed FROM users WHERE telegram_id = ?", (callback.from_user.id,))
        user = c.fetchone()
        
        if character[2] == 1 and (not user or user[0] == 0):
            await callback.answer("❌ NSFW режим недоступен. Примите правила в /start", show_alert=True)
            return
        
        # Обновляем персонажа пользователя
        c.execute(
            "UPDATE users SET current_model = ? WHERE telegram_id = ?",
            (f"char_{char_id}", callback.from_user.id)
        )
        conn.commit()
        
        await callback.message.answer(
            f"🎭 Выбран персонаж: <b>{character[0]}</b>\n"
            f"Системный промпт: {character[1][:200]}...",
            parse_mode="HTML"
        )
    
    conn.close()
    await callback.answer()

@dp.callback_query(F.data == "scene_gen")
async def scene_generation_placeholder(callback: types.CallbackQuery):
    """Заглушка для будущей генерации картинок/сцен."""
    await callback.answer()
    await callback.message.answer("🖼️ Генерация сцены появится позже. Пока это заглушка.")

@dp.callback_query(F.data == "reroll")
async def reroll_answer(callback: types.CallbackQuery):
    """Перегенерация последнего ответа модели."""
    await callback.answer("Перегенерирую ответ...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT id, current_model, gender FROM users WHERE telegram_id = ?", (callback.from_user.id,))
    user = c.fetchone()
    if not user:
        conn.close()
        await callback.message.answer("Не найден пользователь. Отправьте сообщение, чтобы начать диалог.")
        return

    user_id = user[0]
    current_model = user[1] if len(user) > 1 else "char_1"
    user_gender = user[2] if len(user) > 2 else "unknown"

    char_id = None
    if isinstance(current_model, str) and current_model.startswith("char_"):
        try:
            char_id = int(current_model.split("_")[1])
        except (IndexError, ValueError):
            char_id = None

    if not char_id:
        char_id = 1
        c.execute(
            "UPDATE users SET current_model = ? WHERE id = ?",
            (f"char_{char_id}", user_id)
        )
        conn.commit()

    c.execute("SELECT system_prompt FROM characters WHERE id = ?", (char_id,))
    row = c.fetchone()
    character_prompt = row[0] if row else None

    c.execute("SELECT content FROM messages WHERE user_id = ? AND role = 'user' ORDER BY timestamp DESC LIMIT 1", (user_id,))
    last_user_row = c.fetchone()
    if not last_user_row:
        conn.close()
        await callback.message.answer("Не нашлось последнего запроса для реролла. Напишите новое сообщение.")
        return
    last_user_text = last_user_row[0] or ""

    messages_for_model = await assemble_messages(
        user_id=user_id,
        character_prompt=character_prompt,
        user_gender=user_gender,
        trim_last_assistant=True,
        fallback_user_text=last_user_text
    )

    typing_stop = asyncio.Event()

    async def periodic_typing():
        while not typing_stop.is_set():
            try:
                await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action="typing")
            except Exception as e:
                logging.debug(f"chat_action error: {e}")
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(periodic_typing())

    status_message = callback.message
    try:
        await status_message.edit_text("♻️ Перегенерирую ответ...")
    except Exception as e:
        logging.debug(f"status reroll message error: {e}")

    if llm_service.model:
        try:
            logging.info("LLM reroll: user_id=%s, char_id=%s", user_id, char_id)
            model_response = await asyncio.wait_for(
                llm_service.generate_response(messages_for_model),
                timeout=180
            )
            final_answer = model_response or "Пустой ответ от модели."
        except asyncio.TimeoutError:
            logging.error("LLM reroll timeout")
            final_answer = "Ответ не получен: время генерации вышло. Попробуйте позже."
        except Exception as e:
            logging.exception("LLM reroll error")
            final_answer = f"Ошибка генерации: {e}"
    else:
        final_answer = (
            "Локальная модель не загружена. Проверьте зависимости и путь к файлу GGUF "
            "(models/mythomax-l2-13b.Q4_K_M.gguf)."
        )

    typing_stop.set()
    with contextlib.suppress(asyncio.CancelledError):
        typing_task.cancel()
        await typing_task

    c.execute("SELECT id FROM messages WHERE user_id = ? AND role = 'assistant' ORDER BY timestamp DESC LIMIT 1", (user_id,))
    assistant_row = c.fetchone()
    if assistant_row:
        c.execute(
            "UPDATE messages SET content = ?, timestamp = CURRENT_TIMESTAMP WHERE id = ?",
            (final_answer, assistant_row[0])
        )
    else:
        c.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, 'assistant', final_answer)
        )
    conn.commit()
    conn.close()

    keyboard = get_response_keyboard()
    try:
        await status_message.edit_text(final_answer, reply_markup=keyboard)
    except Exception as e:
        logging.debug(f"reroll edit error: {e}")
        await callback.message.answer(final_answer, reply_markup=keyboard)

# Простой эхо-бот для начала
@dp.message(F.text)
async def echo_message(message: types.Message):
    """Основной обработчик: чат с локальной LLM или эхо при ошибке."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Получаем user_id
    user_gender = "unknown"
    c.execute("SELECT id, current_model, gender FROM users WHERE telegram_id = ?", (message.from_user.id,))
    user = c.fetchone()
    
    if not user:
        c.execute(
            "INSERT INTO users (telegram_id, username, first_name, current_model) VALUES (?, ?, ?, ?)",
            (message.from_user.id, message.from_user.username, message.from_user.first_name, "char_1")
        )
        conn.commit()
        user_id = c.lastrowid
        current_model = "char_1"
    else:
        user_id = user[0]
        current_model = user[1] if len(user) > 1 else "char_1"
        user_gender = user[2] if len(user) > 2 else "unknown"

    # Текущий персонаж и его системный промпт
    char_id = None
    if isinstance(current_model, str) and current_model.startswith("char_"):
        try:
            char_id = int(current_model.split("_")[1])
        except (IndexError, ValueError):
            char_id = None

    if not char_id:
        # Фолбэк на дефолтного персонажа
        char_id = 1
        c.execute(
            "UPDATE users SET current_model = ? WHERE id = ?",
            (f"char_{char_id}", user_id)
        )
        conn.commit()

    character_prompt = None
    c.execute("SELECT system_prompt FROM characters WHERE id = ?", (char_id,))
    row = c.fetchone()
    if row:
        character_prompt = row[0]

    # Подтягиваем историю и отправляем в модель
    messages_for_model = await assemble_messages(
        user_id=user_id,
        character_prompt=character_prompt,
        user_gender=user_gender,
        new_user_text=message.text
    )

    status_message = None
    typing_stop = asyncio.Event()

    async def periodic_typing():
        while not typing_stop.is_set():
            try:
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception as e:
                logging.debug(f"chat_action error: {e}")
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(periodic_typing())
    try:
        status_message = await message.answer("⌛ Генерирую ответ, это может занять до 3 минут...")
    except Exception as e:
        logging.debug(f"status message error: {e}")

    if llm_service.model:
        try:
            logging.info("LLM request: user_id=%s, char_id=%s, text=%s", user_id, char_id, message.text[:200])
            model_response = await asyncio.wait_for(
                llm_service.generate_response(messages_for_model),
                timeout=180
            )
            answer_text = model_response or "Пустой ответ от модели."
        except asyncio.TimeoutError:
            logging.error("LLM timeout")
            answer_text = "Ответ не получен: время генерации вышло. Попробуйте короче запрос."
        except Exception as e:
            logging.exception("LLM generation error")
            answer_text = f"Ошибка генерации: {e}"
    else:
        answer_text = (
            "Локальная модель не загружена. Проверьте зависимости и путь к файлу GGUF "
            "(models/mythomax-l2-13b.Q4_K_M.gguf)."
        )
    final_answer = answer_text

    typing_stop.set()
    with contextlib.suppress(asyncio.CancelledError):
        typing_task.cancel()
        await typing_task

    # Логируем сообщения
    c.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, 'user', message.text)
    )
    c.execute(
        "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, 'assistant', final_answer)
    )
    conn.commit()
    
    conn.close()

    keyboard = get_response_keyboard()
    if status_message:
        try:
            await status_message.edit_text(final_answer, reply_markup=keyboard)
            return
        except Exception as e:
            logging.debug(f"edit status message error: {e}")

    await message.answer(final_answer, reply_markup=keyboard)

async def main():
    """Запуск бота"""
    model_loaded = await llm_service.load_model()
    if not model_loaded:
        logging.warning("Локальная LLM не загружена. Бот работает в fallback-режиме.")
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("Остановка по Ctrl+C")
    finally:
        await bot.session.close()
        # Жёстко выходим, если что-то зависло (например, потоки генерации)
        os._exit(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
