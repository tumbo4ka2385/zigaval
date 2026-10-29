# ============================================================
#  vision_profiles.py — хранение и управление профилями Vision-моделей
#  Профили живут в vision_models.json рядом с программой, редактируются
#  через раздел "Vision" в окне настроек лаунчера.
#
#  Типы профилей:
#    gguf     — локальный .gguf через llama-cpp-python (Moondream и т.п.)
#    lmstudio — vision-модель, загруженная в LM Studio (как обычный LLM)
#    cloud    — облачный Moondream API (api.moondream.ai) с ключом
# ============================================================

import json
import os
import uuid

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PROFILES_FILE = os.path.join(BASE_DIR, "vision_models.json")

DEFAULT_VISION_PROMPT = "Подробно опиши что изображено на этом скриншоте на русском языке."


def _default_profiles() -> dict:
    """Профили по умолчанию — создаются при первом запуске, если vision_models.json ещё нет."""
    gguf_id = str(uuid.uuid4())
    return {
        "active_id": gguf_id,
        "profiles": [
            {
                "id":          gguf_id,
                "name":        "Moondream (локально, .gguf)",
                "type":        "gguf",
                # Папка с двумя файлами модели: *-text-model-*.gguf и *-mmproj-*.gguf.
                # Можно указать путь к конкретному text-model файлу -- mmproj
                # ищется автоматически рядом по маске *mmproj*.gguf.
                "model_path":  "models/moondream2-text-model-f16.gguf",
                "mmproj_path": "",  # пусто -- автопоиск рядом с model_path
                "base_url":    "",
                "api_key":     "",
                "model":       "",
                "prompt":      DEFAULT_VISION_PROMPT,
                "n_ctx":       2048,
                "n_gpu_layers": -1,  # -1 = выгрузить все слои на GPU, если есть
            },
            {
                "id":          str(uuid.uuid4()),
                "name":        "LM Studio (vision-модель)",
                "type":        "lmstudio",
                "model_path":  "",
                "mmproj_path": "",
                "base_url":    "http://127.0.0.1:1234",
                "api_key":     "",
                "model":       "llava-llama-3-8b-v1_1",
                "prompt":      DEFAULT_VISION_PROMPT,
                "n_ctx":       2048,
                "n_gpu_layers": -1,
            },
            {
                "id":          str(uuid.uuid4()),
                "name":        "Moondream Cloud (облако)",
                "type":        "cloud",
                "model_path":  "",
                "mmproj_path": "",
                "base_url":    "https://api.moondream.ai/v1",
                "api_key":     "",
                "model":       "moondream-2b",
                "prompt":      DEFAULT_VISION_PROMPT,
                "n_ctx":       2048,
                "n_gpu_layers": -1,
            },
        ],
    }


def load_profiles() -> dict:
    """Загружает vision_models.json. Создаёт файл с дефолтами при первом запуске."""
    if not os.path.exists(PROFILES_FILE):
        data = _default_profiles()
        save_profiles(data)
        return data

    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "profiles" not in data or not data["profiles"]:
            raise ValueError("Пустой или повреждённый vision_models.json")

        # Миграция: добиваем недостающие поля у старых профилей дефолтами,
        # чтобы открытие формы редактирования не падало на KeyError.
        defaults = {
            "model_path": "", "mmproj_path": "", "base_url": "", "api_key": "",
            "model": "", "prompt": DEFAULT_VISION_PROMPT, "n_ctx": 2048, "n_gpu_layers": -1,
        }
        changed = False
        for p in data["profiles"]:
            for key, val in defaults.items():
                if key not in p:
                    p[key] = val
                    changed = True
        if changed:
            save_profiles(data)

        return data
    except Exception as e:
        print(f"[VisionProfiles] [WARN] Не удалось прочитать vision_models.json ({e}), создаю заново.")
        data = _default_profiles()
        save_profiles(data)
        return data


def save_profiles(data: dict):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_profile() -> dict:
    data = load_profiles()
    active_id = data.get("active_id")
    for p in data["profiles"]:
        if p["id"] == active_id:
            return p
    return data["profiles"][0]


def set_active_profile(profile_id: str) -> bool:
    data = load_profiles()
    if any(p["id"] == profile_id for p in data["profiles"]):
        data["active_id"] = profile_id
        save_profiles(data)
        return True
    return False


def add_profile(profile: dict) -> str:
    """Добавляет новый профиль. Возвращает его id."""
    data = load_profiles()
    profile = dict(profile)
    profile["id"] = str(uuid.uuid4())
    profile.setdefault("model_path", "")
    profile.setdefault("mmproj_path", "")
    profile.setdefault("base_url", "")
    profile.setdefault("api_key", "")
    profile.setdefault("model", "")
    profile.setdefault("prompt", DEFAULT_VISION_PROMPT)
    profile.setdefault("n_ctx", 2048)
    profile.setdefault("n_gpu_layers", -1)
    data["profiles"].append(profile)
    save_profiles(data)
    return profile["id"]


def update_profile(profile_id: str, updates: dict) -> bool:
    data = load_profiles()
    for p in data["profiles"]:
        if p["id"] == profile_id:
            p.update(updates)
            save_profiles(data)
            return True
    return False


def delete_profile(profile_id: str) -> bool:
    data = load_profiles()
    if len(data["profiles"]) <= 1:
        return False  # нельзя удалить последний профиль
    data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
    if data["active_id"] == profile_id:
        data["active_id"] = data["profiles"][0]["id"]
    save_profiles(data)
    return True


def list_profiles() -> list:
    return load_profiles()["profiles"]


def find_mmproj_path(model_path: str, explicit_mmproj: str = "") -> str:
    """
    Находит файл mmproj (vision projector) рядом с основной .gguf моделью.
    Если explicit_mmproj задан и существует -- используется он.
    Иначе ищется любой файл с "mmproj" в названии в той же папке.
    """
    if explicit_mmproj and os.path.exists(explicit_mmproj):
        return explicit_mmproj

    folder = os.path.dirname(model_path) or "."
    if not os.path.isdir(folder):
        return ""

    for fname in os.listdir(folder):
        if "mmproj" in fname.lower() and fname.lower().endswith(".gguf"):
            return os.path.join(folder, fname)
    return ""
