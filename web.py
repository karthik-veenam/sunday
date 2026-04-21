"""
Lightweight aiohttp server — serves the Sunday UI and a WebSocket for real-time events.
  GET  /      → static/index.html
  GET  /ws    → WebSocket (bidirectional)
"""
import asyncio
import json
import os
import time
import uuid

from aiohttp import web, WSMsgType

_clients: set[web.WebSocketResponse] = set()
_command_handler = None  # async callable(text: str) set by main.py
_state_cache: dict[str, dict] = {}  # last seen event per type, replayed to new clients
_presence_last_seen: dict[str, float] = {"phone": 0.0}  # seeded → starts as away
_presence_zone: dict[str, str] = {"phone": "unknown"}   # last known zone per device
_PRESENCE_TIMEOUT = 2 * 60  # seconds


def get_presence() -> dict[str, bool]:
    now = time.time()
    return {device: (now - ts) < _PRESENCE_TIMEOUT for device, ts in _presence_last_seen.items()}


def get_presence_zone(label: str = "phone") -> str:
    """Return the last known zone string for a device (e.g. 'home', 'work', 'not_home')."""
    return _presence_zone.get(label, "unknown")


def update_presence(label: str, home: bool, zone: str | None = None) -> None:
    """Called by presence_loop to authoritatively set device presence and zone."""
    _presence_last_seen[label] = time.time() if home else 0.0
    if zone:
        _presence_zone[label] = zone

_SUGGESTIONS_FILE = os.path.join(os.path.dirname(__file__), "last_suggestions.json")

# Pre-populate suggestions from last run so dashboard is never empty on restart
try:
    with open(_SUGGESTIONS_FILE) as _f:
        _state_cache["suggestions"] = json.load(_f)
except Exception:
    pass


# ── Public API ────────────────────────────────────────────────────────────────

def set_command_handler(fn) -> None:
    """Register an async function to handle commands sent from the UI."""
    global _command_handler
    _command_handler = fn


async def emit(event: dict) -> None:
    """Broadcast an event to all connected UI clients."""
    # Cache stateful events so new clients get current state immediately
    if event.get("type") in ("context", "suggestions", "wake", "thinking", "transcribing", "idle", "transcript"):
        _state_cache[event["type"]] = event
        if event.get("type") == "suggestions":
            try:
                with open(_SUGGESTIONS_FILE, "w") as f:
                    json.dump(event, f)
            except Exception:
                pass
    if not _clients:
        return
    data = json.dumps(event)
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_str(data)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def run(port: int = 8080) -> None:
    """Start the HTTP + WebSocket server. Run as an asyncio task."""
    app = web.Application()
    app.router.add_get("/ws", _ws_handler)
    app.router.add_get("/", _index_handler)
    app.router.add_get("/tg", _tg_handler)
    app.router.add_get("/presence", _presence_handler)
    app.router.add_post("/device", _device_handler)
    app.router.add_get("/.well-known/agent.json", _a2a_card_handler)
    app.router.add_post("/tasks/send", _a2a_task_handler)
    app.router.add_static("/static", os.path.join(os.path.dirname(__file__), "static"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[Web] UI available at http://0.0.0.0:{port}")
    while True:
        await asyncio.sleep(3600)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def _presence_handler(request: web.Request) -> web.Response:
    """
    Tasker hits this when phone connects/disconnects from home WiFi.
    GET /presence?device=phone&home=1   → phone is home
    GET /presence?device=phone&home=0   → phone left
    """
    device = request.rel_url.query.get("device", "phone")
    was_home = (time.time() - _presence_last_seen.get(device, 0)) < _PRESENCE_TIMEOUT
    _presence_last_seen[device] = time.time()
    if not was_home:
        print(f"[Presence] {device} → home")
    return web.Response(text="ok")


async def _device_handler(request: web.Request) -> web.Response:
    """
    POST /device  {"device": "fan", "action": "on"}
    Calls _control_device directly — no LLM, instant.
    """
    import tools as _tools
    try:
        body = await request.json()
        device = body.get("device", "").strip().lower()
        action = body.get("action", "").strip().lower()
        if not device or action not in ("on", "off"):
            return web.Response(status=400, text="bad request")
        result = await _tools._control_device(device, action, None, None, None)
        return web.Response(text=result)
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def _index_handler(request: web.Request) -> web.Response:
    static = os.path.join(os.path.dirname(__file__), "static", "index.html")
    return web.FileResponse(static)


async def _tg_handler(request: web.Request) -> web.Response:
    static = os.path.join(os.path.dirname(__file__), "static", "tg.html")
    return web.FileResponse(static)


async def _a2a_card_handler(request: web.Request) -> web.Response:
    card = {
        "name": "Sunday",
        "description": "Home voice assistant with presence detection, calendar access, and smart home control.",
        "url": "http://localhost:8080",
        "version": "1.0.0",
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "presence",
                "name": "Check Presence",
                "description": "Check if Karthik is currently home",
                "tags": ["presence", "home"],
                "examples": ["is karthik home?", "check presence"],
            },
            {
                "id": "calendar",
                "name": "Get Calendar",
                "description": "Get calendar events for today or tomorrow",
                "tags": ["calendar", "schedule", "meetings"],
                "examples": ["calendar today", "calendar tomorrow", "any meetings today?"],
            },
        ],
    }
    return web.json_response(card)


async def _a2a_task_handler(request: web.Request) -> web.Response:
    import tools as _tools
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    task_id = body.get("id", str(uuid.uuid4()))
    parts = body.get("message", {}).get("parts", [])
    text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text").lower().strip()

    try:
        if any(w in text for w in ("presence", "is karthik", "home", "away")):
            result = await _tools._get_presence()
        elif any(w in text for w in ("calendar", "schedule", "meeting", "tomorrow", "today", "event")):
            date_str = "tomorrow" if "tomorrow" in text else "today"
            result = await _tools._get_calendar(date_str)
        else:
            result = await _tools._get_presence()
    except Exception as e:
        result = f"Error: {e}"

    return web.json_response({
        "id": task_id,
        "status": {"state": "completed"},
        "artifacts": [{"parts": [{"type": "text", "text": result}]}],
    })


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=15)  # ping every 15s — keeps SSH tunnels alive
    await ws.prepare(request)
    _clients.add(ws)
    # Replay cached state so the new client sees current data immediately
    for cached in _state_cache.values():
        try:
            await ws.send_str(json.dumps(cached))
        except Exception:
            pass
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and _command_handler:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "command" and data.get("text"):
                        asyncio.create_task(_command_handler(data["text"]))
                except Exception:
                    pass
    finally:
        _clients.discard(ws)
    return ws
