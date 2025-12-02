"""
Stable Diffusion локально на M1
Установите сначала: pip install diffusers accelerate safetensors
"""

import asyncio
from pathlib import Path
import logging
from typing import Optional
import sqlite3

try:
    import torch
    from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler
    from PIL import Image
    import io
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

class StableDiffusionLocal:
    def __init__(self, model_name="runwayml/stable-diffusion-v1-5"):
        self.model_name = model_name
        self.pipe = None
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        
    async def load_model(self):
        """Загрузка модели Stable Diffusion"""
        if not SD_AVAILABLE:
            print("❌ Diffusers не установлен: pip install diffusers")
            return False
        
        try:
            print(f"🔄 Загружаем модель {self.model_name} на {self.device}...")
            
            # Используем float32 для MPS (Metal Performance Shaders)
            dtype = torch.float32 if self.device == "mps" else torch.float16
            
            self.pipe = StableDiffusionPipeline.from_pretrained(
                self.model_name,
                torch_dtype=dtype,
                safety_checker=None,  # Отключаем встроенный safety checker для NSFW
                requires_safety_checker=False
            )
            
            # Оптимизации для M1
            self.pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                self.pipe.scheduler.config
            )
            
            if self.device == "mps":
                # MPS-specific optimizations
                self.pipe.enable_attention_slicing()
            
            self.pipe = self.pipe.to(self.device)
            
            print(f"✅ Модель загружена на {self.device}")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка загрузки модели: {e}")
            return False
    
    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        steps: int = 20,
        height: int = 512,
        width: int = 512,
        guidance_scale: float = 7.5
    ) -> Optional[bytes]:
        """Генерация изображения"""
        if not self.pipe:
            print("Модель не загружена")
            return None
        
        try:
            # Генерация
            with torch.autocast(self.device):
                image = self.pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    num_inference_steps=steps,
                    height=height,
                    width=width,
                    guidance_scale=guidance_scale
                ).images[0]
            
            # Конвертируем в bytes для отправки в Telegram
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            return img_byte_arr.getvalue()
            
        except Exception as e:
            logging.error(f"Ошибка генерации изображения: {e}")
            return None
    
    async def generate_nsfw_image(self, prompt: str) -> Optional[bytes]:
        """Генерация NSFW изображения"""
        # Добавляем негативный промпт для улучшения качества
        negative_prompt = "ugly, deformed, disfigured, poor details, bad anatomy"
        
        return await self.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=25,
            guidance_scale=7.0
        )

# Инициализация
sd_service = StableDiffusionLocal()

# Для быстрой генерации используйте модель-тренд:
# sd_service_fast = StableDiffusionLocal("stabilityai/sd-turbo")