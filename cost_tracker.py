# ============================================================
#  cost_tracker.py — учёт расходов на облачные модели (DeepSeek и т.п.)
#  Считает потраченную сумму по токенам (вход/выход), показывает остаток
#  от стартового баланса и переводит её в выбранную валюту.
#  Настройки живут в cost_settings.json рядом с программой.
#
#  Работает только для профилей с type == "cloud" -- локальные движки
#  (Ollama, LM Studio) бесплатны и сюда не попадают.
# ============================================================

import json
import os

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
COST_FILE    = os.path.join(BASE_DIR, "cost_settings.json")

CURRENCIES = ["CNY", "RUB", "USD", "EUR"]

CURRENCY_SYMBOLS = {
    "CNY": "¥",
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
}

CURRENCY_NAMES = {
    "CNY": "Юани",
    "RUB": "Рубли",
    "USD": "Доллары",
    "EUR": "Евро",
}

# Курсы "сколько единиц валюты за 1 юань" -- ставятся вручную пользователем
# в настройках, так как зависят от того, по какому курсу он реально
# пополнял баланс DeepSeek. Это ориентировочные значения на момент
# написания, нужны только как стартовая точка.
DEFAULT_RATES_PER_CNY = {
    "CNY": 1.0,
    "RUB": 10.78,
    "USD": 0.1477,
    "EUR": 0.1287,
}

# Официальные цены DeepSeek (платформа считает в юанях, см.
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing, проверено в июне 2026):
#   deepseek-v4-flash: 1 юань / 1M входных токенов (промах кэша), 2 юаня / 1M выходных
#                       (0.02 юаня при попадании в кэш -- кэш-хиты в логах ассистента
#                       не различаются, поэтому здесь считаем по полной цене промаха)
#   deepseek-v4-pro:    3 юаня / 1M входных, 6 юаней / 1M выходных (со скидкой,
#                       действующей и после её формального окончания 31.05.2026 --
#                       это и есть итоговая цена 1/4 от полной)
# Это дефолт -- пользователь может поправить в разделе настроек "Расходы",
# если DeepSeek изменит тарифы.
DEEPSEEK_DEFAULT_PRICES_CNY_PER_1M = {
    "deepseek-v4-flash": {"input": 1.0, "output": 2.0},
    "deepseek-chat":     {"input": 1.0, "output": 2.0},
    "deepseek-reasoner": {"input": 1.0, "output": 2.0},
    "deepseek-v4-pro":   {"input": 3.0, "output": 6.0},
}

# Если модель не из списка выше (любой другой облачный провайдер) --
# используется этот дефолт-профиль цен, который пользователь правит вручную
# под свой реальный тариф в разделе настроек "Расходы".
GENERIC_CLOUD_DEFAULT_PRICES_CNY_PER_1M = {"input": 1.0, "output": 2.0}


def _default_settings() -> dict:
    return {
        "starting_balance_cny": 32.95,   # стартовый баланс, который ввёл пользователь
        "display_currency": "CNY",
        "rates_per_cny": dict(DEFAULT_RATES_PER_CNY),
        # Цены по model id (юаней за 1M токенов) -- ключ "*" -- цена по умолчанию
        # для моделей, которых нет в явном списке.
        "prices_cny_per_1m": dict(DEEPSEEK_DEFAULT_PRICES_CNY_PER_1M),
        "default_price_cny_per_1m": dict(GENERIC_CLOUD_DEFAULT_PRICES_CNY_PER_1M),
        # Накопленные за всё время токены по модели: {model_id: {"input": N, "output": N}}
        "lifetime_usage": {},
    }


