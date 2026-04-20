"""
Persistent microphone stream — one arecord process shared by wake and STT.
Amplifies 3x inline. Both consumers read from the same asyncio Queue.
"""
import asyncio
import subprocess
import sys
import threading

import numpy as np

CHUNK  = 1280   # 80ms at 16kHz
RATE   = 16000
AMP    = 3.0


class MicStream:
    def __init__(self, device: str):
        self._device = device
        self._queue: asyncio.Queue | None = None
        self._loop:  asyncio.AbstractEventLoop | None = None
        self._proc:  subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue = asyncio.Queue(maxsize=200)  # ~16s buffer

        if sys.platform == "darwin":
            cmd = ["sox", "-t", "coreaudio", "AIRHUG 21",
                   "-r", str(RATE), "-c", "1",
                   "-e", "signed-integer", "-b", "16", "-t", "raw", "-"]
        else:
            cmd = ["arecord", "-D", self._device,
                   "-r", str(RATE), "-c", "1",
                   "-f", "S16_LE", "-t", "raw", "-q"]

        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._reader, daemon=True, name="mic-reader")
        self._thread.start()
        print(f"[Mic] Stream started ({self._device})")

    def _put(self, audio: np.ndarray) -> None:
        """Called on the event loop thread via call_soon_threadsafe."""
        try:
            self._queue.put_nowait(audio)
        except asyncio.QueueFull:
            # Drop oldest frame to make room
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(audio)
            except Exception:
                pass

    def _reader(self) -> None:
        while True:
            raw = self._proc.stdout.read(CHUNK * 2)
            if not raw:
                break
            audio = np.frombuffer(raw, dtype=np.int16).copy()
            self._loop.call_soon_threadsafe(self._put, audio)
        print("[Mic] arecord exited")

    async def read(self) -> np.ndarray:
        """Get the next amplified chunk."""
        return await self._queue.get()

    def drain(self) -> None:
        """Discard buffered audio — call before STT to avoid stale frames."""
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                drained += 1
            except Exception:
                break
        if drained:
            print(f"[Mic] Drained {drained} stale frames")
