"""
Interactive wake word comparison test.
Run on Pi directly in your terminal so you can see prompts in real time.

Usage:
    /home/djpi/momo-env/bin/python /home/djpi/test_wake_interactive.py
"""
import sys
import time
import numpy as np
import pyaudio

CHUNK      = 1280    # 80ms at 16kHz
RATE       = 16000
THRESHOLD  = 0.07
WARMUP     = 25      # ~2s warmup
LISTEN_SEC = 15      # seconds per model test

MODELS = [
    ("sunday.onnx",     "/home/djpi/openwakeword-models/sunday.onnx"),
    ("sunday_new.onnx", "/home/djpi/openwakeword-models/sunday_new.onnx"),
]

def test_model(name, path, stream):
    print(f"\n{'='*50}")
    print(f"  Model: {name}")
    print(f"{'='*50}")

    from openwakeword.model import Model
    try:
        mdl = Model(wakeword_models=[path])
    except TypeError:
        mdl = Model(wakeword_model_paths=[path])

    # Warmup with silence
    silence = np.zeros(CHUNK, dtype=np.int16)
    print(f"  Warming up ({WARMUP} frames)...", end="", flush=True)
    for _ in range(WARMUP):
        mdl.predict(silence)
    print(" done")

    # Countdown before listening
    for i in [3, 2, 1]:
        print(f"  Starting in {i}...", flush=True)
        time.sleep(1)
    print(f"\n  >>> SAY 'SUNDAY' NOW (listening {LISTEN_SEC}s) <<<\n")

    max_chunks = int(LISTEN_SEC * RATE / CHUNK)
    max_score_seen = 0.0
    detected = False

    for i in range(max_chunks):
        raw = stream.read(CHUNK, exception_on_overflow=False)
        audio = np.frombuffer(raw, dtype=np.int16)
        preds = mdl.predict(audio)
        score = max(preds.values())
        if score > max_score_seen:
            max_score_seen = score

        elapsed = (i + 1) * CHUNK / RATE
        if score >= 0.01:
            marker = " *** DETECTED ***" if score >= THRESHOLD else ""
            print(f"  [{elapsed:5.1f}s] score={score:.4f}{marker}", flush=True)
        if score >= THRESHOLD:
            detected = True
        # Progress dots every ~2s
        if i % 25 == 24:
            remaining = LISTEN_SEC - elapsed
            print(f"  ... {remaining:.0f}s remaining (peak so far: {max_score_seen:.4f})", flush=True)

    result = "DETECTED" if detected else "NOT DETECTED"
    print(f"\n  Result: {result}  (peak score: {max_score_seen:.4f}, threshold: {THRESHOLD})")
    return detected, max_score_seen


def main():
    pa = pyaudio.PyAudio()

    # Find the USB mic
    mic_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and "AIRHUG" in info.get("name", ""):
            mic_index = i
            print(f"Using mic: [{i}] {info['name']}")
            break
    if mic_index is None:
        # fallback: first input device
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                mic_index = i
                print(f"Using mic (fallback): [{i}] {info['name']}")
                break

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        input_device_index=mic_index,
        frames_per_buffer=CHUNK,
    )

    print("\nWake word comparison test")
    print("Each model gets a 15-second window — say 'Sunday' clearly when prompted.\n")

    results = {}
    for name, path in MODELS:
        detected, peak = test_model(name, path, stream)
        results[name] = (detected, peak)

    stream.stop_stream()
    stream.close()
    pa.terminate()

    print(f"\n{'='*50}")
    print("  SUMMARY")
    print(f"{'='*50}")
    for name, (detected, peak) in results.items():
        status = "PASS" if detected else "FAIL"
        print(f"  [{status}] {name:25s}  peak={peak:.4f}")
    print()


if __name__ == "__main__":
    main()
