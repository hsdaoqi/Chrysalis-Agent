"""语音输入：录音 + 本地 Whisper 转写。

使用 sounddevice 录音，faster-whisper 做本地 STT。
模型懒加载，首次调用时加载。

模型加载优先级：
1. 环境变量 CHRYSALIS_WHISPER_PATH 指定的本地目录
2. 项目 models/whisper-{size} 目录
3. 从 hf-mirror.com 镜像自动下载
"""

import io
import os
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

_SAMPLE_RATE = 16000
_CHANNELS = 1
_DTYPE = "int16"
_HF_MIRROR = "https://hf-mirror.com"


class VoiceRecorder:
    """按键式录音 + Whisper 转写。"""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        language: str | None = None,
        model_path: str | None = None,
    ):
        self._model_size = model_size or os.getenv("CHRYSALIS_WHISPER_MODEL", "base")
        self._device = device or os.getenv("CHRYSALIS_WHISPER_DEVICE", "cpu")
        self._language = language or os.getenv("CHRYSALIS_VOICE_LANG", "zh")
        self._model_path = model_path or os.getenv("CHRYSALIS_WHISPER_PATH", "")
        self._model = None
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        """开始录音。非阻塞，音频数据在回调中累积。"""
        with self._lock:
            if self._recording:
                return
            self._frames = []
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype=_DTYPE,
                callback=self._audio_callback,
            )
            self._stream.start()

    def stop_and_transcribe(self, on_done: Callable[[str], None] | None = None) -> str | None:
        """停止录音并转写。

        如果提供 on_done 回调，转写在后台线程执行并通过回调返回结果。
        否则同步执行并返回文本。
        """
        with self._lock:
            if not self._recording:
                return ""
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            frames = self._frames
            self._frames = []

        if not frames:
            if on_done:
                on_done("")
            return ""

        audio = np.concatenate(frames)

        if on_done:
            threading.Thread(
                target=self._transcribe_async,
                args=(audio, on_done),
                daemon=True,
            ).start()
            return None

        return self._transcribe(audio)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if self._recording:
            self._frames.append(indata.copy())

    def _transcribe_async(self, audio: np.ndarray, on_done: Callable[[str], None]) -> None:
        text = self._transcribe(audio)
        on_done(text)

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self._get_model()
        audio_float = audio.astype(np.float32) / 32768.0
        segments, _ = model.transcribe(
            audio_float,
            language=self._language,
            beam_size=5,
            vad_filter=True,
        )
        return "".join(seg.text for seg in segments).strip()

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            model_path = self._resolve_model_path()
            os.environ.setdefault("HF_ENDPOINT", _HF_MIRROR)
            self._model = WhisperModel(
                model_path,
                device=self._device,
                compute_type="int8" if self._device == "cpu" else "float16",
            )
        return self._model

    def _resolve_model_path(self) -> str:
        """按优先级查找模型路径。"""
        if self._model_path:
            p = Path(self._model_path)
            if p.is_dir():
                return str(p)

        from configs.config import PROJECT_ROOT
        local_dir = PROJECT_ROOT / "models" / f"whisper-{self._model_size}"
        if local_dir.is_dir():
            return str(local_dir)

        return self._model_size
