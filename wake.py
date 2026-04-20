import asyncio

import numpy as np
from openwakeword.model import Model

from mic import MicStream


class WakeWordDetector:
    CHUNK_SAMPLES = 1280  # 80ms at 16kHz
    CONFIRM_FRAMES = 1
    # Feed this many frames to the model before checking scores each wake cycle.
    # This warms the model's rolling mel buffer from backlogged audio accumulated
    # during STT+agent, so it doesn't start cold every cycle.
    WARMUP_FRAMES = 20  # ~1.6s — enough to fill the model's rolling buffer

    def __init__(self, model_path: str, threshold: float = 0.5):
        print("[Wake] Loading model...")
        try:
            self.model = Model(wakeword_models=[model_path])
        except TypeError:
            self.model = Model(wakeword_model_paths=[model_path])
        self.threshold = threshold

    async def wait_for_wake_word(self, mic: MicStream) -> None:
        """Read from shared MicStream until 'sunday' is detected."""
        # Do NOT drain — process backlogged audio to re-warm the model's rolling
        # buffer. Apply a warmup guard to ignore scores during warm-up.
        consecutive = 0
        warmup_remaining = self.WARMUP_FRAMES

        while True:
            audio = await mic.read()
            # Amplify only for the wake word model — STT reads raw audio
            boosted = np.clip(audio.astype(np.float32) * 3.0, -32768, 32767).astype(np.int16)

            if warmup_remaining > 0:
                warmup_remaining -= 1
                self.model.predict(boosted)  # feed to build buffer, don't check score
                continue

            predictions = self.model.predict(boosted)
            max_score = max(predictions.values())
            if max_score >= 0.15:
                print(f"[Wake] score={max_score:.3f}")

            if max_score >= self.threshold:
                consecutive += 1
                if consecutive >= self.CONFIRM_FRAMES:
                    print(f"[Wake] Wake word confirmed (score={max_score:.3f})")
                    # Feed silence to decay the model's internal neural/mel state
                    silence = np.zeros(self.CHUNK_SAMPLES, dtype=np.int16)
                    n_silence = int(1.5 * 16000 / self.CHUNK_SAMPLES)
                    for _ in range(n_silence):
                        self.model.predict(silence)  # silence needs no amplification
                    for key in self.model.prediction_buffer:
                        self.model.prediction_buffer[key].clear()
                    return
            else:
                consecutive = 0
