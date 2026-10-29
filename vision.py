# ============================================================
#  vision.py -- анализ изображений активной Vision-моделью
#  Поддерживает три типа бэкенда (переключаются в Настройки -> Vision):
#    gguf     -- локальный .gguf через llama-cpp-python (Moondream и т.п.)
#    lmstudio -- vision-модель, загруженная в LM Studio
#    cloud    -- облачный Moondream API (api.moondream.ai) с ключом
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import os
import base64
import io
import numpy as np
import vision_profiles as vp


class Vision:
    def __init__(self):
        self.profile = vp.get_active_profile()
        self._gguf_handle = None       # ленивая загрузка -- модель грузится при первом запросе
        self._gguf_loaded_path = None  # путь модели, под который сейчас поднят _gguf_handle
        print(f"[Vision] Активен профиль: {self.profile['name']} ({self.profile['type']})")

    def reload_profile(self):
        """Перечитывает активный профиль из vision_models.json -- вызывать
        после смены Vision-модели в Настройках."""
        new_profile = vp.get_active_profile()
        if new_profile["id"] != self.profile.get("id"):
            self._gguf_handle = None  # сменили профиль -- выгружаем старую gguf-модель из памяти
            self._gguf_loaded_path = None
        self.profile = new_profile
        print(f"[Vision] Профиль переключён: {self.profile['name']} ({self.profile['type']})")

    # -- Публичный API (используется main.py) --------------------

    def screenshot_and_describe(self, extra_prompt: str = "") -> str | None:
        """Делает скриншот экрана и описывает его активной Vision-моделью."""
        frame = self._grab_screen()
        if frame is None:
            return None

        from PIL import Image
        img = Image.fromarray(frame) if isinstance(frame, np.ndarray) else frame

        if img.width > 1280:
            ratio = 1280 / img.width
            img = img.resize((1280, int(img.height * ratio)), Image.LANCZOS)

        question = extra_prompt if extra_prompt else self.profile.get("prompt", vp.DEFAULT_VISION_PROMPT)
        print(f"[Vision] Анализирую через {self.profile['name']}...")

        return self._describe_image(img, question)

    def describe(self, image_path: str, extra_prompt: str = "") -> str | None:
        """Анализирует картинку из файла активной Vision-моделью."""
        from PIL import Image
        try:
            img = Image.open(image_path).convert("RGB")
        except Exception as e:
            print(f"[Vision] [ERR] Не удалось открыть изображение: {e}")
            return None

        question = extra_prompt if extra_prompt else self.profile.get("prompt", vp.DEFAULT_VISION_PROMPT)
        return self._describe_image(img, question)

    # -- Маршрутизация по типу профиля ---------------------------

    def _describe_image(self, img, question: str) -> str | None:
        ptype = self.profile.get("type")
        try:
            if ptype == "gguf":
                return self._describe_gguf(img, question)
            elif ptype == "lmstudio":
                return self._describe_openai_compatible(img, question)
            elif ptype == "cloud":
                return self._describe_cloud(img, question)
            else:
                print(f"[Vision] [ERR] Неизвестный тип профиля: {ptype}")
                return None
        except Exception as e:
            print(f"[Vision] [ERR] Ошибка анализа ({self.profile['name']}): {e}")
            return None

    # -- gguf (llama-cpp-python, локально) ------------------------

    def _ensure_gguf_loaded(self):
        model_path = self.profile.get("model_path", "")
        if not model_path or not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Файл модели не найден: {model_path}\n"
                f"Укажи правильный путь в Настройки -> Vision -> Изменить профиль."
            )

        if self._gguf_handle is not None and self._gguf_loaded_path == model_path:
            return  # уже загружена эта же модель

        mmproj_path = vp.find_mmproj_path(model_path, self.profile.get("mmproj_path", ""))
        if not mmproj_path or not os.path.exists(mmproj_path):
            raise FileNotFoundError(
                "Не найден mmproj-файл (vision projector) рядом с моделью.\n"
                "Положи файл с 'mmproj' в названии (например moondream2-mmproj-f16.gguf) "
                "в ту же папку, либо укажи путь явно в профиле."
            )

        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import MoondreamChatHandler
        except ImportError:
            raise ImportError(
                "Для локальных .gguf Vision-моделей нужна библиотека llama-cpp-python.\n"
                "Установи: pip install llama-cpp-python"
            )

        print(f"[Vision] Загружаю gguf-модель: {model_path}")
        print(f"[Vision] mmproj: {mmproj_path}")

        chat_handler = MoondreamChatHandler(clip_model_path=mmproj_path, verbose=False)
        self._gguf_handle = Llama(
            model_path=model_path,
            chat_handler=chat_handler,
            n_ctx=self.profile.get("n_ctx", 2048),
            n_gpu_layers=self.profile.get("n_gpu_layers", -1),
            verbose=False,
        )
        self._gguf_loaded_path = model_path
        print("[Vision] gguf-модель загружена OK")

    def _describe_gguf(self, img, question: str) -> str:
        self._ensure_gguf_loaded()

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        data_url = f"data:image/png;base64,{b64}"

        result = self._gguf_handle.create_chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": question},
                ],
            }],
            max_tokens=800,
            temperature=0.3,
        )
        return result["choices"][0]["message"]["content"].strip()

    # -- lmstudio (OpenAI-совместимый /v1/chat/completions) -------

    def _describe_openai_compatible(self, img, question: str) -> str:
        import requests

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        base_url = self.profile.get("base_url", "http://127.0.0.1:1234").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.profile.get("api_key"):
            headers["Authorization"] = f"Bearer {self.profile['api_key']}"

        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json={
                "model": self.profile.get("model", ""),
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": question},
                    ],
                }],
                "max_tokens": 800,
                "temperature": 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    # -- cloud (Moondream Cloud API) -------------------------------

    def _describe_cloud(self, img, question: str) -> str:
        import requests

        if not self.profile.get("api_key"):
            raise ValueError(
                "Для облачного Moondream нужен API-ключ.\n"
                "Получи его на moondream.ai и впиши в Настройки -> Vision -> Изменить профиль."
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        base_url = self.profile.get("base_url", "https://api.moondream.ai/v1").rstrip("/")

        resp = requests.post(
            f"{base_url}/query",
            headers={
                "X-Moondream-Auth": self.profile["api_key"],
                "Content-Type": "application/json",
            },
            json={
                "image_url": f"data:image/png;base64,{b64}",
                "question": question,
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("answer", "").strip()

    # -- Захват экрана ---------------------------------------------

    def _grab_screen(self):
        """Захват экрана -- сначала пробует dxcam (быстрее на Windows), потом PIL."""
        try:
            import dxcam
            cam = dxcam.create(output_color="RGB")
            frame = cam.grab()
            del cam
            if frame is not None:
                return frame
        except Exception:
            pass

        try:
            from PIL import ImageGrab
            return ImageGrab.grab()
        except Exception as e:
            print(f"[Vision] [ERR] Не смог захватить экран: {e}")
            return None
