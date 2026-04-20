import aiohttp


class HAClient:
    """Async Home Assistant REST API client."""

    def __init__(self, url: str, token: str):
        self._url = url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self._url}/api/states", headers=self._headers) as r:
                return await r.json()

    async def get_state(self, entity_id: str) -> dict:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self._url}/api/states/{entity_id}", headers=self._headers) as r:
                return await r.json()

    async def call_service(self, domain: str, service: str, data: dict = {}) -> str:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{self._url}/api/services/{domain}/{service}",
                headers=self._headers,
                json=data,
            ) as r:
                return "ok" if r.status in (200, 201) else f"HA returned {r.status}"
