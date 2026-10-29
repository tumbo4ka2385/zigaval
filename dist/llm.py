# ============================================================
#  llm.py - общение с активной моделью (облако/Ollama/LM Studio)
#  Активный профиль берётся из model_profiles.py / models.json
#  Переключить модель -- через лаунчер, без перезапуска кода.
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import requests
import os
from datetime import datetime
import config
import model_profiles as mp

BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
CONTEXT_FILE         = os.path.join(BASE_DIR, "context.txt")
HISTORY_CONTEXT_FILE = os.path.join(BASE_DIR, "history_context.txt")

MAX_HISTORY_ENTRIES = 20


def load_context() -> str:
    if not os.path.exists(CONTEXT_FILE):
        return ""
    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            lines = [line for line in f.readlines() if not line.strip().startswith("#")]
        return "".join(lines).strip()
    except Exception as e:
        print(f"[LLM] [WARN] Не удалось прочитать context.txt: {e}")
        return ""


def load_history_context() -> str:
    if not os.path.exists(HISTORY_CONTEXT_FILE):
        return ""
    try:
        with open(HISTORY_CONTEXT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return ""
        entries = [e.strip() for e in content.split("---") if e.strip()]
        recent = entries[-MAX_HISTORY_ENTRIES:]
        return "\n---\n".join(recent)
    except Exception as e:
        print(f"[LLM] [WARN] Не удалось прочитать history_context.txt: {e}")
        return ""


def save_history_entry(summary: str):
    if not summary.strip():
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"[{ts}]\n{summary.strip()}\n---\n"
        with open(HISTORY_CONTEXT_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
        print("[LLM] Саммари разговора сохранено в history_context.txt")
    except Exception as e:
        print(f"[LLM] [WARN] Не удалось сохранить историю разговора: {e}")


def build_system_prompt(base_prompt: str) -> str:
    base = base_prompt
    context = load_context()
    history = load_history_context()

    if context:
        base += f"\n\n=== Контекст о пользователе и доп. инструкции ===\n{context}"
    if history:
        base += (
            "\n\n=== Краткая память о прошлых разговорах ===\n"
            "(используй только если это релевантно текущему разговору)\n"
            f"{history}"
        )
    return base


class LLM:
    def __init__(self):
        self.profile = mp.get_active_profile()
        self.history = [
            {"role": "system", "content": build_system_prompt(self.profile["system_prompt"])}
        ]
        self.last_usage = None
        print(f"[LLM] Готов к работе: {self.profile['name']} ({self.profile['model']}) OK")
        if load_context():
            print("[LLM] Контекст загружен из context.txt")
        if load_history_context():
            print("[LLM] Память о прошлых разговорах загружена")

    def reload_profile(self):
        """Перечитывает активный профиль из models.json -- вызывать после смены модели в лаунчере."""
        self.profile = mp.get_active_profile()
        self.history = [
            {"role": "system", "content": build_system_prompt(self.profile["system_prompt"])}
        ]
        self.last_usage = None
        print(f"[LLM] Профиль переключён: {self.profile['name']} ({self.profile['model']})")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.profile.get("api_key"):
            h["Authorization"] = f"Bearer {self.profile['api_key']}"
        return h

    def _endpoint(self) -> str:
        return mp.chat_endpoint(self.profile)

    def is_addressed(self, text: str) -> bool:
        """
        Быстро спрашивает активную модель -- эта фраза обращена к ассистенту или нет?
        """
        prompt = (
            "Ты определяешь, обращается ли человек к голосовому ассистенту. "
            "Ответь ТОЛЬКО одним словом: ДА или НЕТ.\n\n"
            "Примеры когда отвечать ДА:\n"
            "- вопросы (что такое фотосинтез?, как дела?)\n"
            "- просьбы (расскажи, объясни, помоги)\n"
            "- прямые обращения (слушай, эй, ты здесь?)\n\n"
            "Примеры когда отвечать НЕТ:\n"
            "- человек говорит сам с собой (блин, где мои ключи)\n"
            "- разговор с кем-то другим (подожди, я сейчас)\n"
            "- случайный шум или бормотание\n\n"
            f"Фраза: {text}\n"
            "Ответ (ДА или НЕТ):"
        )
        try:
            resp = requests.post(
                self._endpoint(),
                headers=self._headers(),
                json={
                    "model": self.profile["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 5,
                    "temperature": 0.0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
            print(f"[LLM] is_addressed: {answer}")
            return "НЕТ" not in answer
        except Exception as e:
            print(f"[LLM] [WARN] Ошибка is_addressed: {e}")
            return True

    def ask(self, user_text: str) -> tuple[str | None, dict | None]:
        self.history.append({"role": "user", "content": user_text})

        try:
            resp = requests.post(
                self._endpoint(),
                headers=self._headers(),
                json={
                    "model": self.profile["model"],
                    "messages": self.history,
                    "max_tokens": self.profile["max_tokens"],
                    "temperature": self.profile["temperature"],
                },
                timeout=120 if self.profile["type"] in ("ollama", "lmstudio") else 60,
            )
            resp.raise_for_status()
            data = resp.json()

            reply = data["choices"][0]["message"]["content"].strip()
            self.history.append({"role": "assistant", "content": reply})

            usage = data.get("usage", {})
            self.last_usage = {
                "prompt_tokens":     usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens":      usage.get("total_tokens", 0),
            }
            return reply, self.last_usage

        except requests.exceptions.ConnectionError:
            print(f"[LLM] [ERR] Нет подключения к {self.profile['name']} ({self.profile['base_url']}).")
            return None, None
        except requests.exceptions.Timeout:
            print("[LLM] [ERR] Таймаут.")
            return None, None
        except Exception as e:
            print(f"[LLM] [ERR] Ошибка: {e}")
            return None, None

    def reset_history(self):
        self.history = [{"role": "system", "content": build_system_prompt(self.profile["system_prompt"])}]
        self.last_usage = None
        print("[LLM] История сброшена.")

    def summarize_and_save_history(self):
        dialogue = [m for m in self.history if m["role"] != "system"]
        if len(dialogue) < 2:
            return

        transcript = "\n".join(
            f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}"
            for m in dialogue
        )

        prompt = (
            "Сделай очень краткое саммари этого разговора (1-3 предложения), "
            "сохрани только важные факты о пользователе, его планах или предпочтениях, "
            "которые стоит помнить в будущих разговорах. Если ничего важного не было сказано "
            "(пустая болтовня, тест) -- ответь одним словом: ПУСТО.\n\n"
            f"Разговор:\n{transcript}\n\n"
            "Саммари:"
        )

        try:
            resp = requests.post(
                self._endpoint(),
                headers=self._headers(),
                json={
                    "model": self.profile["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            summary = resp.json()["choices"][0]["message"]["content"].strip()

            if summary.upper().startswith("ПУСТО"):
                print("[LLM] Разговор без важных фактов -- не сохраняю.")
                return

            save_history_entry(summary)

        except Exception as e:
            print(f"[LLM] [WARN] Не удалось сделать саммари разговора: {e}")
