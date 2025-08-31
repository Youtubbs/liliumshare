import asyncio, json, websockets
from urllib.parse import urlencode

class Signaling:
    def __init__(self, ws_url, pubkey):
        # ensure the pubkey is URL-encoded so + and / are safe
        qs = urlencode({"pubkey": pubkey})
        self.ws_url = f"{ws_url}?{qs}"
        self.ws = None
        self.handlers = {}

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url)

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    def on(self, mtype, cb):
        self.handlers[mtype] = cb

    async def loop(self):
        async for message in self.ws:
            try:
                data = json.loads(message)
            except:
                continue
            h = self.handlers.get(data.get("type"))
            if h:
                await h(data)

