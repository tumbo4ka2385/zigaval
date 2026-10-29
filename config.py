# ============================================================
#  config.py -- общие настройки (STT/TTS/запись)
#  Настройки моделей (API ключи, локальные движки) -- в models.json,
#  редактируются через кнопку "Модель" в лаунчере.
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


# ---------- STT (faster-whisper) ----------
STT_MODEL        = "large-v3-turbo"
STT_DEVICE       = "cuda"
STT_COMPUTE_TYPE = "float16"
STT_LANGUAGE     = "ru"

# ---------- TTS (Silero) ----------
TTS_MODEL_URL   = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
TTS_MODEL_PATH  = "models/silero_5_ru.pt"
TTS_SPEAKER     = "baya"
TTS_SAMPLE_RATE = 48000

# ---------- Запись звука ----------
SAMPLE_RATE        = 16000
CHANNELS           = 1
CHUNK              = 1024
SILENCE_THRESHOLD  = 500
SILENCE_DURATION   = 1.5
MAX_RECORD_SECONDS = 30

# ---------- Команды выхода ----------
EXIT_COMMANDS = ["выход", "пока", "хватит"]

# ---------- Перебивание TTS ----------
INTERRUPT_THRESHOLD = 1500

# ---------- Команды замолчать ----------
SILENCE_COMMANDS = ["подожди", "стой", "стоп", "нет", "тихо", "замолчи", "достаточно"]

# ---------- Vision (Moondream локально) ----------
VISION_MODEL_PATH = "models/moondream.mf"
