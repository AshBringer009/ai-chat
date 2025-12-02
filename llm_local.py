"""
Простая интеграция локальной модели через llama.cpp
Сначала скачайте модель в формате GGUF!
"""

import asyncio
import time
from typing import List, Dict
import sqlite3
from pathlib import Path
import os

# Вариант 1: Llama.cpp (рекомендуется для M1)
try:
    from llama_cpp import Llama
    LLAMA_AVAILABLE = True
except ImportError:
    LLAMA_AVAILABLE = False

# Вариант 2: Transformers (если есть GPU память)
try:
    from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
    import torch
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

# Настройки по умолчанию берём из окружения, чтобы не менять код при смене модели
DEFAULT_MODEL_PATH = os.getenv("LLM_MODEL_PATH", "models/mythomax-l2-13b.Q4_K_M.gguf")
DEFAULT_CTX = int(os.getenv("LLM_CTX", "1024"))
DEFAULT_THREADS = int(os.getenv("LLM_THREADS", str(os.cpu_count() or 4)))
DEFAULT_GPU_LAYERS = int(os.getenv("LLM_GPU_LAYERS", "1"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "80"))

class LocalLLM:
    def __init__(self, model_type="llama"):
        self.model_type = model_type
        self.model = None
        self.tokenizer = None
        self.model_path = DEFAULT_MODEL_PATH
        self.n_ctx = DEFAULT_CTX
        self.n_threads = DEFAULT_THREADS
        self.n_gpu_layers = DEFAULT_GPU_LAYERS
        self.default_max_tokens = DEFAULT_MAX_TOKENS

    def apply_env(self):
        """Обновляем конфиг из переменных окружения после чтения .env."""
        self.model_path = os.getenv("LLM_MODEL_PATH", self.model_path)
        self.n_ctx = int(os.getenv("LLM_CTX", str(self.n_ctx)))
        self.n_threads = int(os.getenv("LLM_THREADS", str(self.n_threads)))
        self.n_gpu_layers = int(os.getenv("LLM_GPU_LAYERS", str(self.n_gpu_layers)))
        self.default_max_tokens = int(os.getenv("LLM_MAX_TOKENS", str(self.default_max_tokens)))
        
    async def load_model(self):
        """Загрузка модели (выберите одну из вариантов)"""
        
        if self.model_type == "llama" and LLAMA_AVAILABLE:
            # Скачайте модель с HuggingFace в формате GGUF
            # Например: https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF
            model_path = self.model_path
            print(f"[LLM] init with model_path={model_path}, ctx={self.n_ctx}, threads={self.n_threads}, gpu_layers={self.n_gpu_layers}, max_tokens={self.default_max_tokens}")
            
            if not Path(model_path).exists():
                print(f"Модель не найдена по пути: {model_path}")
                print("Скачайте модель командой:")
                print("wget https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q4_K_M.gguf -O models/")
                return False
            
            # Настройки для M1
            self.model = Llama(
                model_path=model_path,
                n_ctx=self.n_ctx,  # Размер контекста (короче -> быстрее)
                n_threads=self.n_threads,  # Количество потоков CPU
                n_gpu_layers=self.n_gpu_layers,  # Использовать GPU слои (Metal для M1)
                verbose=True
            )
            print("✅ Модель Llama.cpp загружена")
            return True
            
        elif self.model_type == "transformers" and TRANSFORMERS_AVAILABLE:
            # Для более новых Mac с достаточной памятью
            model_name = "mistralai/Mistral-7B-Instruct-v0.2"
            
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float16,
                device_map="auto",
                load_in_4bit=True  # Квантование 4-bit для экономии памяти
            )
            print("✅ Модель Transformers загружена")
            return True
            
        else:
            print("❌ Не удалось загрузить модель. Установите зависимости:")
            print("pip install llama-cpp-python  # для GGUF моделей")
            print("pip install transformers torch accelerate  # для прямого запуска")
            return False
    
    async def generate_response(self, messages: List[Dict], max_tokens=None) -> str:
        """Генерация ответа"""
        
        if not self.model:
            return "Модель не загружена. Проверьте настройки."
        
        # Используем дефолт из окружения, если параметр не передан
        if max_tokens is None:
            max_tokens = self.default_max_tokens
        
        try:
            if self.model_type == "llama":
                # Используем chat_completion с автоформатированием (mistral-instruct и т.п.)
                start = time.time()

                def _call_model():
                    print(f"[LLM] start generation, max_tokens={max_tokens}")
                    return self.model.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.65,
                        top_p=0.85,
                        repeat_penalty=1.05,
                    )

                output = await asyncio.to_thread(_call_model)
                duration = time.time() - start
                usage = output.get("usage", {})
                print(f"[LLM] generation done in {duration:.2f}s, usage={usage}")
                
                return output['choices'][0]['message']['content'].strip()
                
            elif self.model_type == "transformers":
                # Форматируем для transformers
                prompt = self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )
                
                inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
                
                with torch.no_grad():
                    outputs = self.model.generate(
                        **inputs,
                        max_new_tokens=max_tokens,
                        temperature=0.7,
                        do_sample=True
                    )
                
                return self.tokenizer.decode(outputs[0], skip_special_tokens=True)
                
        except Exception as e:
            return f"Ошибка генерации: {str(e)}"
    
    def _format_prompt_llama(self, messages: List[Dict]) -> str:
        """Форматирование промпта без лишних <s>, ближе к llama-2/chat."""
        if not messages:
            return ""

        system_prompt = ""
        convo = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system" and not system_prompt:
                system_prompt = content
            else:
                convo.append({"role": role, "content": content})

        pairs = []
        current_user = None
        for msg in convo:
            if msg["role"] == "user":
                current_user = msg["content"]
            elif msg["role"] == "assistant" and current_user is not None:
                pairs.append((current_user, msg["content"]))
                current_user = None
        if current_user is not None:
            pairs.append((current_user, ""))

        if not pairs:
            return ""

        segments = []
        for idx, (user_text, assistant_text) in enumerate(pairs):
            if idx == 0 and system_prompt:
                segments.append(f"[INST] <<SYS>> {system_prompt} <</SYS>>\n\n{user_text} [/INST] {assistant_text}")
            else:
                segments.append(f"[INST] {user_text} [/INST] {assistant_text}")

        # Если последний ассистент пустой, оставляем открытым для генерации
        if pairs and pairs[-1][1] == "":
            if segments[-1].endswith(" "):
                segments[-1] = segments[-1].rstrip()

        return " ".join(segments)
    
    async def get_chat_history(self, user_id: int, limit=20):
        """Получение истории чата из БД"""
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        
        c.execute('''
            SELECT role, content FROM messages 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit))
        
        rows = c.fetchall()
        conn.close()
        
        # Преобразуем в нужный формат
        history = []
        for role, content in reversed(rows):  # Переворачиваем для хронологического порядка
            history.append({"role": role, "content": content})
        
        return history

# Интеграция с ботом
llm_service = LocalLLM(model_type="llama")  # или "transformers"

# В обработчике сообщений вместо echo:
"""
history = await llm_service.get_chat_history(user_id)
history.append({"role": "user", "content": message.text})

response = await llm_service.generate_response(history)
await message.answer(response)
"""
