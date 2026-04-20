import asyncio
import sys
import threading

import numpy as np


class TTS:
    def __init__(self, model_path: str, length_scale: float = 0.9):
        self._mac = sys.platform == "darwin"
        if self._mac:
            print("[TTS] Using macOS say command")
            self.sample_rate = 22050
        else:
            print("[TTS] Loading Piper...")
            from piper.voice import PiperVoice, SynthesisConfig
            self._voice = PiperVoice.load(model_path)
            self._syn_config = SynthesisConfig(length_scale=length_scale)
            self.sample_rate = self._voice.config.sample_rate
            self._lock = threading.Lock()
            print("[TTS] Piper ready")

    def synthesize(self, text: str) -> bytes:
        """Synthesize one sentence. On Mac returns text as sentinel bytes."""
        text = text.strip()
        if not text:
            return b""
        if self._mac:
            return b"__say__" + text.encode()
        with self._lock:
            chunks = []
            for audio_chunk in self._voice.synthesize(text, self._syn_config):
                audio_int16 = (audio_chunk.audio_float_array * 32767).astype(np.int16)
                chunks.append(audio_int16.tobytes())
            return b"".join(chunks)

    async def play(self, pcm: bytes) -> None:
        """Play audio. On Mac: calls say for sentinel bytes, else aplay."""
        if not pcm:
            return
        if self._mac:
            if pcm.startswith(b"__say__"):
                text = pcm[7:].decode()
                proc = await asyncio.create_subprocess_exec(
                    "say", text,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            return
        proc = await asyncio.create_subprocess_exec(
            "aplay", "-D", "plughw:2,0", "-r", str(self.sample_rate), "-f", "S16_LE",
            "-c", "1", "-t", "raw", "-q",
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=pcm)

    async def speak(self, text: str) -> None:
        """Convenience: synthesize then play."""
        pcm = await asyncio.get_event_loop().run_in_executor(None, self.synthesize, text)
        await self.play(pcm)
