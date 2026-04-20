"""
Kasa/Tapo client for L900 light strips (top light, panels).
Uses python-kasa for local LAN control.
"""
import asyncio
from kasa import Discover, Device
from kasa.iot import IotStrip


class KasaClient:
    def __init__(self, username: str, password: str, devices: dict):
        """devices: {friendly_name: ip} or {friendly_name: {ip, username, password}}
        Per-device credentials override the global username/password."""
        self._default_username = username
        self._default_password = password
        # Normalise: always store as {name: {ip, username, password}}
        self._device_configs: dict[str, dict] = {}
        for k, v in (devices or {}).items():
            name = k.lower()
            if isinstance(v, str):
                self._device_configs[name] = {"ip": v, "username": username, "password": password}
            else:
                self._device_configs[name] = {
                    "ip": v["ip"],
                    "username": v.get("username", username),
                    "password": v.get("password", password),
                }
        self._devices: dict[str, Device] = {}

    async def start(self) -> None:
        """Connect to all configured devices."""
        for name, cfg in self._device_configs.items():
            try:
                dev = await Discover.discover_single(
                    cfg["ip"],
                    username=cfg["username"],
                    password=cfg["password"],
                )
                await dev.update()
                self._devices[name] = dev
                print(f"[Kasa] Connected: {name} ({cfg['ip']}) — {dev.alias}")
            except Exception as e:
                print(f"[Kasa] Failed to connect {name} ({cfg['ip']}): {e}")

    def get_device(self, name: str) -> Device | None:
        return self._devices.get(name.lower())

    async def get_state(self, name: str) -> dict:
        dev = self.get_device(name)
        if not dev:
            return {}
        await dev.update()
        state = {"on": dev.is_on}
        if hasattr(dev, "brightness"):
            state["brightness"] = dev.brightness
        if hasattr(dev, "color_temp"):
            state["color_temp"] = dev.color_temp
        return state

    async def set_on(self, name: str, on: bool) -> bool:
        dev = self.get_device(name)
        if not dev:
            return False
        try:
            if on:
                await dev.turn_on()
            else:
                await dev.turn_off()
            return True
        except Exception as e:
            print(f"[Kasa] set_on error for {name}: {e}")
            return False

    async def set_brightness(self, name: str, brightness: int) -> bool:
        dev = self.get_device(name)
        if not dev:
            return False
        try:
            await dev.set_brightness(brightness)
            return True
        except Exception as e:
            print(f"[Kasa] brightness error for {name}: {e}")
            return False

    async def set_color(self, name: str, hue: int, saturation: int, brightness: int | None = None) -> bool:
        dev = self.get_device(name)
        if not dev:
            return False
        try:
            h, s, v = hue, saturation, brightness if brightness is not None else 100
            await dev.set_hsv(h, s, v)
            return True
        except Exception as e:
            print(f"[Kasa] color error for {name}: {e}")
            return False

    async def set_color_temp(self, name: str, kelvin: int, brightness: int | None = None) -> bool:
        dev = self.get_device(name)
        if not dev:
            return False
        try:
            if brightness is not None:
                await dev.set_brightness(brightness)
            await dev.set_color_temp(kelvin)
            return True
        except Exception as e:
            print(f"[Kasa] color_temp error for {name}: {e}")
            return False

    def get_all_states(self) -> dict[str, dict]:
        """Return last known states (cached). Call refresh_all_states() for fresh data."""
        states = {}
        for name, dev in self._devices.items():
            state = {"on": dev.is_on}
            if hasattr(dev, "brightness"):
                state["brightness"] = dev.brightness
            states[name] = state
        return states

    async def refresh_all_states(self) -> dict[str, dict]:
        """Fetch fresh state from all devices and return."""
        await asyncio.gather(*[dev.update() for dev in self._devices.values()], return_exceptions=True)
        return self.get_all_states()
