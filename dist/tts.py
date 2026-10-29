# ============================================================
#  tts.py -- Text-to-Speech через Silero
#  Поддерживает перебивание голосом и команды "замолчи"
#  Поддерживает русский и английский язык (авто-определение)
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


import os
import re
import torch
import threading
import numpy as np
import soundfile as sf
import sounddevice as sd
import urllib.request
import pyaudio
import wave
import tempfile
import config


# Таблица транслитерации для смешанных русско-английских текстов
_TRANSLIT = {
    'a': 'а', 'b': 'б', 'c': 'с', 'd': 'д', 'e': 'е', 'f': 'ф',
    'g': 'г', 'h': 'х', 'i': 'и', 'j': 'й', 'k': 'к', 'l': 'л',
    'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'q': 'к', 'r': 'р',
    's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс',
    'y': 'й', 'z': 'з',
    'A': 'А', 'B': 'Б', 'C': 'С', 'D': 'Д', 'E': 'Е', 'F': 'Ф',
    'G': 'Г', 'H': 'Х', 'I': 'И', 'J': 'Й', 'K': 'К', 'L': 'Л',
    'M': 'М', 'N': 'Н', 'O': 'О', 'P': 'П', 'Q': 'К', 'R': 'Р',
    'S': 'С', 'T': 'Т', 'U': 'У', 'V': 'В', 'W': 'В', 'X': 'КС',
    'Y': 'Й', 'Z': 'З',
}


def _translit_latin(text: str) -> str:
    """Транслитерирует латинские буквы в кириллицу для русской TTS-модели."""
    return ''.join(_TRANSLIT.get(ch, ch) for ch in text)


def _detect_language(text: str) -> str:
    """Определяет преобладающий язык текста: 'ru' или 'en'."""
    latin  = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    cyril  = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    return 'en' if latin > cyril else 'ru'


# Известные голоса по моделям
TTS_MODEL_VOICES = {
    "ru": ["aidar", "baya", "kseniya", "xenia", "eugene", "random"],
    "en": ["en_0", "en_1", "en_2", "en_3", "en_4", "en_5",
           "en_6", "en_7", "en_8", "en_9", "en_10", "en_11",
           "en_12", "en_13", "en_14", "en_15", "en_16",
           "en_17", "en_18", "en_19", "en_20", "en_21",
           "en_22", "en_23", "en_24", "en_25", "en_26",
           "en_27", "en_28", "en_29", "en_30", "en_31",
           "en_32", "en_33", "en_34", "en_35", "en_36",
           "en_37", "en_38", "en_39", "en_40", "en_41",
           "en_42", "en_43", "en_44", "en_45", "en_46",
           "en_47", "en_48", "en_49", "en_50", "en_51",
           "en_52", "en_53", "en_54", "en_55", "en_56",
           "en_57", "en_58", "en_59", "en_60", "en_61",
           "en_62", "en_63", "en_64", "en_65", "en_66",
           "en_67", "en_68", "en_69", "en_70", "en_71",
           "en_72", "en_73", "en_74", "en_75", "en_76",
           "en_77", "en_78", "en_79", "en_80", "en_81",
           "en_82", "en_83", "en_84", "en_85", "en_86",
           "en_87", "en_88", "en_89", "en_90", "en_91",
           "en_92", "en_93", "en_94", "en_95", "en_96",
           "en_97", "en_98", "en_99", "en_100", "en_101",
           "en_102", "en_103", "en_104", "en_105", "en_106",
           "en_107", "en_108", "en_109", "en_110", "en_111",
           "en_112", "en_113", "en_114", "en_115", "en_116",
           "en_117", "random"],
}

TTS_MODEL_URLS = {
    "ru": "https://models.silero.ai/models/tts/ru/v4_ru.pt",
    "en": "https://models.silero.ai/models/tts/en/v3_en.pt",
}

TTS_MODEL_PATHS = {
    "ru": "models/silero_v4_ru.pt",
    "en": "models/silero_v3_en.pt",
}

TTS_DEFAULT_SPEAKERS = {
    "ru": "baya",
    "en": "en_0",
}


