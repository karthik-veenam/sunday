# Sunday

A private, fully local AI voice assistant running on a Raspberry Pi. Sunday wakes on a custom wake word, transcribes speech locally, reasons with Claude AI, controls smart home devices, and speaks back — all without sending audio to the cloud.

It also reflects proactively: every hour (configurable) Sunday looks at the room state, checks the weather, reads the news, and decides whether to say something useful — or stay quiet.

---

## What It Is

Sunday is not a wrapper around Google Assistant or Alexa. It is a Python-based voice assistant built from composable components:

- **Wake word** detection via a custom OpenWakeWord model
- **STT** via Faster Whisper running on-device
- **LLM reasoning** via Claude (Haiku 4.5) with tool calling
- **TTS** via Piper with streaming sentence playback
- **Home automation** across Z-Wave, Kasa/Tapo, Tuya, Fire TV, and Home Assistant
- **Memory** via a local SQLite database — every action logged, patterns extracted nightly
- **Proactive reflection** — Sunday notices things and brings them up
- **Web dashboard** — real-time UI over WebSocket showing state, device status, and tappable suggestions
- **Telegram** fallback for when you're not home

Everything runs on a Raspberry Pi. Audio never leaves the device.

---

## Features

### Voice Pipeline
- Custom "sunday" wake word with configurable confidence threshold
- Local speech-to-text (Faster Whisper tiny-int8, runs on-device)
- Adaptive silence detection — longer timeouts mid-conversation
- Streaming TTS playback with sentence-level pipelining (Piper, British English female voice)
- Conversation continuation without re-waking; goodbye phrase detection

### Smart Home Control
- **Z-Wave (Hogar hub):** Fan (speed 1–3), lights (on/off, 0–100% brightness) — local LAN, no cloud
- **Kasa/Tapo L900 light strips:** Brightness, HSV color, hex color, Kelvin color temperature
- **Fire TV:** Wake/sleep, launch apps (Netflix, Prime, YouTube, Hotstar, Spotify, Plex), playback control, volume, navigation
- **Tuya smart plug:** Geyser on/off, live power monitoring (watts, amps, volts)
- **Home Assistant:** Media players, climate (AC via Broadlink IR), sensors, arbitrary service calls
- **Google Assistant bridge:** Natural language commands for devices without direct API support

### AI Agent
- Claude Haiku 4.5 with streaming and concurrent tool execution
- Rich system prompt with room layout, device map, and personality context
- Full tool calling framework — 10+ callable tools
- Conversation history across turns in a session

### Memory & Learning
- SQLite action log — every tool call stored with inputs, outputs, timestamps
- Device state inference from history when hub is unavailable
- User insights saved by category (preference, routine, sequence, device)
- Nightly reflection job (2 AM via systemd) — Claude analyzes the last 24 hours and extracts new patterns
- Insights injected into agent system prompt on next startup

### Proactive Intelligence
- Heartbeat loop (default: 1 hour) gathers room context, weather, cricket scores, news headlines
- Claude decides whether to speak, stay silent, or send a Telegram message
- Generates 3 tappable action suggestions per reflection cycle
- Presence-aware — no speaking when nobody's home, Telegram fallback instead

### Web Dashboard
- Real-time single-page app over WebSocket
- Live clock, temperature, humidity, presence indicator
- Animated avatar ring for listening / thinking / speaking states
- Streaming response display, device status panel, tappable suggestion cards

### Communication
- Primary: voice
- Secondary: Telegram bot (async, multi-user, long-polling)
- Tertiary: web dashboard

---

## Hardware Requirements

| Component | Details |
|---|---|
| **Raspberry Pi** | Pi 4 (2GB+ RAM recommended) or Pi 5 |
| **Microphone** | USB microphone or audio HAT — must appear as an ALSA device |
| **Speaker** | Any ALSA-compatible output |
| **Network** | WiFi (802.11n/ac) — all smart devices must be on the same LAN |
| **Z-Wave hub** | Hogar hub with local LAN API (Socket.IO + REST) |
| **Smart lights** | TP-Link Kasa/Tapo L900 light strips (or compatible) |
| **Fire TV** | Amazon Fire TV Stick with ADB over WiFi enabled |
| **Smart plug** | Tuya-compatible plug (tested with Wipro 16A) |
| **Home Assistant** | Running on the same network, with a long-lived access token |
| **ADB** | `adb` installed on the Pi for Fire TV control |

The project targets Linux/ALSA. It will not run on macOS or Windows without significant modification.

---

## Installation

