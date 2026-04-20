import asyncio
import os
import subprocess
import sys
import time

# Force PulseAudio to connect via the known socket (works from SSH)
os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"
os.environ["PULSE_RUNTIME_PATH"] = "/run/user/1000/pulse/"
os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"

import memory
import web as ui
from config import Config
from mic import MicStream
from wake import WakeWordDetector
from stt import STT
from tts import TTS
from agent import Agent
from home import HAClient
from hogar import HogarClient
from reflection import ReflectionEngine
import tools

GOODBYE = {"goodbye", "bye sunday", "that's all", "thats all", "stop listening"}


async def _voice_loop(
    mic: MicStream,
    wake: WakeWordDetector,
    stt: STT,
    tts: TTS,
    agent: Agent,
    config: Config,
    engine: ReflectionEngine,
    chime,
) -> None:
    async def _on_recorded():
        chime(config.done_sound)
        await ui.emit({"type": "transcribing"})

    print("[Sunday] Waiting for wake word...")

    while True:
        t_wake = time.perf_counter()
        await wake.wait_for_wake_word(mic)
        chime(config.awake_sound)
        await ui.emit({"type": "wake"})
        await asyncio.sleep(0.5)  # let chime finish + echo decay before recording

        t_stt = time.perf_counter()
        transcript = await stt.listen_and_transcribe(
            mic,
            silence_threshold=config.stt_silence_threshold,
            speech_threshold=config.stt_speech_threshold,
            silence_seconds=0.7,
            speech_start_timeout=6.0,
            on_recorded=_on_recorded,
        )
        print(f"[⏱] STT: {time.perf_counter() - t_stt:.2f}s")
        if not transcript:
            await ui.emit({"type": "idle"})
            continue
        print(f"[You] {transcript}")
        print("[Sunday] Transcribed, processing...")
        await ui.emit({"type": "transcript", "text": transcript})
        await ui.emit({"type": "thinking"})

        while True:
            if any(phrase in transcript.lower() for phrase in GOODBYE):
                await tts.speak("Goodbye! Call me if you need anything.")
                agent.reset()
                break

            engine.speaking = True
            t_agent = time.perf_counter()
            keep_listening = await agent.process(transcript, tts)
            engine.speaking = False
            print(f"[⏱] Agent total: {time.perf_counter() - t_agent:.2f}s")

            if not keep_listening:
                print("[Sunday] Conversation ended by Claude — returning to wake word.")
                await ui.emit({"type": "idle"})
                agent.reset()
                break

            chime(config.awake_sound)
            await ui.emit({"type": "wake"})
            await asyncio.sleep(0.5)  # let chime finish + echo decay before recording

            t_stt = time.perf_counter()
            transcript = await stt.listen_and_transcribe(
                mic,
                silence_threshold=config.stt_silence_threshold,
                speech_threshold=config.stt_speech_threshold,
                silence_seconds=0.7,
                speech_start_timeout=3.0,
                on_recorded=_on_recorded,
            )
            if transcript:
                print(f"[⏱] STT: {time.perf_counter() - t_stt:.2f}s")

            if not transcript:
                await ui.emit({"type": "idle"})
                print("[Sunday] Waiting for wake word...")
                break
            print(f"[You] {transcript}")
            print("[Sunday] Transcribed, processing...")
            await ui.emit({"type": "transcript", "text": transcript})
            await ui.emit({"type": "thinking"})