class TTS:
    def __init__(self, stt=None):
        # Читаем настройки TTS из активного профиля (models.json),
        # если там нет -- берём из config.py
        try:
            import model_profiles as mp
            profile = mp.get_active_profile()
            self.tts_lang    = profile.get("tts_lang", "ru")
            self.tts_speaker = profile.get("tts_speaker", None)
        except Exception:
            self.tts_lang    = "ru"
            self.tts_speaker = None

        if not self.tts_speaker:
            self.tts_speaker = TTS_DEFAULT_SPEAKERS.get(self.tts_lang, "baya")

        model_url  = TTS_MODEL_URLS.get(self.tts_lang, TTS_MODEL_URLS["ru"])
        model_path = TTS_MODEL_PATHS.get(self.tts_lang, TTS_MODEL_PATHS["ru"])

        print(f"[TTS] Загружаю Silero TTS (язык={self.tts_lang}, голос={self.tts_speaker})...")
        os.makedirs(os.path.dirname(model_path), exist_ok=True)

        if not os.path.exists(model_path):
            print("[TTS] Скачиваю модель...")
            urllib.request.urlretrieve(model_url, model_path)
            print("[TTS] Модель скачана OK")

        self.device = torch.device("cpu")
        self.model  = torch.package.PackageImporter(model_path).load_pickle(
            "tts_models", "model"
        )
        self.model.to(self.device)
        self.stt             = stt
        self._interrupted    = False
        self._silence_requested = False
        self._interrupt_audio   = []
        print("[TTS] Модель загружена OK")

    def _preprocess(self, text: str) -> str:
        """Убирает markdown-разметку. Для русской модели транслитерирует латиницу."""
        text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
        text = re.sub(r'#+\s*', '', text)
        if self.tts_lang == "ru":
            text = _translit_latin(text)
        return text

    def _split_text(self, text: str, max_len: int = 900):
        text = self._preprocess(text)
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        chunks, current = [], ""
        for s in sentences:
            if len(current) + len(s) + 1 <= max_len:
                current = (current + " " + s).strip()
            else:
                if current:
                    chunks.append(current)
                current = s[:max_len]
        if current:
            chunks.append(current)
        return chunks

    def _listen_for_interrupt(self):
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                        input=True, frames_per_buffer=512)
        triggered = False
        self._interrupt_audio = []
        try:
            while not self._interrupted:
                data = stream.read(512, exception_on_overflow=False)
                volume = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
                if not triggered and volume > config.INTERRUPT_THRESHOLD:
                    triggered = True
                    print("[[INT]] Перебиваю!")
                    self._interrupted = True
                    self._interrupt_audio.append(data)
                elif triggered:
                    self._interrupt_audio.append(data)
                    if len(self._interrupt_audio) > int(16000 / 512 * 1.5):
                        break
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    def _check_silence_command(self) -> bool:
        if not self._interrupt_audio or not self.stt:
            return False
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        p = pyaudio.PyAudio()
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
            wf.setframerate(16000)
            wf.writeframes(b"".join(self._interrupt_audio))
        p.terminate()
        try:
            text = self.stt.transcribe(tmp_path).lower()
            print(f"[INT] Сказано при перебивании: {text}")
            return any(cmd in text for cmd in config.SILENCE_COMMANDS)
        except Exception:
            return False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def speak(self, text: str) -> bool:
        """
        Озвучивает текст.
        Возвращает True если перебили командой молчания.
        """
        if not text.strip():
            return False

        self._interrupted       = False
        self._silence_requested = False
        self._interrupt_audio   = []

        listener = threading.Thread(target=self._listen_for_interrupt, daemon=True)
        listener.start()

        for chunk in self._split_text(text):
            if self._interrupted:
                break
            if not chunk.strip():
                continue
            try:
                audio = self.model.apply_tts(
                    text=chunk,
                    speaker=self.tts_speaker,
                    sample_rate=config.TTS_SAMPLE_RATE,
                )
                sd.play(audio.numpy(), samplerate=config.TTS_SAMPLE_RATE)
                while sd.get_stream().active:
                    if self._interrupted:
                        sd.stop()
                        break
            except Exception as e:
                print(f"[TTS] [WARN] Ошибка: {e}")

        self._interrupted = True
        listener.join(timeout=2)

        if self._interrupt_audio and self.stt:
            self._silence_requested = self._check_silence_command()

        return self._silence_requested

    def speak_to_file(self, text: str, path: str):
        audio = self.model.apply_tts(
            text=self._preprocess(text),
            speaker=self.tts_speaker,
            sample_rate=config.TTS_SAMPLE_RATE,
        )
        sf.write(path, audio.numpy(), config.TTS_SAMPLE_RATE)
