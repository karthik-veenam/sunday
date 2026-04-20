import asyncio

import numpy as np
from faster_whisper import WhisperModel

from mic import MicStream


class STT:
    RATE  = 16000
    CHUNK = 1280  # must match MicStream.CHUNK (80ms at 16kHz)

    def __init__(self, model_size: str = "tiny-int8", data_dir: str = "/home/djpi/data"):
        print("[STT] Loading Whisper...")
        self.model = WhisperModel(model_size, device="cpu")

    async def listen_and_transcribe(
        self,
        mic: MicStream,
        silence_threshold: int = 700,
        speech_threshold: int = 1200,
        silence_seconds: float = 0.3,
        max_seconds: float = 15.0,
        speech_start_timeout: float = 3.0,
        on_recorded: callable = None,
    ) -> str:
        """Record from shared MicStream until silence, then transcribe."""
        mic.drain()  # discard frames that arrived before we started listening

        audio = await self._record_until_silence(
            mic, silence_threshold, speech_threshold,
            silence_seconds, max_seconds, speech_start_timeout,
        )

        if on_recorded:
            on_recorded()

        if audio.size < self.RATE // 2:  # less than 0.5s of audio
            return ""

        # Boost signal before Whisper; disable VAD filter — our recording loop
        # already handles speech detection, double-VAD strips real speech as noise
        audio_f32 = np.clip(audio.astype(np.float32) * 3.0, -32768, 32767) / 32768.0
        segments, _ = self.model.transcribe(
            audio_f32, beam_size=1, language="en", vad_filter=False
        )
        return " ".join(seg.text for seg in segments).strip()

    async def _record_until_silence(
        self,
        mic: MicStream,
        silence_threshold: int,
        speech_threshold: int,
        silence_seconds: float,
        max_seconds: float,
        speech_start_timeout: float,
    ) -> np.ndarray:
        frames: list[np.ndarray] = []
        silent_chunks = 0
        speech_detected = False
        silence_limit        = int(silence_seconds        * self.RATE / self.CHUNK)
        max_chunks           = int(max_seconds            * self.RATE / self.CHUNK)
        start_timeout_chunks = int(speech_start_timeout   * self.RATE / self.CHUNK)
        waited_chunks = 0
        # Rolling pre-buffer: keep last N chunks before speech onset so word-initial
        # consonants (quiet plosives like T/K/P) aren't clipped off
        PRE_BUFFER = 4  # ~320ms look-back
        pre_buf: list[np.ndarray] = []

        for _ in range(max_chunks):
            chunk = await mic.read()
            rms = int(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

            if not speech_detected:
                waited_chunks += 1
                if waited_chunks >= start_timeout_chunks:
                    break
                # Maintain rolling pre-buffer of recent frames
                pre_buf.append(chunk)
                if len(pre_buf) > PRE_BUFFER:
                    pre_buf.pop(0)
                if rms >= speech_threshold:
                    speech_detected = True
                    frames.extend(pre_buf)  # include pre-buffer to catch word onset
                    pre_buf = []
                continue

            frames.append(chunk)

            if rms < silence_threshold:
                silent_chunks += 1
            else:
                silent_chunks = 0

            if silent_chunks >= silence_limit:
                break

        if not speech_detected:
            return np.array([], dtype=np.int16)
        return np.concatenate(frames) if frames else np.array([], dtype=np.int16)