async def _context_loop(engine=None) -> None:
    """Push room sensors + device states to UI every 60s."""
    import json as _json
    import tools as tool_registry
    while True:
        try:
            await asyncio.sleep(0)  # yield to let other tasks start first
            ctx: dict = {}
            if tool_registry._ha:
                temp_s = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
                hum_s  = await tool_registry._ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
                tv_s   = await tool_registry._ha.get_state("media_player.tv")
                try:
                    ctx["temp"] = round(float(temp_s.get("state", 0)), 1)
                except (ValueError, TypeError):
                    pass
                try:
                    ctx["humidity"] = round(float(hum_s.get("state", 0)), 1)
                except (ValueError, TypeError):
                    pass
                ctx["tv"]       = tv_s.get("state", "unknown")

            # Real device states from Hogar hub (authoritative, real-time)
            hogar_states = tool_registry._hogar.get_all_states() if tool_registry._hogar else {}
            if hogar_states:
                ctx["devices"] = {name: ("on" if s.get("on") else "off") for name, s in hogar_states.items()}
                ctx["uptimes"] = {}

            # Kasa/Tapo light strips (fresh state each loop)
            if tool_registry._kasa:
                kasa_states = await tool_registry._kasa.refresh_all_states()
                for name, state in kasa_states.items():
                    ctx.setdefault("devices", {})[name] = "on" if state.get("on") else "off"

            # Tuya devices via HA (geyser + projector)
            if tool_registry._ha:
                for _key, _entity in [("geyser",    "switch.geyser_socket_1"),
                                       ("projector", "switch.16amp_smart_plug_2_socket_1")]:
                    try:
                        _s = await tool_registry._ha.get_state(_entity)
                        _raw = _s.get("state", "off")
                        ctx.setdefault("devices", {})[_key] = "on" if _raw == "on" else "off"
                    except Exception:
                        pass
            else:
                # Fallback: infer states from last actions in memory
                import datetime as _dt
                devices: dict = {}
                uptimes: dict = {}
                now = _dt.datetime.now()
                for row in memory.get().last_actions_per_device():
                    try:
                        inp = _json.loads(row["inputs"]) if isinstance(row["inputs"], str) else row["inputs"]
                        ts = _dt.datetime.fromisoformat(row["timestamp"])
                        mins = int((now - ts).total_seconds() / 60)
                        uptime_str = f"{mins//60}h" if mins >= 60 else f"{mins}m"

                        if row["tool_name"] == "control_device":
                            key = inp.get("device", "").lower()
                            action = inp.get("action", "unknown")
                            devices[key] = action
                            if action == "on":
                                uptimes[key] = uptime_str
                        elif row["tool_name"] == "send_google_assistant_command":
                            cmd = inp.get("command", "").lower()
                            for kw in ["fan", "ac", "moon", "dashboard", "panels", "projector", "geyser",
                                       "light 1", "light 2", "cove", "spots", "foot lamp", "top light"]:
                                if kw in cmd:
                                    state = "on" if "turn on" in cmd or "set" in cmd else "off"
                                    devices[kw] = state
                                    if state == "on":
                                        uptimes[kw] = uptime_str
                    except Exception:
                        pass
                ctx["devices"] = devices
                ctx["uptimes"] = uptimes

            if engine:
                presence = await engine._ping_phone()
                ctx["presence"] = presence.get("home", True)
            await ui.emit({"type": "context", **ctx})
        except Exception as e:
            print(f"[Context] Error: {e}")
        await asyncio.sleep(10)  # presence refreshes every 30s


