import asyncio, json, websockets
from urllib.parse import urlencode
import inspect

class Signaling:
    def __init__(self, ws_url, pubkey):
        # ensure the pubkey is URL-encoded so + and / are safe
        qs = urlencode({"pubkey": pubkey})
        self.ws_url = f"{ws_url}?{qs}"
        self.ws = None
        self.handlers = {}

    async def connect(self):
        # Loud, fail-fast connection so you can see if the viewer can reach the backend
        print("[ws-connecting]", self.ws_url, flush=True)
        try:
            self.ws = await asyncio.wait_for(websockets.connect(self.ws_url), timeout=7)
            print("[ws-connected]", self.ws_url, flush=True)
        except Exception as e:
            print("[ws-connect-error]", repr(e), flush=True)
            raise

    async def send(self, obj):
        print("[ws-out]", obj.get("type"), flush=True)  # DEBUG
        await self.ws.send(json.dumps(obj))

    def on(self, mtype, cb):
        # cb may be sync or async; loop() handles both
        self.handlers[mtype] = cb

    async def loop(self):
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                except Exception:
                    continue
                t = data.get("type")
                cb = self.handlers.get(t)
                if cb:
                    await cb(data) if asyncio.iscoroutinefunction(cb) else cb(data)
        except asyncio.CancelledError:
            # don’t propagate; host/viewer will decide when to exit
            return
        except Exception as e:
            print("[ws-loop] error:", e, flush=True)
            return

