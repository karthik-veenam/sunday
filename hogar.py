"""
Hogar local hub client — real-time device state via Socket.IO + REST.
Connects to the Z-wave hub on LAN, no cloud needed.
"""
import asyncio
import json
import time
import ssl
import aiohttp
import socketio

# ── Defaults (overridden by config.json) ─────────────────────────────────────
_DEFAULT_IP      = "192.168.88.22"
_DEFAULT_HOME_ID = 4783
_DEFAULT_USER_ID = "user_13514"
_DEFAULT_TOKEN   = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzM4NCJ9"
    ".eyJpc3MiOiJodHRwOlwvXC9sdWNpLnZubiIsImF1ZCI6Imh0dHA6XC9cL2x1Y2kudm5jIiwiZXhwIjoyMDY1NTM2MzQzLCJqdGkiOjEzNTE0LCJob21lX2lkIjo0NzgzfQ"
    ".a33fi1YlNZGVarXFRWN_VMNldF8jEO0t9tEn_BuSKabCliShC_xkpLPS7RSNlcEu"
)
_DEFAULT_DEVICE_MAP = {
    "light 1":   "9-1",
    "light 2":   "9-2",
    "spots":     "9-3",
    "foot lamp": "9-5",
    "cove":       "9-6",
    "fan":       "9-9",
}


class HogarClient:
    """
    Maintains real-time device state for all mapped devices.
    Call start() to begin polling + WebSocket listening.
    Device map and hub credentials come from config.json — no hardcoded IDs.
    """

    def __init__(self, ip: str = _DEFAULT_IP, home_id: int = _DEFAULT_HOME_ID,
                 user_id: str = _DEFAULT_USER_ID, token: str = _DEFAULT_TOKEN,
                 device_map: dict | None = None):
        if device_map is None:
            device_map = _DEFAULT_DEVICE_MAP
        # Hub connection params
        self._hub_ip   = ip
        self._hub_ws   = f"http://{ip}:8009"
        self._hub_rest = f"https://{ip}:89"
        self._home_id  = home_id
        self._user_id  = user_id
        self._token    = token

        # Device map: friendly name (lower) → full devid
        # device_map from config stores just the suffix after the hub MAC prefix
        _pfx = f"{ip.replace('.', '_').replace('_', ':')}_zwave-cb4b64ae:".replace('_', ':')
        # Actually prefix is the hub MAC, not the IP — keep it stable
        _mac_pfx = "70:2c:1f:37:a8:4f_zwave-cb4b64ae:"
        raw_map = device_map or {}
        self._device_map: dict[str, str] = {
            k.lower(): (_mac_pfx + v if not v.startswith("70:") else v)
            for k, v in raw_map.items()
        }
        self._id_to_name: dict[str, str] = {v: k for k, v in self._device_map.items()}

        self._states: dict[str, dict] = {}   # friendly_name → {on, brightness, speed}
        self._lock = asyncio.Lock()
        self._sio = socketio.AsyncClient(ssl_verify=False, logger=False, engineio_logger=False)
        self._running = False
        self._register_handlers()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_state(self, name: str) -> dict | None:
        """Return current state dict for a device, or None if unknown."""
        return self._states.get(name.lower())

    def get_all_states(self) -> dict[str, dict]:
        """Return copy of all known device states."""
        return dict(self._states)

    def is_on(self, name: str) -> bool | None:
        s = self._states.get(name.lower())
        return s["on"] if s else None

    async def set_device(self, name: str, on: bool, brightness: int | None = None, speed: int | None = None) -> bool:
        """Send a control command to a device. Returns True on success."""
        devid = self._device_map.get(name.lower())
        if not devid:
            print(f"[Hogar] Unknown device: {name!r}")
            return False

        executions = [{"command": "OnOff", "params": {"on": on}}]
        if brightness is not None:
            executions.append({"command": "Brightness", "params": {"brightness": brightness}})
        if speed is not None:
            executions.append({"command": "Speed", "params": {"speed": speed}})

        # Hogar only supports one execution per call — send OnOff first
        for execution in executions:
            body = {
                "homeid": self._home_id,
                "payload": {
                    "cmd": "set",
                    "reqid": f"sunday-{int(time.time())}",
                    "objects": [{"type": "devices", "data": [devid], "execution": execution}],
                },
            }
            ok = await self._rest_post("/home-control/push-control-to-thing", body)
            if not ok:
                return False
        return True

    async def start(self) -> None:
        """Poll states once then start the WebSocket listener loop."""
        self._running = True
        await self._poll_all_states()
        asyncio.create_task(self._ws_loop())
        asyncio.create_task(self._poll_loop())

    # ── REST ─────────────────────────────────────────────────────────────────

    async def _rest_post(self, path: str, body: dict) -> bool:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(f"{self._hub_rest}{path}", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json(content_type=None)
                    return data.get("success", False)
        except Exception as e:
            print(f"[Hogar] REST error {path}: {e}")
            return False

    async def _poll_all_states(self) -> None:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        body = {
            "homeid": self._home_id,
            "payload": {"cmd": "get", "reqid": f"sunday-poll-{int(time.time())}", "objects": [{"type": "devices", "data": []}]},
        }
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(f"{self._hub_rest}/device/get-status-devices", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    data = await r.json(content_type=None)
            for dev in data.get("data", []):
                self._ingest(dev)
            print(f"[Hogar] Polled {len(self._states)} device states.")
        except Exception as e:
            print(f"[Hogar] Poll error: {e}")

    async def _poll_loop(self) -> None:
        """Re-poll REST every 15 seconds to keep state fresh."""
        while self._running:
            await asyncio.sleep(15)
            await self._poll_all_states()

    # ── WebSocket ─────────────────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        sio = self._sio

        @sio.event
        async def connect():
            print("[Hogar] WS connected, joining room...")
            await sio.emit("joinRoom", [self._user_id, self._home_id])

        @sio.on("status")
        async def on_status(data):
            if isinstance(data, str):
                data = json.loads(data)
            objects = data.get("payload", {}).get("objects", [])
            for obj in objects:
                if obj.get("type") == "devices":
                    for dev in obj.get("data", []):
                        self._ingest(dev)

        @sio.event
        async def disconnect():
            print("[Hogar] WS disconnected.")

    async def _ws_loop(self) -> None:
        while self._running:
            try:
                await self._sio.connect(
                    f"{self._hub_ws}?auth_token={self._token}",
                    transports=["websocket"],
                    wait_timeout=10,
                )
                await self._sio.wait()
            except Exception as e:
                print(f"[Hogar] WS error: {e} — reconnecting in 5s")
                await asyncio.sleep(5)

    # ── State ingestion ───────────────────────────────────────────────────────

    def _ingest(self, dev: dict) -> None:
        devid = dev.get("devid", "")
        name = self._id_to_name.get(devid)
        if not name:
            return  # unmapped device, ignore
        states = dev.get("states", {})
        entry: dict = {}
        if "OnOff" in states:
            entry["on"] = states["OnOff"].get("on", False)
        if "Brightness" in states:
            entry["brightness"] = states["Brightness"].get("brightness", 0)
        if "Speed" in states:
            entry["speed"] = states["Speed"].get("speed", 0)
        if "OpenClose" in states:
            entry["open"] = states["OpenClose"].get("open", False)
        if entry:
            self._states[name] = entry
            print(f"[Hogar] {name} → {entry}")
