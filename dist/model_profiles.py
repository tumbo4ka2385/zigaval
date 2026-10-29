# ============================================================
#  model_profiles.py — хранение и управление профилями моделей
#  Профили живут в models.json рядом с программой, редактируются
#  через GUI лаунчера (или вручную в файле).
#
#  Типы профилей:
#    cloud    — любой OpenAI-совместимый API (DeepSeek, OpenAI, и т.д.)
#    ollama   — локальный Ollama (http://localhost:11434)
#    lmstudio — локальный LM Studio (http://127.0.0.1:1234)
# ============================================================

import json
import os
import uuid

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PROFILES_FILE = os.path.join(BASE_DIR, "models.json")

DEFAULT_SYSTEM_PROMPT = "Ты умный голосовой ассистент. Отвечай кратко и по делу."


def _default_profiles() -> dict:
    """Профили по умолчанию — создаются при первом запуске, если models.json ещё нет."""
    deepseek_id = str(uuid.uuid4())
    return {
        "active_id": deepseek_id,
        "profiles": [
            {
                "id":           deepseek_id,
                "name":         "DeepSeek (облако)",
                "type":         "cloud",
                "base_url":     "https://api.deepseek.com",
                "api_key":      "",
                "model":        "deepseek-chat",
                "max_tokens":   500,
                "temperature":  0.7,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
            {
                "id":           str(uuid.uuid4()),
                "name":         "LM Studio (локально)",
                "type":         "lmstudio",
                "base_url":     "http://127.0.0.1:1234",
                "api_key":      "",
                "model":        "local-model",
                "max_tokens":   500,
                "temperature":  0.7,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
            {
                "id":           str(uuid.uuid4()),
                "name":         "Ollama (локально)",
                "type":         "ollama",
                "base_url":     "http://localhost:11434",
                "api_key":      "",
                "model":        "llama3",
                "max_tokens":   500,
                "temperature":  0.7,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
            },
        ],
    }


def _migrate_legacy_config(data: dict) -> dict:
    """Если есть старый config.py с DEEPSEEK_* — переносим его как первый профиль."""
    try:
        import config
        if hasattr(config, "DEEPSEEK_API_KEY") and config.DEEPSEEK_API_KEY:
            legacy_id = str(uuid.uuid4())
            data["profiles"].insert(0, {
                "id":           legacy_id,
                "name":         "DeepSeek (из старого конфига)",
                "type":         "cloud",
                "base_url":     getattr(config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                "api_key":      config.DEEPSEEK_API_KEY,
                "model":        getattr(config, "DEEPSEEK_MODEL", "deepseek-chat"),
                "max_tokens":   getattr(config, "LM_MAX_TOKENS", 500),
                "temperature":  getattr(config, "LM_TEMPERATURE", 0.7),
                "system_prompt": getattr(config, "LM_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
            })
            data["active_id"] = legacy_id
    except Exception:
        pass
    return data


def load_profiles() -> dict:
    """Загружает models.json. Создаёт файл с дефолтами при первом запуске."""
    if not os.path.exists(PROFILES_FILE):
        data = _default_profiles()
        data = _migrate_legacy_config(data)
        save_profiles(data)
        return data

    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "profiles" not in data or not data["profiles"]:
            raise ValueError("Пустой или повреждённый models.json")
        return data
    except Exception as e:
        print(f"[Profiles] ⚠️ Не удалось прочитать models.json ({e}), создаю заново.")
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
    # active_id битый — берём первый профиль
    return data["profiles"][0]


def set_active_profile(profile_id: str):
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
    profile.setdefault("max_tokens", 500)
    profile.setdefault("temperature", 0.7)
    profile.setdefault("system_prompt", DEFAULT_SYSTEM_PROMPT)
    profile.setdefault("api_key", "")
    data["profiles"].append(profile)
    save_profiles(data)
    return profile["id"]


def update_profile(profile_id: str, updates: dict):
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


def chat_endpoint(profile: dict) -> str:
    """Возвращает полный URL для chat completions в зависимости от типа профиля."""
    base = profile["base_url"].rstrip("/")
    if profile["type"] == "ollama":
        return f"{base}/v1/chat/completions"   # Ollama поддерживает OpenAI-совместимый роут
    return f"{base}/v1/chat/completions"        # cloud и lmstudio тоже OpenAI-совместимые


def models_endpoint(base_url: str) -> str:
    """URL для получения списка загруженных моделей (OpenAI-совместимый /v1/models)."""
    return f"{base_url.rstrip('/')}/v1/models"


def fetch_available_models(base_url: str, api_key: str = "", timeout: float = 5.0) -> list:
    """
    Запрашивает список моделей у LM Studio / Ollama / любого OpenAI-совместимого API.
    Возвращает список id моделей (строки). Бросает исключение при ошибке сети --
    вызывающий код должен сам ловить и показывать сообщение пользователю.
    """
    import requests
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.get(models_endpoint(base_url), headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    # OpenAI-совместимый формат: {"data": [{"id": "..."}]}
    items = data.get("data", data if isinstance(data, list) else [])
    ids = [item.get("id") or item.get("name") for item in items if isinstance(item, dict)]
    return [i for i in ids if i]
