# ============================================================
#  recorder.py -- запись звука с микрофона по тишине
#  Запись останавливается автоматически когда ты замолчал
# ============================================================

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


import pyaudio
import wave
import numpy as np
import tempfile
import os
import config


class Recorder:
    def __init__(self):
        self.format   = pyaudio.paInt16
        self.channels = config.CHANNELS
        self.rate     = config.SAMPLE_RATE
        self.chunk    = config.CHUNK

    def record(self) -> str:
        """
        Записывает речь до наступления тишины.
        Возвращает путь к временному WAV-файлу.
        """
        p = pyaudio.PyAudio()
        stream = p.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )

        print("[[MIC]] Слушаю... (говори, остановлюсь сам когда замолчишь)")

        frames = []
        silent_chunks = 0
        max_silent_chunks = int(config.SILENCE_DURATION * self.rate / self.chunk)
        max_chunks = int(config.MAX_RECORD_SECONDS * self.rate / self.chunk)
        speech_started = False

        for _ in range(max_chunks):
            data = stream.read(self.chunk, exception_on_overflow=False)
            frames.append(data)

            # Проверяем громкость чанка
            amplitude = np.frombuffer(data, dtype=np.int16)
            volume = np.abs(amplitude).mean()

            if volume > config.SILENCE_THRESHOLD:
                speech_started = True
                silent_chunks = 0
            elif speech_started:
                silent_chunks += 1
                if silent_chunks >= max_silent_chunks:
                    print("[[MIC]] Тишина -- останавливаю запись.")
                    break

        stream.stop_stream()
        stream.close()
        p.terminate()

        # Сохраняем в временный файл
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()

        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(p.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(b"".join(frames))

        return tmp_path
