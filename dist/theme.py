# ============================================================
#  theme.py — хранение и применение акцентного цвета интерфейса
#  Настройка живёт в launcher_settings.json рядом с программой,
#  редактируется через раздел "Внешний вид" в окне настроек.
# ============================================================

import json
import os
import colorsys

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "launcher_settings.json")

DEFAULT_ACCENT = "#7c5cfc"  # исходный фиолетовый

# Кастомизация вкладки "Чат" (пузыри сообщений) -- касается только её,
# журнал ([ЖУРНАЛ]) использует обычный tk.Text и этими настройками не управляется.
DEFAULT_CHAT_BUBBLE_RADIUS = 14   # px, закруглённость углов пузыря
DEFAULT_CHAT_FONT_SCALE    = 1.0  # множитель размера шрифта сообщений

# Верхние/нижние границы для слайдеров в разделе "Внешний вид".
CHAT_BUBBLE_RADIUS_MIN = 0
CHAT_BUBBLE_RADIUS_MAX = 40   # было 30
CHAT_FONT_SCALE_MIN    = 0.7
CHAT_FONT_SCALE_MAX    = 2.4  # было 1.8

# Размер круглой аватарки активной модели в шапке главного окна.
DEFAULT_HEADER_AVATAR_SIZE = 44   # px
HEADER_AVATAR_SIZE_MIN = 16
HEADER_AVATAR_SIZE_MAX = 150

# Версия программы -- показывается в разделе настроек "О программе".
APP_VERSION = "1.2"


def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(v)))


def hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError(f"Некорректный hex-цвет: {hex_color}")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b) -> str:
    return "#{:02x}{:02x}{:02x}".format(_clamp(r), _clamp(g), _clamp(b))


def is_valid_hex_color(value: str) -> bool:
    try:
        hex_to_rgb(value)
        return True
    except Exception:
        return False


def dim(hex_color: str, factor: float = 0.42) -> str:
    """
    Возвращает затемнённый/приглушённый вариант цвета — используется там,
    где раньше был ACCENT_DIM (фон активных элементов, hover, выделение).
    Смешивает цвет с фоном тёмной темы, а не просто умножает яркость,
    чтобы приглушённый вариант не уходил в грязно-серый на ярких цветах.
    """
    r, g, b = hex_to_rgb(hex_color)
    bg_r, bg_g, bg_b = hex_to_rgb("#14141a")
    nr = bg_r + (r - bg_r) * factor
    ng = bg_g + (g - bg_g) * factor
    nb = bg_b + (b - bg_b) * factor
    return rgb_to_hex(nr, ng, nb)


def _default_settings() -> dict:
    return {
        "accent_color": DEFAULT_ACCENT,
        "chat_bubble_radius": DEFAULT_CHAT_BUBBLE_RADIUS,
        "chat_font_scale": DEFAULT_CHAT_FONT_SCALE,
        "header_avatar_size": DEFAULT_HEADER_AVATAR_SIZE,
    }


def load_settings() -> dict:
    """Загружает launcher_settings.json. Создаёт файл с дефолтами при первом запуске."""
    if not os.path.exists(SETTINGS_FILE):
        data = _default_settings()
        save_settings(data)
        return data

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("launcher_settings.json повреждён")
        data.setdefault("accent_color", DEFAULT_ACCENT)
        if not is_valid_hex_color(data["accent_color"]):
            data["accent_color"] = DEFAULT_ACCENT
        data.setdefault("chat_bubble_radius", DEFAULT_CHAT_BUBBLE_RADIUS)
        data.setdefault("chat_font_scale", DEFAULT_CHAT_FONT_SCALE)
        try:
            data["chat_bubble_radius"] = max(CHAT_BUBBLE_RADIUS_MIN, min(CHAT_BUBBLE_RADIUS_MAX, int(data["chat_bubble_radius"])))
        except Exception:
            data["chat_bubble_radius"] = DEFAULT_CHAT_BUBBLE_RADIUS
        try:
            data["chat_font_scale"] = max(CHAT_FONT_SCALE_MIN, min(CHAT_FONT_SCALE_MAX, float(data["chat_font_scale"])))
        except Exception:
            data["chat_font_scale"] = DEFAULT_CHAT_FONT_SCALE
        data.setdefault("header_avatar_size", DEFAULT_HEADER_AVATAR_SIZE)
        try:
            data["header_avatar_size"] = max(HEADER_AVATAR_SIZE_MIN, min(HEADER_AVATAR_SIZE_MAX, int(data["header_avatar_size"])))
        except Exception:
            data["header_avatar_size"] = DEFAULT_HEADER_AVATAR_SIZE
        return data
    except Exception as e:
        print(f"[Theme] ⚠️ Не удалось прочитать launcher_settings.json ({e}), создаю заново.")
        data = _default_settings()
        save_settings(data)
        return data


def save_settings(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_accent_color() -> str:
    return load_settings()["accent_color"]


def set_accent_color(hex_color: str):
    if not is_valid_hex_color(hex_color):
        raise ValueError(f"Некорректный hex-цвет: {hex_color}")
    data = load_settings()
    data["accent_color"] = hex_color
    save_settings(data)


def get_chat_bubble_radius() -> int:
    return load_settings()["chat_bubble_radius"]


def set_chat_bubble_radius(radius: int):
    radius = max(CHAT_BUBBLE_RADIUS_MIN, min(CHAT_BUBBLE_RADIUS_MAX, int(radius)))
    data = load_settings()
    data["chat_bubble_radius"] = radius
    save_settings(data)


def get_chat_font_scale() -> float:
    return load_settings()["chat_font_scale"]


def set_chat_font_scale(scale: float):
    scale = max(CHAT_FONT_SCALE_MIN, min(CHAT_FONT_SCALE_MAX, float(scale)))
    data = load_settings()
    data["chat_font_scale"] = scale
    save_settings(data)


def get_header_avatar_size() -> int:
    return load_settings()["header_avatar_size"]


def set_header_avatar_size(size: int):
    size = max(HEADER_AVATAR_SIZE_MIN, min(HEADER_AVATAR_SIZE_MAX, int(size)))
    data = load_settings()
    data["header_avatar_size"] = size
    save_settings(data)


def hsv_to_hex(h, s, v) -> str:
    """h, s, v в диапазоне [0, 1]."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_to_hex(r * 255, g * 255, b * 255)


def hex_to_hsv(hex_color: str):
    r, g, b = hex_to_rgb(hex_color)
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