async def run(config: Config) -> None:
    # Set speaker volume at startup — prevents it resetting low after service restarts
    try:
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"],
            timeout=3, check=False,
        )
        print("[Sunday] Volume set to 100%.")
    except Exception as e:
        print(f"[Sunday] Volume set failed: {e}")

    mic = MicStream(config.mic_device)
    mic.start(asyncio.get_event_loop())

    wake = WakeWordDetector(config.wakeword_model, config.wakeword_threshold)
    stt = STT(config.whisper_model, config.whisper_data_dir, groq_api_key=config.groq_api_key)
    tts = TTS(config.piper_model, config.piper_length_scale, elevenlabs_api_key=config.elevenlabs_api_key)

    insights = [i["insight"] for i in memory.get().get_insights(limit=30)]
    if insights:
        print(f"[Sunday] Loaded {len(insights)} insights from memory.")


    # Pre-warm cloud connections — eliminates TCP/TLS overhead on first real request
    async def _warmup():
        import asyncio
        try:
            await asyncio.gather(
                asyncio.get_event_loop().run_in_executor(None, lambda: stt._groq.models.list()),
                asyncio.get_event_loop().run_in_executor(None, lambda: list(tts._client.text_to_speech.convert(text='.', voice_id='cgSgspJ2msm6clMCkdW9', model_id='eleven_turbo_v2_5', output_format='pcm_22050'))),
            )
            print('[Sunday] Cloud connections warmed up.')
        except Exception as e:
            print(f'[Sunday] Warmup warning: {e}')

    await _warmup()
    agent = Agent(config.xai_api_key, insights=insights)

    async def _handle_ui_command(text: str) -> None:
        print(f"[UI Command] {text!r}")
        await ui.emit({"type": "transcript", "text": text})
        await agent.process(text, tts)

    ui.set_command_handler(_handle_ui_command)

    hogar = HogarClient(ip=config.hogar_ip)
    await hogar.start()
    tools.set_hogar_client(hogar)

    from firetv import FireTVClient
    firetv = FireTVClient(config.firetv_ip or None)
    if await firetv.connect():
        print(f"[Sunday] Fire TV connected at {firetv._ip}.")
    else:
        print("[Sunday] Fire TV unavailable — will discover on demand.")
    tools.set_firetv_client(firetv)

    kasa = None
    if config.tapo_username and config.tapo_devices:
        from kasa_client import KasaClient
        kasa = KasaClient(config.tapo_username, config.tapo_password, config.tapo_devices)
        await kasa.start()
        tools.set_kasa_client(kasa)

    if config.tuya_api_key and config.tuya_geyser_id:
        from tuya import GeyserClient
        geyser = GeyserClient(config.tuya_api_key, config.tuya_api_secret, config.tuya_geyser_id, config.tuya_region)
        tools.set_geyser_client(geyser)
        print("[Sunday] Geyser (Tuya) connected.")

    ha_client = None
    if config.ha_token:
        ha_client = HAClient(config.ha_url, config.ha_token)
        tools.set_ha_client(ha_client)
        print("[Sunday] Home Assistant connected.")
    else:
        print("[Sunday] Home Assistant not configured — add ha_token to config.json")

    if config.outlook_ics_url or config.gcal_ics_url:
        tools.set_calendar_urls(config.outlook_ics_url, config.gcal_ics_url)

    def chime(sound: str) -> None:
        if not sound:
            return
        if sys.platform == "darwin":
            player = ["afplay", sound]
        else:
            player = ["aplay", "-D", "plughw:2,0", "-q", sound]
        asyncio.get_event_loop().run_in_executor(None, subprocess.run, player)

    engine = ReflectionEngine(
        config, tts,
        ha_client=ha_client,
        stt=stt,
        agent=agent,
        chime=chime,
    )

    tasks = [
        _voice_loop(mic, wake, stt, tts, agent, config, engine, chime),
        engine.heartbeat_loop(),
        engine.alert_loop(),
        engine.scheduler_loop(),
        engine.presence_loop(),
        engine.suggestion_loop(),
        engine.telegram_loop(),
        ui.run(),
        _context_loop(engine),
    ]
    if kasa:
        tasks.append(kasa.retry_loop())

    await asyncio.gather(*tasks)


async def test_tts(config: Config, text: str) -> None:
    """Skip wake+STT, feed text directly to agent — for silent debugging."""
    tts = TTS(config.piper_model, config.piper_length_scale, elevenlabs_api_key=config.elevenlabs_api_key)
    agent = Agent(config.xai_api_key)
    if config.ha_token:
        tools.set_ha_client(HAClient(config.ha_url, config.ha_token))
    print(f"[Test] Sending: {text!r}")
    await agent.process(text, tts)
    print("[Test] Done.")


def main() -> None:
    cfg_path = "config.json"
    if "--config" in sys.argv:
        cfg_path = sys.argv[sys.argv.index("--config") + 1]
    config = Config.load(cfg_path)
    memory.init()

    if not config.xai_api_key:
        print("ERROR: Set groq_api_key in config.json")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        text = " ".join(sys.argv[2:]) or "Hello, say something."
        try:
            asyncio.run(test_tts(config, text))
        except KeyboardInterrupt:
            pass
        return

    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        print("\n[Sunday] Bye.")


if __name__ == "__main__":
    main()
