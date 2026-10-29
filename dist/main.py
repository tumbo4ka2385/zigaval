# ============================================================
#  main.py -- главный цикл
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


import os
from recorder import Recorder
from stt import STT
from tts import TTS
from llm import LLM
from vision import Vision
import config


def main():
    print("=" * 50)
    print("  Голосовой ассистент запускается...")
    print("=" * 50)

    recorder = Recorder()
    stt      = STT()
    tts      = TTS(stt=stt)
    llm      = LLM()
    vision   = Vision()

    print(f"[Модель] Активна: {llm.profile['name']} ({llm.profile['type']})")

    print("[[EAR]] Слушаю фоном...\n")
    print('  <<посмотри на экран>> -- скриншот и описание')
    print('  <<посмотри на экран и [вопрос]>> -- скриншот + вопрос\n')

    last_vision_context = None

    try:
        _run_loop(recorder, stt, tts, llm, vision, last_vision_context)
    except (KeyboardInterrupt, SystemExit):
        print("[LLM] Завершение по прерыванию -- сохраняю краткую память о разговоре...")
        llm.summarize_and_save_history()
        raise
    except Exception as e:
        print(f"[ERR] Неожиданная ошибка: {e}")
        print("[LLM] Сохраняю краткую память о разговоре перед выходом...")
        llm.summarize_and_save_history()
        raise


def _run_loop(recorder, stt, tts, llm, vision, last_vision_context):
    while True:
        audio_path = recorder.record()
        text = stt.transcribe(audio_path)

        if os.path.exists(audio_path):
            os.remove(audio_path)

        if not text:
            continue

        print(f"[Ты]: {text}")
        text_lower = text.lower()

        # Выход
        if any(cmd in text_lower for cmd in config.EXIT_COMMANDS):
            tts.speak("Пока!")
            print("[LLM] Сохраняю краткую память о разговоре...")
            llm.summarize_and_save_history()
            break

        # Команда скриншота
        is_screenshot_cmd = any(p in text_lower for p in [
            "посмотри на экран", "глянь на экран", "что на экране",
            "смотри на экран", "анализируй экран"
        ])

        if is_screenshot_cmd:
            tts.speak("Смотрю на экран...")
            extra = ""
            for trigger in ["посмотри на экран и", "глянь на экран и"]:
                if trigger in text_lower:
                    extra = text_lower.split(trigger, 1)[-1].strip()
                    break

            description = vision.screenshot_and_describe(extra_prompt=extra)
            if description is None:
                tts.speak("Не смогла проанализировать экран.")
                continue

            print(f"[Vision]: {description}")
            last_vision_context = description

            prompt = (
                f"На экране пользователя: {description}\n\nВопрос: {extra}"
                if extra else
                f"Кратко скажи что видишь на экране пользователя: {description}"
            )

            print("[[THINK]] Думаю...")
            reply, usage = llm.ask(prompt)
            if reply:
                print(f"[Ассистент]: {reply}".replace(chr(10), "<br>"))
                if usage:
                    print(f"[Токены]: вход={usage['prompt_tokens']} выход={usage['completion_tokens']} итого={usage['total_tokens']} max={llm.profile['max_tokens']}")
                print("[[SPEAK]] Говорю...")
                tts.speak(reply)
            continue

        # Контекст предыдущего скриншота
        if last_vision_context and any(p in text_lower for p in [
            "про это", "об этом", "на экране", "там", "это что", "что это"
        ]):
            text = f"[Контекст экрана: {last_vision_context}]\n\nВопрос: {text}"

        # Это обращение ко мне?
        if not llm.is_addressed(text):
            print("[[ZZZ]] Не ко мне, игнорирую.\n")
            continue

        # Сброс истории
        if "новый диалог" in text_lower or "сбрось историю" in text_lower:
            llm.reset_history()
            last_vision_context = None
            tts.speak("История сброшена.")
            continue

        # Обычный запрос
        print("[[THINK]] Думаю...")
        reply, usage = llm.ask(text)
        if reply is None:
            tts.speak("Не смогла связаться с нейросетью.")
            continue

        print(f"[Ассистент]: {reply}".replace(chr(10), "<br>"))
        if usage:
            print(f"[Токены]: вход={usage['prompt_tokens']} выход={usage['completion_tokens']} итого={usage['total_tokens']} max={llm.profile['max_tokens']}")

        print("[[SPEAK]] Говорю...")
        silenced = tts.speak(reply)
        if silenced:
            print("[[SHH]] Замолкаю по команде.\n")

        print()


if __name__ == "__main__":
    main()
