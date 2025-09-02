import argparse, asyncio, json, os, sys, time
import numpy as np

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, MediaStreamTrack
from av import VideoFrame

try:
    from screen_capture import ScreenTrack  # now portal-first inside
except Exception as e:
    print("screen_capture import failed:", e)
    raise

from signaling import Signaling

KEYS_PATH = os.path.expanduser("~/.liliumshare/keys.json")

def load_pubkey():
    try:
        with open(KEYS_PATH, "r") as f:
            return json.load(f)["public"]
    except Exception as e:
        print("Could not read host pubkey at", KEYS_PATH, "error:", e)
        sys.exit(1)

class Dummy(MediaStreamTrack):
    kind = "video"
    def __init__(self): super().__init__()
    async def recv(self): await asyncio.sleep(1/30.0); 

async def run_host(ws_url: str, pubkey_override: str | None):
    host_pubkey = pubkey_override or load_pubkey()

    sig = Signaling(ws_url, host_pubkey)
    await sig.connect()
    print("Connected as", host_pubkey)

    pc = RTCPeerConnection()
    @pc.on("iceconnectionstatechange")
    def _on_ice_state():
        print("[host] ICE state:", pc.iceConnectionState)
    @pc.on("connectionstatechange")
    def _on_conn_state():
        print("[host] PC state:", pc.connectionState)

    # Add video track now
    video = ScreenTrack(fps=20)
    await video.start_capture()
    if os.environ.get("XDG_SESSION_TYPE"):
        print(f"[host/capture] session={os.environ.get('XDG_SESSION_TYPE')} desktop={os.environ.get('XDG_CURRENT_DESKTOP')}")
    pc.addTrack(video)

    @pc.on("icecandidate")
    def on_ice(candidate):
        if candidate is None:
            return
        asyncio.create_task(sig.send({
            "type": "ice",
            "to": _current_viewer[0] or "",
            "candidate": candidate.to_sdp(),
            "sdpMid": candidate.sdpMid,
            "sdpMLineIndex": candidate.sdpMLineIndex,
        }))

    _current_viewer = [None]

    async def on_incoming(msg):
        viewer = msg.get("viewer")
        # Permissions cache per viewer (populated by incoming-join)
        perms = msg.get("permissions", {})
        _current_viewer[0] = viewer
        print("[host] incoming-join from", viewer, "perms=", perms)

        # Auto-accept: host creates offer and sends to viewer
        await pc.setLocalDescription(await pc.createOffer())
        await sig.send({"type": "offer", "to": viewer, "sdp": pc.localDescription.sdp})
        print("[host] sent offer")

    async def on_answer(msg):
        sdp = msg.get("sdp")
        if not sdp:
            print("[host] answer missing sdp")
            return
        print("[host] got answer; applying")
        await pc.setRemoteDescription(RTCSessionDescription(sdp, "answer"))
        print("[host] set remote answer")

    async def on_ice_from_viewer(msg):
        cand = msg.get("candidate")
        sdpMid = msg.get("sdpMid")
        sdpMLineIndex = msg.get("sdpMLineIndex")
        if cand is None:
            return
        try:
            await pc.addIceCandidate(RTCIceCandidate(
                sdpMid=sdpMid, sdpMLineIndex=sdpMLineIndex, candidate=cand))
        except Exception as e:
            print("[host] addIceCandidate error:", e)

    sig.on("incoming-join", on_incoming)
    sig.on("answer", on_answer)
    sig.on("ice", on_ice_from_viewer)
    sig.on("hello", lambda m: None)

    try:
        await sig.loop()
    finally:
        await pc.close()

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://localhost:8081/ws")
    ap.add_argument("--pubkey", help="override host pubkey (base64)")
    return ap.parse_args()

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_host(args.ws, args.pubkey))

