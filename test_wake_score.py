"""
10-trial wake word scoring test — default audio, no custom settings.
Run on Pi:
    /home/djpi/momo-env/bin/python /home/djpi/test_wake_score.py
"""
import subprocess, time
import numpy as np

CHUNK      = 1280
RATE       = 16000
THRESHOLD  = 0.07
WARMUP     = 25
TRIALS     = 10
MODEL_PATH = "/home/djpi/openwakeword-models/sunday.onnx"

proc = subprocess.Popen(
    ["arecord", "-D", "plughw:CARD=A21,DEV=0", "-r", str(RATE), "-c", "1", "-f", "S16_LE", "-t", "raw", "-q"],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
)

def read_chunk():
    raw = proc.stdout.read(CHUNK * 2)
    audio = np.frombuffer(raw, dtype=np.int16).copy()
    if len(audio) < CHUNK:
        audio = np.pad(audio, (0, CHUNK - len(audio)))
    return audio

from openwakeword.model import Model
try:
    mdl = Model(wakeword_models=[MODEL_PATH])
except TypeError:
    mdl = Model(wakeword_model_paths=[MODEL_PATH])

silence = np.zeros(CHUNK, dtype=np.int16)
print("Warming up...", end="", flush=True)
for _ in range(WARMUP):
    mdl.predict(silence)
print(" ready.\n")

print("=" * 52)
print("  10-TRIAL TEST  —  sunday.onnx")
print("  Say 'Sunday' the moment you see >> GO! <<")
print("=" * 52)

scores = []

for trial in range(1, TRIALS + 1):
    print(f"\nTrial {trial:2d}/10", end="", flush=True)
    for c in [3, 2, 1]:
        print(f"  {c}...", end="", flush=True)
        time.sleep(1)
    # Drain ~3s of buffered audio that accumulated during countdown
    for _ in range(38):
        proc.stdout.read(CHUNK * 2)

    print("  >> GO! <<", flush=True)

    peak = 0.0
    for _ in range(int(4.0 * RATE / CHUNK)):
        audio = read_chunk()
        s = max(mdl.predict(audio).values())
        if s > peak:
            peak = s

    hit = peak >= THRESHOLD
    bar = "#" * min(int(peak * 100), 30)
    print(f"          peak={peak:.4f}  [{bar:<30}]  {'DETECTED ✓' if hit else 'missed ✗'}", flush=True)
    scores.append(peak)

    for _ in range(8):
        read_chunk()
        mdl.predict(silence)

proc.terminate()

detected = sum(1 for s in scores if s >= THRESHOLD)
print(f"\n{'='*52}")
print(f"  RESULTS: {detected}/{TRIALS} detected  (threshold={THRESHOLD})")
print(f"  Scores:  {[f'{s:.3f}' for s in scores]}")
print(f"  Peak:    {max(scores):.4f}")
print(f"  Mean:    {sum(scores)/len(scores):.4f}")
print("=" * 52)