### 1. System dependencies

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv alsa-utils android-tools-adb
```

### 2. Create a virtual environment

```bash
python3 -m venv /home/djpi/momo-env
source /home/djpi/momo-env/bin/activate
```

### 3. Install Python dependencies

There is no `requirements.txt` yet — install packages directly:

```bash
pip install anthropic openwakeword faster-whisper piper-tts \
            python-kasa tinytuya aiohttp python-socketio numpy
```

### 4. Download model files

**Wake word model** (custom "sunday" model):
```bash
mkdir -p /home/djpi/openwakeword-models
# Place your sunday.onnx model at:
# /home/djpi/openwakeword-models/sunday.onnx
```
Train or obtain a custom OpenWakeWord model at [github.com/dscripka/openWakeWord](https://github.com/dscripka/openWakeWord).

**Whisper STT model** (auto-downloaded on first run):
```bash
mkdir -p /home/djpi/data
# tiny-int8 model downloads automatically to /home/djpi/data on first run
```

**Piper TTS model:**
```bash
mkdir -p /home/djpi/piper-data
cd /home/djpi/piper-data
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/southern_english_female/low/en_GB-southern_english_female-low.onnx.json
```

**Chime sounds** (optional but expected by config):
```bash
mkdir -p /home/djpi/sounds
# Place awake.wav and done.wav at /home/djpi/sounds/
```

### 5. Configure

Copy or create `config.json` in the project directory. See [Configuration](#configuration) below.

### 6. Enable Fire TV ADB (one-time)

On your Fire TV: Settings → My Fire TV → Developer Options → ADB Debugging: ON

Then from the Pi:
```bash
adb connect <firetv_ip>:5555
# Accept the connection prompt on the TV screen
```

### 7. Set up the nightly reflection timer (optional)

```bash
cp sunday-reflect.service sunday-reflect.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sunday-reflect.timer
```

---

## Configuration

All runtime configuration lives in `config.json` at the project root.

```json
{
  "anthropic_api_key": "sk-ant-...",

  "ha_url": "http://homeassistant.local:8123",
  "ha_token": "eyJ...",

  "mic_device": "plughw:3,0",
  "stt_silence_threshold": 700,
  "stt_speech_threshold": 1200,

  "wakeword_model": "/home/djpi/openwakeword-models/sunday.onnx",
  "wakeword_threshold": 0.2,
  "whisper_model": "tiny-int8",
  "whisper_data_dir": "/home/djpi/data",
  "piper_model": "/home/djpi/piper-data/en_GB-southern_english_female-low.onnx",
  "piper_length_scale": 1.2,

  "awake_sound": "/home/djpi/sounds/awake.wav",
  "done_sound": "/home/djpi/sounds/done.wav",

  "roka_phone_ip": "192.168.88.56",
  "reflection_interval": 3600,

  "tapo_username": "email@example.com",
  "tapo_password": "password",
  "tapo_devices": {
    "top light": { "ip": "192.168.88.117" },
    "panels": { "ip": "192.168.88.114" },
    "moon": { "ip": "192.168.88.118" },
    "dashboard": { "ip": "192.168.88.119" }
  },

  "tuya_api_key": "...",
  "tuya_api_secret": "...",
  "tuya_region": "in",
  "tuya_geyser_id": "...",

  "presence_devices": {
    "192.168.88.56": "phone",
    "192.168.88.9": "macbook"
  },

  "telegram_bot_token": "...",
  "telegram_chat_id": 123456789,
  "telegram_users": {
    "123456789": "Karthik"
  },

  "hogar_ip": "192.168.88.22",
  "hogar_home_id": 4783,
  "hogar_user_id": "user_13514",
  "hogar_token": "...",
  "hogar_device_map": {
    "fan": "9-9",
    "light 1": "9-1",
    "light 2": "9-2",
    "spots": "9-3",
    "foot lamp": "9-4",
    "cove": "9-5"
  }
}
```

### Field Reference

| Field | Description |
|---|---|
| `anthropic_api_key` | Anthropic API key for Claude |
| `ha_url` | Home Assistant base URL |
| `ha_token` | Home Assistant long-lived access token |
| `mic_device` | ALSA device identifier for the microphone |
| `stt_silence_threshold` | RMS level below which audio is considered silence |
| `stt_speech_threshold` | RMS level above which audio is considered speech |
| `wakeword_model` | Path to the `.onnx` OpenWakeWord model |
| `wakeword_threshold` | Detection confidence threshold (0.0–1.0) |
| `whisper_model` | Whisper model name (`tiny-int8`) or path to local snapshot |
| `whisper_data_dir` | Directory for Whisper model cache |
| `piper_model` | Path to the `.onnx` Piper TTS model |
| `piper_length_scale` | TTS speed — lower = faster, higher = slower (default: 1.2) |
| `awake_sound` | WAV file played on wake word detection |
| `done_sound` | WAV file played after response completes |
| `roka_phone_ip` | IP of the primary presence-detection device |
| `reflection_interval` | Heartbeat interval in seconds (default: 3600) |
| `tapo_username` | Kasa/Tapo account email |
| `tapo_password` | Kasa/Tapo account password |
| `tapo_devices` | Map of `name → {ip}` for each Tapo light |
| `tuya_api_key` | Tuya Cloud API key |
| `tuya_api_secret` | Tuya Cloud API secret |
| `tuya_region` | Tuya Cloud region code (`in`, `us`, `eu`) |
| `tuya_geyser_id` | Device ID of the Tuya smart plug |
| `presence_devices` | Map of `ip → label` for WiFi presence detection |
| `telegram_bot_token` | Telegram bot token |
| `telegram_chat_id` | Default Telegram chat ID for outbound messages |
| `telegram_users` | Map of `chat_id → name` for multi-user support |
| `hogar_ip` | Local IP of the Hogar Z-Wave hub |
| `hogar_home_id` | Hogar home ID integer |
| `hogar_user_id` | Hogar user ID string |
| `hogar_token` | Hogar JWT auth token |
| `hogar_device_map` | Map of `name → device_id_suffix` for Z-Wave devices |

---

## Running

### Start the voice assistant

```bash
source /home/djpi/momo-env/bin/activate
python main.py
```

Sunday will load models, connect to devices, start the web server, then wait for the wake word.

### Test mode (no microphone required)

```bash
python main.py --test "turn on the fan"
```

Feeds text directly to the agent and plays the TTS response. Useful for testing tools and device control without speaking.

### Run the web dashboard

The dashboard starts automatically with `main.py` on port 8080 (default):

```
http://<pi-ip>:8080
```

### Run the nightly reflection manually

```bash
python reflect.py
```

Analyzes the last 24 hours of actions, extracts patterns, saves new insights to the database.

---

## Project Structure

```
sunday/
├── main.py                  # Entry point — voice loop, model loading, event orchestration
├── agent.py                 # Claude API client, conversation history, streaming tool calls
├── tools.py                 # Tool definitions and handlers (device control, queries)
├── reflection.py            # Proactive reflection engine — context gathering, heartbeat loop
├── reflect.py               # Standalone nightly reflection job (run by systemd timer)
├── wake.py                  # Wake word detection (OpenWakeWord)
├── stt.py                   # Speech-to-text (Faster Whisper)
├── tts.py                   # Text-to-speech (Piper) with async sentence-streaming
├── memory.py                # SQLite action log and insight storage
├── web.py                   # aiohttp web server and WebSocket handler
├── hogar.py                 # Hogar Z-Wave hub client (Socket.IO + REST)
├── firetv.py                # Fire TV control via ADB over WiFi
├── kasa_client.py           # Kasa/Tapo light strip client
├── tuya.py                  # Tuya Cloud API client (geyser)
├── home.py                  # Home Assistant REST API client
├── config.py                # Config dataclass and JSON loader
├── config.json              # Runtime configuration (not committed)
├── static/
│   └── index.html           # Web dashboard (single-page app)
├── sunday.db                # SQLite database (created on first run)
├── sunday-reflect.service   # Systemd service unit for nightly reflection
├── sunday-reflect.timer     # Systemd timer unit (fires at 02:00 daily)
├── test_claude_reflect.py   # Reflection pipeline tests
├── test_reflection_context.py
└── test_world.py            # World context (weather, news, scores) tests
```

---

## How It Works

```
Wake word detected
       │
       ▼
Audio captured via arecord → Faster Whisper → transcript
       │
       ▼
Claude Haiku (with tools, memory context, room state)
       │
       ├── Tool calls → device control (Hogar, Kasa, Fire TV, etc.)
       │
       ▼
Streaming response → sentence splitter → Piper TTS → aplay
       │
       ▼
UI events over WebSocket → dashboard updates in real time
       │
       ▼
Actions logged to SQLite → nightly reflection extracts insights
```

The reflection engine runs in parallel: every hour it pulls room sensor data, live weather, news, and cricket scores, then asks Claude whether to say something, send a Telegram message, or do nothing.

---

## Notes

- The wake word model (`sunday.onnx`) is a custom-trained OpenWakeWord model and is not included in this repo.
- `config.json` contains secrets — keep it out of version control.
- SSL verification is disabled for the Hogar hub (intentional — local LAN only).
- Fire TV ADB pairing must be done manually the first time from the Pi.
- The Whisper `tiny-int8` model downloads automatically on first run (~40MB).
