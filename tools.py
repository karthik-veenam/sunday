import asyncio
import colorsys
import subprocess
import urllib.request
import json as _json
from datetime import datetime
from typing import TYPE_CHECKING

import memory

if TYPE_CHECKING:
    from home import HAClient

DEFINITIONS = [
    {
        "name": "control_device",
        "description": (
            "Turn a device on or off, and optionally set brightness, color, or color temperature. "
            "Works for: TV, soundbar, dining room speaker, Sian's room speaker, AC, projector, "
            "fan, light 1, light 2, spots, foot lamp, cove, geyser, top light, panels, moon, dashboard. "
            "Top light, panels, moon, and dashboard also support color (hue 0-360, saturation 0-100), hex color, and color_temp (2700-6500K). "
            "Use action 'on' or 'off'. Optionally set volume 0-100 for speakers, "
            "brightness 0-100 for lights, or speed 1-3 for the fan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device name, e.g. 'TV', 'fan', 'light 1', 'cove', 'AC', 'top light', 'panels'",
                },
                "action": {"type": "string", "enum": ["on", "off"]},
                "volume": {
                    "type": "integer",
                    "description": "Volume 0-100 (speakers/TV only, optional)",
                },
                "brightness": {
                    "type": "integer",
                    "description": "Brightness 0-100 (lights only, optional)",
                },
                "speed": {
                    "type": "integer",
                    "description": "Fan speed 1=low, 2=medium, 3=high (fan only, optional)",
                },
                "hue": {
                    "type": "integer",
                    "description": "Color hue 0-360 (top light and panels only). 0=red, 60=yellow, 120=green, 180=cyan, 240=blue, 300=purple",
                },
                "saturation": {
                    "type": "integer",
                    "description": "Color saturation 0-100 (top light and panels only). 0=white, 100=full color",
                },
                "color_temp": {
                    "type": "integer",
                    "description": "Color temperature in Kelvin 2700-6500 (top light and panels only). 2700=warm white, 4000=neutral, 6500=cool white",
                },
                "color": {
                    "type": "string",
                    "description": "Hex color code e.g. '#FF0000' for red, '#00FFFF' for cyan (top light and panels only). Use this instead of hue/saturation when a specific color is requested.",
                },
            },
            "required": ["device", "action"],
        },
    },
    {
        "name": "get_devices",
        "description": "List all controllable devices and their current state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_home_info",
        "description": "Get home sensor readings: temperature, humidity, internet speed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_timer",
        "description": "Set a countdown timer that plays a sound when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "Duration in seconds"},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "get_memories",
        "description": "Retrieve insights and patterns learned about Karthik over time. Use this when asked what you've learned, what patterns you've noticed, or anything about past behaviour.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_memory",
        "description": (
            "Save something you just learned about Karthik, his preferences, or his home setup. "
            "Call this whenever Karthik corrects you, teaches you something new, or reveals a preference "
            "you didn't know — e.g. 'you can actually do X', 'I prefer Y', 'that device works via Z'. "
            "Keep the insight concise and factual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "insight": {
                    "type": "string",
                    "description": "The thing you learned, written as a short factual statement.",
                },
            },
            "required": ["insight"],
        },
    },
    {
        "name": "get_presence",
        "description": "Check which of Karthik's devices are on the home network right now. Tells you if he's home (phone) and if he's working from home (macbook).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_ssh_address",
        "description": "Get the current ngrok SSH address for this Pi. Use when asked for the SSH address, remote access address, ngrok address, or how to connect to the Pi remotely.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "control_firetv",
        "description": (
            "Control the Firestick TV. Use for: turning it on/off, launching apps (netflix, prime, youtube, hotstar), "
            "playback control (play, pause, stop, next, prev, rewind, forward), "
            "searching for content on a specific app, volume (volume_up, volume_down, mute), "
            "navigation (back, home), and Alexa cross-app play (play=finds content across all apps like voice remote). "
            "Also use to get current Fire TV status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["on", "off", "status", "launch", "search", "key", "play"],
                    "description": "on=wake, off=sleep, status=get state, launch=open app, search=search content in specific app, key=send keypress, play=Alexa cross-app search (finds and opens content on the right app automatically)",
                },
                "app": {
                    "type": "string",
                    "description": "App name for launch/search: netflix, prime, youtube, hotstar, spotify",
                },
                "query": {
                    "type": "string",
                    "description": "Search query e.g. 'Breaking Bad' (for search action)",
                },
                "key": {
                    "type": "string",
                    "description": "Key to press: play_pause, play, pause, stop, next, prev, rewind, forward, back, home, volume_up, volume_down, mute",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "send_google_assistant_command",
        "description": (
            "Send a voice command to Google Assistant via Home Assistant. "
            "Use for: Fan, all lights together (e.g. 'turn off all lights in RoKa\\'s room'), "
            "Light 1, Light 2, Cove Light, Spots, Foot lamp. NOT for projector (use control_device) or screen (use projector_screen)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Natural language command, e.g. 'turn on projector in RoKa\\'s room'",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "projector_screen",
        "description": "Control the projector screen. Use 'down' to lower it before watching, 'close' to retract it after.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["down", "close"],
                    "description": "down=lower screen for watching, close=retract screen when done",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "set_alarm",
        "description": (
            "Set a morning wake-up alarm via Google Assistant. "
            "Use when Karthik asks to set an alarm, or when proactively setting his morning alarm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "Alarm time, e.g. '7:00 AM', '7:30 AM', '8:00 AM'",
                },
            },
            "required": ["time"],
        },
    },
    {
        "name": "get_calendar",
        "description": (
            "Get Karthik's calendar events for a given day. "
            "Use when asked about meetings, schedule, busy times, or calendar. "
            "Pulls from Outlook work calendar and Google Calendar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date as YYYY-MM-DD. Omit for today.",
                },
            },
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for live data: scores, news, weather, prices, stock, events, anything current. Always use this instead of guessing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_fitbot",
        "description": (
            "Ask FitBot for fitness data: calories consumed today, protein, calorie deficit, "
            "week summary, or weight history. Use for any question about Karthik's or Sravya's "
            "food intake, workouts, or fitness progress. Pass the question as natural language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language fitness question, e.g. 'how many calories has karthik had today', 'karthik week summary', 'sravya protein today'",
                },
            },
            "required": ["query"],
        },
    },
]

