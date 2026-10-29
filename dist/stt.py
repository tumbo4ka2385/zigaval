# ============================================================
#  stt.py -- Speech-to-Text через faster-whisper
#  Чтобы сменить STT -- замени этот файл, сохранив функцию transcribe()
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


from faster_whisper import WhisperModel
import config


class STT:
    def __init__(self):
        print(f"[STT] Загружаю модель '{config.STT_MODEL}' на {config.STT_DEVICE}...")
        self.model = WhisperModel(
            config.STT_MODEL,
            device=config.STT_DEVICE,
            compute_type=config.STT_COMPUTE_TYPE,
        )
        print("[STT] Модель загружена OK")

    def transcribe(self, audio_path: str) -> str:
        """
        Принимает путь к WAV-файлу.
        Возвращает распознанный текст (строка) или пустую строку.
        """
        segments, info = self.model.transcribe(
            audio_path,
            language=config.STT_LANGUAGE,
            beam_size=5,
            vad_filter=True,           # фильтр тишины -- убирает мусор
            vad_parameters=dict(
                min_silence_duration_ms=500
            ),
        )
        text = " ".join(seg.text for seg in segments).strip()
        return text
