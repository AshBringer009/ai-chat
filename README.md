## Локальный Telegram-бот с LLM

Aiogram-бот, который работает с локальными GGUF-моделями через `llama.cpp`. История чата хранится в SQLite, есть выбор персонажа и настройка пола обращения.

### Зависимости
- Python 3.10+ (тестировалось на macOS, Apple Silicon)
- Telegram Bot API токен
- Библиотеки: `aiogram`, `llama-cpp-python`, `sqlite3` (встроен), опционально `transformers`/`torch` для альтернативных моделей

### Установка (macOS)
```bash
cd /Users/alexmamzin/Documents/workout/chat-bot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install aiogram llama-cpp-python
# при необходимости: pip install transformers torch
```

### Модель
1) Скачайте GGUF-файл в папку `models/`, например:
```bash
wget -L "https://huggingface.co/mradermacher/L3-8B-Lunar-Stheno-GGUF/resolve/main/L3-8B-Lunar-Stheno.Q4_K_M.gguf" \
  -O models/L3-8B-Lunar-Stheno.Q4_K_M.gguf
```
2) В `.env` укажите путь и параметры:
```
BOT_TOKEN=ваш_токен
ADMIN_ID=ваш_telegram_id
LLM_MODEL_PATH=models/L3-8B-Lunar-Stheno.Q4_K_M.gguf
LLM_CTX=1024
LLM_THREADS=6
LLM_GPU_LAYERS=12
LLM_MAX_TOKENS=128
```

### Запуск
```bash
source venv/bin/activate
python bot.py
```
После запуска бот загрузит модель, создаст/мигрирует SQLite-базу `bot.db` и начнёт polling.

### Полезное
- Файлы моделей (`models/*.gguf`) игнорируются `.gitignore`, добавляйте их вручную.
- Если меняете `.env`, перезапустите бота: параметры подхватываются при старте.