# Hogar Z-wave devices (direct LAN control + real-time state)
_HOGAR_DEVICES = {
    "fan", "light 1", "light 2", "spots", "foot lamp", "cove",
}

# Map friendly names → HA media_player entity
_HA_DEVICE_MAP = {
    "tv":                    "media_player.tv",
    "soundbar":              "media_player.soundbar_2",
    "dining room speaker":   "media_player.googlehome5093",
    "dining speaker":        "media_player.googlehome5093",
    "sian speaker":          "media_player.googlehome9588",
    "sians room speaker":    "media_player.googlehome9588",
    "display":               "media_player.display",
}

_ha: "HAClient | None" = None
_hogar = None  # HogarClient | None
_geyser = None  # GeyserClient | None
_kasa = None  # KasaClient | None
_firetv = None  # FireTVClient | None
_on_insight: "callable | None" = None
_outlook_ics_url: str = ""
_gcal_ics_url: str = ""


def set_ha_client(client: "HAClient") -> None:
    global _ha
    _ha = client


def set_hogar_client(client) -> None:
    global _hogar
    _hogar = client


def set_geyser_client(client) -> None:
    global _geyser
    _geyser = client


def set_kasa_client(client) -> None:
    global _kasa
    _kasa = client


def set_firetv_client(client) -> None:
    global _firetv
    _firetv = client


def set_calendar_urls(outlook_url: str = "", gcal_url: str = "") -> None:
    global _outlook_ics_url, _gcal_ics_url
    _outlook_ics_url = outlook_url or ""
    _gcal_ics_url = gcal_url or ""
    if _outlook_ics_url:
        print("[Calendar] Outlook ICS configured.")
    if _gcal_ics_url:
        print("[Calendar] Google Calendar ICS configured.")


def set_insight_callback(fn: "callable") -> None:
    global _on_insight
    _on_insight = fn


def _notify_insight(insight: str) -> None:
    if _on_insight:
        _on_insight(insight)