def load_settings() -> dict:
    """Загружает cost_settings.json. Создаёт файл с дефолтами при первом запуске."""
    if not os.path.exists(COST_FILE):
        data = _default_settings()
        save_settings(data)
        return data

    try:
        with open(COST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("cost_settings.json повреждён")
        defaults = _default_settings()
        for key, val in defaults.items():
            data.setdefault(key, val)
        return data
    except Exception as e:
        print(f"[Cost] ⚠️ Не удалось прочитать cost_settings.json ({e}), создаю заново.")
        data = _default_settings()
        save_settings(data)
        return data


def save_settings(data: dict):
    with open(COST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_starting_balance_cny() -> float:
    return float(load_settings()["starting_balance_cny"])


def set_starting_balance_cny(value: float):
    data = load_settings()
    data["starting_balance_cny"] = float(value)
    save_settings(data)


def get_display_currency() -> str:
    return load_settings()["display_currency"]


def set_display_currency(code: str):
    if code not in CURRENCIES:
        raise ValueError(f"Неизвестная валюта: {code}")
    data = load_settings()
    data["display_currency"] = code
    save_settings(data)


def get_rate_per_cny(code: str) -> float:
    data = load_settings()
    return float(data["rates_per_cny"].get(code, DEFAULT_RATES_PER_CNY.get(code, 1.0)))


def set_rate_per_cny(code: str, value: float):
    if code not in CURRENCIES:
        raise ValueError(f"Неизвестная валюта: {code}")
    data = load_settings()
    data["rates_per_cny"][code] = float(value)
    save_settings(data)


def cny_to_currency(amount_cny: float, code: str) -> float:
    if code == "CNY":
        return amount_cny
    data = load_settings()
    rate = float(data["rates_per_cny"].get(code, DEFAULT_RATES_PER_CNY.get(code, 1.0)))
    return amount_cny * rate


def get_price_for_model(model_id: str) -> dict:
    """Возвращает {"input": цена, "output": цена} в юанях за 1M токенов
    для данного id модели. Если модель не известна явно -- берёт
    общий дефолт для облачных моделей (настраиваемый)."""
    data = load_settings()
    prices = data["prices_cny_per_1m"]
    if model_id in prices:
        return prices[model_id]
    return data["default_price_cny_per_1m"]


def set_price_for_model(model_id: str, input_price: float, output_price: float):
    data = load_settings()
    data["prices_cny_per_1m"][model_id] = {
        "input": float(input_price),
        "output": float(output_price),
    }
    save_settings(data)


def calc_cost_cny(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Считает стоимость в юанях по числу входных/выходных токенов
    для конкретной модели."""
    price = get_price_for_model(model_id)
    cost = (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]
    return cost


def add_usage(model_id: str, input_tokens: int, output_tokens: int):
    """Добавляет токены к накопленному за всё время использованию модели --
    это то, что используется для расчёта остатка баланса между запусками
    программы (а не только в рамках одной сессии)."""
    data = load_settings()
    usage = data["lifetime_usage"].setdefault(model_id, {"input": 0, "output": 0})
    usage["input"] += int(input_tokens)
    usage["output"] += int(output_tokens)
    save_settings(data)


def get_lifetime_spent_cny() -> float:
    """Суммарные расходы в юанях по всем моделям за всё время."""
    data = load_settings()
    total = 0.0
    for model_id, usage in data["lifetime_usage"].items():
        total += calc_cost_cny(model_id, usage.get("input", 0), usage.get("output", 0))
    return total


def get_remaining_balance_cny() -> float:
    return get_starting_balance_cny() - get_lifetime_spent_cny()


def format_amount(amount: float, code: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(code, "")
    # Юани/рубли/доллары/евро -- везде достаточно 2 знаков после запятой,
    # но при очень маленьких суммах (доли копейки на дешёвых моделях)
    # показываем больше знаков, чтобы расход не округлялся в "0.00"
    if abs(amount) < 0.01 and amount != 0:
        return f"{amount:.4f} {symbol}"
    return f"{amount:.2f} {symbol}"


def reset_usage():
    """Сбрасывает накопленную статистику расходов (например, после
    реального пополнения баланса на платформе DeepSeek)."""
    data = load_settings()
    data["lifetime_usage"] = {}
    save_settings(data)
