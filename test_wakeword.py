"""
Standalone wake word model tester.
Streams mic audio and prints live scores — no TTS, no agent, no Sunday running.

Usage:
    python3 test_wakeword.py /path/to/model.onnx [threshold]

Example:
    python3 test_wakeword.py ~/Downloads/sun_day.onnx 0.15
"""
import sys
import subprocess
import numpy as np
from openwakeword.model import Model

MODEL_PATH = sys.argv[1] if len(sys.argv) > 1 else "sunday.onnx"
THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15

CHUNK_SAMPLES = 1280   # 80ms at 16kHz
WARMUP_FRAMES = 10
AMPLIFY = 3.0

print(f"[Test] Loading model: {MODEL_PATH}")
model = Model(wakeword_model_paths=[MODEL_PATH])
print(f"[Test] Threshold: {THRESHOLD}")
print(f"[Test] Say 'Sunday' — scores will print live. Ctrl+C to stop.\n")

proc = subprocess.Popen(
    ["sox", "-q", "-d", "-r", "16000", "-c", "1",
     "-e", "signed-integer", "-b", "16", "-t", "raw", "-"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
)

frame = 0
try:
    while True:
        raw = proc.stdout.read(CHUNK_SAMPLES * 2)
        if not raw:
            break

        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        audio = np.clip(audio * AMPLIFY, -32768, 32767).astype(np.int16)
        predictions = model.predict(audio)
        frame += 1

        if frame <= WARMUP_FRAMES:
            continue

        score = max(predictions.values())

        if score >= THRESHOLD:
            print(f"  DETECTED  score={score:.3f}  {'█' * int(score * 40)}")
        elif score >= 0.05:
            print(f"            score={score:.3f}  {'░' * int(score * 40)}")

except KeyboardInterrupt:
    print("\n[Test] Done.")
finally:
    proc.kill()
    proc.wait()