async def execute(name: str, inputs: dict, user_text: str = "") -> str:
    result = await _execute(name, inputs)
    try:
        memory.get().log_action(name, inputs, result, user_text)
    except Exception:
        pass
    return result


async def _execute(name: str, inputs: dict) -> str:
    if name == "get_time":
        return datetime.now().strftime("It's %I:%M %p on %A, %B %d, %Y")

    if name == "set_timer":
        seconds = int(inputs["seconds"])
        asyncio.create_task(_run_timer(seconds))
        m, s = divmod(seconds, 60)
        if m and s:
            return f"Timer set for {m} minute{'s' if m != 1 else ''} and {s} second{'s' if s != 1 else ''}."
        if m:
            return f"Timer set for {m} minute{'s' if m != 1 else ''}."
        return f"Timer set for {s} second{'s' if s != 1 else ''}."

    if name == "get_devices":
        return await _get_devices()

    if name == "get_presence":
        return await _get_presence()

    if name == "get_ssh_address":
        return await _get_ssh_address()

    if name == "set_alarm":
        return await _set_alarm(inputs["time"])

    if name == "get_calendar":
        date_str = inputs.get("date")
        return await _get_calendar(date_str)

    if name == "control_firetv":
        return await _control_firetv(inputs)

    if not _ha:
        return "Home Assistant is not configured."

    if name == "control_device":
        device = inputs["device"].lower().strip()
        action = inputs["action"]
        volume = inputs.get("volume")
        brightness = inputs.get("brightness")
        speed = inputs.get("speed")
        hue = inputs.get("hue")
        saturation = inputs.get("saturation")
        color_temp = inputs.get("color_temp")
        color_hex = inputs.get("color")
        if color_hex:
            hue, saturation = _hex_to_hs(color_hex)
        return await _control_device(device, action, volume, brightness, speed, hue, saturation, color_temp)

    if name == "get_home_info":
        return await _get_home_info()

    if name == "get_memories":
        insights = memory.get().get_insights(limit=50)
        if not insights:
            return "No insights saved yet."
        return "\n".join(f"- [{i.get('category') or 'general'}] {i['insight']}" for i in insights)

    if name == "save_memory":
        insight = inputs["insight"]
        memory.get().save_insight(insight, category="conversation")
        _notify_insight(insight)
        print(f"[Memory] Saved insight: {insight!r}")
        return f"Got it, I'll remember that."

    if name == "send_google_assistant_command":
        command = inputs["command"]
        result = await _ha.call_service(
            "google_assistant_sdk", "send_text_command", {"command": command}
        )
        return "Done." if result == "ok" else f"Google Assistant command failed: {result}"

    if name == "projector_screen":
        if not _ha:
            return "Home Assistant not configured."
        action = inputs["action"]
        cmd = "start projector screen down" if action == "down" else "start projector screen close"
        result = await _ha.call_service("google_assistant_sdk", "send_text_command", {"command": cmd})
        label = "Projector screen lowered." if action == "down" else "Projector screen closed."
        return label if result == "ok" else f"Screen command failed: {result}"

    if name == "web_search":
        query = inputs.get("query", "")
        try:
            import urllib.parse
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_redirect=1"
            req = urllib.request.Request(url, headers={"User-Agent": "Sunday/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read())
            answer = data.get("AbstractText") or data.get("Answer") or ""
            if not answer:
                related = data.get("RelatedTopics", [])
                snippets = [t.get("Text", "") for t in related[:3] if isinstance(t, dict) and t.get("Text")]
                answer = " | ".join(snippets)
            return answer if answer else f"No quick answer found for: {query}"
        except Exception as e:
            return f"Search failed: {e}"

    if name == "ask_fitbot":
        from a2a_client import call_fitbot
        return await call_fitbot(inputs["query"])

    if name == "end_conversation":
        return "ok"

    return f"Unknown tool: {name}"


def _hex_to_hs(hex_color: str) -> tuple[int, int]:
    """Convert hex color (#RRGGBB) to (hue 0-360, saturation 0-100)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return round(h * 360), round(s * 100)


async def _control_device(
    device: str, action: str,
    volume: int | None, brightness: int | None, speed: int | None,
    hue: int | None = None, saturation: int | None = None, color_temp: int | None = None
) -> str:
    on = action == "on"

    # ── Hogar Z-wave (fan + lights) ──────────────────────────────────────────
    hogar_key = None
    for key in _HOGAR_DEVICES:
        if key in device or device in key:
            hogar_key = key
            break

    if hogar_key is not None:
        if not _hogar:
            return "Hogar hub is not connected."
        ok = await _hogar.set_device(hogar_key, on=on, brightness=brightness, speed=speed)
        if not ok:
            return f"Failed to control {hogar_key}."
        parts = [f"{hogar_key.title()} turned {action}"]
        if brightness is not None and on:
            parts.append(f"brightness {brightness}%")
        if speed is not None and on:
            speed_name = {1: "low", 2: "medium", 3: "high"}.get(speed, str(speed))
            parts.append(f"speed {speed_name}")
        return ", ".join(parts) + "."

    # ── Kasa/Tapo light strips ────────────────────────────────────────────────
    if _kasa and _kasa.get_device(device):
        ok = await _kasa.set_on(device, on)
        if ok and on:
            if hue is not None:
                await _kasa.set_color(device, hue, saturation if saturation is not None else 100, brightness)
            elif color_temp is not None:
                await _kasa.set_color_temp(device, color_temp, brightness)
            elif brightness is not None:
                await _kasa.set_brightness(device, brightness)
        return f"{device.title()} turned {action}." if ok else f"Failed to control {device}."

    # ── Geyser via Tuya Cloud ─────────────────────────────────────────────────
    if "geyser" in device:
        if not _geyser:
            return "Geyser not configured."
        ok = await _geyser.set_state(on)
        return f"Geyser turned {action}." if ok else "Geyser command failed."

    # ── Projector via HA switch ───────────────────────────────────────────────
    if "projector" in device:
        if not _ha:
            return "Home Assistant is not configured."
        service = "turn_on" if on else "turn_off"
        result = await _ha.call_service("switch", service, {"entity_id": "switch.16amp_smart_plug_2_socket_1"})
        return f"Projector turned {action}." if result == "ok" else f"Projector command failed: {result}"

    # ── AC via Broadlink IR ───────────────────────────────────────────────────
    if "ac" in device:
        if not _ha:
            return "Home Assistant is not configured."
        result = await _ha.call_service("remote", "send_command", {
            "entity_id": "remote.rm4",
            "device": "ac",
            "command": action,
        })
        return f"AC turned {action}." if result == "ok" else f"AC command failed: {result}"

    # ── HA media players ─────────────────────────────────────────────────────
    if not _ha:
        return "Home Assistant is not configured."

    entity = None
    for key, eid in _HA_DEVICE_MAP.items():
        if key in device or device in key:
            entity = eid
            break

    if entity is None:
        return (
            f"I don't know a device called '{device}'. "
            "Try: TV, soundbar, dining room speaker, Sian's speaker, AC, "
            "fan, light 1, light 2, spots, foot lamp, cove."
        )

    service = "turn_on" if on else "turn_off"
    result = await _ha.call_service("media_player", service, {"entity_id": entity})
    if result != "ok":
        return f"Couldn't control {device}: {result}"

    if volume is not None and on:
        await _ha.call_service("media_player", "volume_set", {
            "entity_id": entity,
            "volume_level": round(volume / 100, 2),
        })
        return f"{device.title()} turned on at {volume}% volume."

    return f"{device.title()} turned {action}."


async def _control_firetv(inputs: dict) -> str:
    if not _firetv:
        return "Fire TV is not configured."
    action = inputs["action"]
    if action == "status":
        state = await _firetv.get_state()
        if not state["on"]:
            return "Fire TV is off."
        app = state.get("app", "")
        return f"Fire TV is on, currently running {app}." if app else "Fire TV is on."
    if action == "on":
        ok = await _firetv.wake()
        return "Fire TV turned on." if ok else "Failed to wake Fire TV."
    if action == "off":
        ok = await _firetv.sleep()
        return "Fire TV turned off." if ok else "Failed to sleep Fire TV."
    if action == "launch":
        app = inputs.get("app", "")
        ok = await _firetv.launch_app(app)
        return f"Launched {app}." if ok else f"Couldn't launch {app} — is it installed?"
    if action == "search":
        app = inputs.get("app", "netflix")
        query = inputs.get("query", "")
        ok = await _firetv.search(app, query)
        return f"Searching for '{query}' on {app}." if ok else f"Search failed on {app}."
    if action == "key":
        key = inputs.get("key", "")
        ok = await _firetv.keypress(key)
        return f"Sent {key}." if ok else f"Unknown key: {key}."
    if action == "play":
        query = inputs.get("query", "")
        if not query:
            return "Need a query for play action."
        ok = await _firetv.global_play(query)
        return f"Playing '{query}'." if ok else "Couldn't trigger Alexa play."
    return "Unknown Fire TV action."


async def _get_devices() -> str:
    lines = []

    # Hogar Z-wave devices (real-time state)
    if _hogar:
        hogar_states = _hogar.get_all_states()
        if hogar_states:
            for name, s in sorted(hogar_states.items()):
                state = "on" if s.get("on") else "off"
                extra = ""
                if s.get("brightness") is not None:
                    extra += f", brightness={s['brightness']}%"
                if s.get("speed") is not None:
                    extra += f", speed={s['speed']}"
                lines.append(f"{name.title()} ({state}{extra})")

    # Kasa/Tapo light strips
    if _kasa:
        for name, state in _kasa.get_all_states().items():
            s = "on" if state.get("on") else "off"
            extra = f", brightness={state['brightness']}%" if state.get("brightness") is not None else ""
            lines.append(f"{name.title()} ({s}{extra})")

    # Fire TV
    if _firetv:
        try:
            state = await _firetv.get_state()
            s = "on" if state.get("on") else "off"
            app = f", {state['app']}" if state.get("app") and state.get("on") else ""
            lines.append(f"Fire TV ({s}{app})")
        except Exception:
            lines.append("Fire TV (unknown)")

    # Geyser via Tuya Cloud
    if _geyser:
        try:
            gs = await _geyser.get_state()
            state = "on" if gs.get("on") else "off"
            extra = f", {gs['power_w']}W" if gs.get("power_w") else ""
            lines.append(f"Geyser ({state}{extra})")
        except Exception:
            lines.append("Geyser (unknown)")

    # HA media players
    if _ha:
        states = await _ha.get_states()
        for s in states:
            eid = s["entity_id"]
            name = s["attributes"].get("friendly_name", eid)
            state = s.get("state", "unknown")
            if eid in ("media_player.tv", "media_player.soundbar",
                       "media_player.googlehome5093", "media_player.googlehome9588",
                       "media_player.display"):
                lines.append(f"{name} ({state})")

    if not lines:
        return "No devices found."
    return "Device states: " + ", ".join(lines)


async def _ping(ip: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", ip,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False


async def _get_presence() -> str:
    import web as _web
    presence = _web.get_presence()
    home = presence.get("phone", False)
    zone = _web.get_presence_zone("phone")
    if zone and zone not in ("unknown", "not_home"):
        location = zone.replace("_", " ").title()  # e.g. "Work", "Gym"
        return f"Karthik is at {location}."
    return "Karthik is " + ("home" if home else "away") + "."


async def _get_ssh_address() -> str:
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=3) as r:
            data = _json.loads(r.read())
        tunnels = data.get("tunnels", [])
        if not tunnels:
            return "ngrok is not running — no tunnel active."
        url = tunnels[0]["public_url"]  # e.g. tcp://0.tcp.in.ngrok.io:12345
        host = url.split("//")[1].split(":")[0]
        port = url.split(":")[-1]
        return f"ssh djpi@{host} -p {port}"
    except Exception as e:
        return f"Could not reach ngrok: {e}"


async def _get_home_info() -> str:
    temp = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_temperature")
    humidity = await _ha.get_state("sensor.lumi_lumi_sensor_ht_agl02_humidity")
    down = await _ha.get_state("sensor.hx510_download_speed")
    up = await _ha.get_state("sensor.hx510_upload_speed")

    t = round(float(temp.get("state", 0)), 1)
    h = round(float(humidity.get("state", 0)), 1)
    d = round(float(down.get("state", 0)))
    u = round(float(up.get("state", 0)))

    return (f"Temperature is {t}°C, humidity is {h}%. "
            f"Internet speed: {d} Mbps down, {u} Mbps up.")


async def _run_timer(seconds: int) -> None:
    await asyncio.sleep(seconds)
    subprocess.run(["aplay", "-q", "/home/djpi/sounds/done.wav"])


async def _set_alarm(time_str: str) -> str:
    if not _ha:
        return "Home Assistant not configured — can't set alarm."
    command = f"wake me up at {time_str}"
    result = await _ha.call_service("google_assistant_sdk", "send_text_command", {"command": command})
    return f"Alarm set for {time_str}." if result == "ok" else f"Alarm command failed: {result}"


async def _get_calendar(date_str: str | None = None) -> str:
    from datetime import date, timedelta
    IST_OFF = timedelta(hours=5, minutes=30)
    import datetime as _dt
    today = (_dt.datetime.utcnow() + IST_OFF).date()

    if date_str:
        ds = date_str.strip().lower()
        if ds in ("today", ""):
            target = today
        elif ds == "tomorrow":
            target = today + _dt.timedelta(days=1)
        else:
            try:
                target = _dt.date.fromisoformat(date_str.strip())
            except ValueError:
                target = today
    else:
        target = today

    events = await _fetch_calendar(target)
    if not events:
        label = "today" if target == today else str(target)
        return f"No meetings on calendar for {label}."

    label = "today" if target == today else ("tomorrow" if target == today + _dt.timedelta(days=1) else str(target))
    lines = [f"Calendar for {label}:"]
    for ev in events:
        status_tag = f" [{ev['status']}]" if ev["status"] != "busy" else ""
        end = f"–{ev['end']}" if ev.get("end") else ""
        lines.append(f"  {ev['start']}{end}{status_tag}  {ev['title']}")
    return "\n".join(lines)


async def _fetch_calendar(target_date=None) -> list:
    """Fetch and expand recurring events for a date. Returns list of dicts."""
    import datetime as _dt
    from datetime import timezone, timedelta

    urls = [u for u in [_outlook_ics_url, _gcal_ics_url] if u]
    if not urls:
        return []

    IST = timezone(timedelta(hours=5, minutes=30))
    if target_date is None:
        target_date = (_dt.datetime.utcnow() + timedelta(hours=5, minutes=30)).date()

    all_events = []

    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        print("[Calendar] icalendar/recurring_ical_events not installed — run: pip3 install icalendar recurring-ical-events")
        return []

    def to_ist(dt):
        if isinstance(dt, _dt.datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        # all-day date
        return _dt.datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=IST)

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Sunday/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read()

            cal = icalendar.Calendar.from_ical(raw)
            # Use timezone-aware bounds — fixes Google Calendar date matching
            start_dt = _dt.datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=IST)
            end_dt = _dt.datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=IST)
            events = recurring_ical_events.of(cal).between(start_dt, end_dt)

            for ev in events:
                status = str(ev.get('STATUS', 'CONFIRMED')).upper()
                transp = str(ev.get('TRANSP', 'OPAQUE')).upper()
                if status == 'CANCELLED':
                    continue

                summary = str(ev.get('SUMMARY', 'Meeting'))
                dt_start = to_ist(ev['DTSTART'].dt)
                dt_end = to_ist(ev['DTEND'].dt) if ev.get('DTEND') else None

                # Mark as free only for work calendar (TRANSPARENT) — Google personal events keep all
                is_free = transp == 'TRANSPARENT' and url == _outlook_ics_url
                if is_free:
                    continue

                all_events.append({
                    "start": dt_start.strftime("%I:%M %p"),
                    "end": dt_end.strftime("%I:%M %p") if dt_end else None,
                    "start_24": dt_start.strftime("%H:%M"),
                    "title": summary,
                    "status": "tentative" if status == "TENTATIVE" else "busy",
                })
        except Exception as e:
            print(f"[Calendar] Fetch error: {e}")

    # Deduplicate (same start+end across multiple calendars) and sort
    seen = set()
    unique = []
    for ev in sorted(all_events, key=lambda x: x["start_24"]):
        key = (ev["start_24"], ev.get("end", ""), ev["title"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    return unique
