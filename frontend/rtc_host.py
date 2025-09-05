#!/usr/bin/env python3
import argparse, asyncio, json, os, sys
from typing import Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, MediaStreamTrack

try:
    from screen_capture import ScreenTrack
except Exception as e:
    print("screen_capture import failed:", e)
    raise

from signaling import Signaling

# --- centralized network config loader ---
import json as _json
from pathlib import Path as _Path

def _load_netcfg():
    env_path = os.getenv("LILIUM_NETCFG")
    if env_path:
        p = _Path(env_path)
    else:
        here = _Path(__file__).resolve()
        candidates = [
            here.parent / "backend" / "network_config.json",
            here.parent.parent / "backend" / "network_config.json",
            here.parents[2] / "backend" / "network_config.json",
        ]
        p = next((c for c in candidates if c.exists()), None)
    data = {}
    if p and p.exists():
        try:
            data = _json.loads(p.read_text())
        except Exception:
            data = {}
    be = data.get("backend", {})
    http_base = be.get("http_base", "http://localhost:18080")
    ws_base   = be.get("ws_base",   http_base.replace("http://","ws://").replace("https://","wss://").rstrip("/") + "/ws")
    return {"http_base": http_base, "ws_base": ws_base}

_NETCFG = _load_netcfg()
_DEFAULT_WS_BASE = _NETCFG["ws_base"]
# ------------------------------------------

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
    async def recv(self): await asyncio.sleep(1/30.0)

async def run_host(ws_url: str, pubkey_override: Optional[str]):
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

    _current_viewer = [None]

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
    except asyncio.CancelledError:
        print("[host] signaling cancelled; staying alive until process exit", flush=True)
        # Keep process alive so existing PeerConnection can keep sending frames.
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print("[host] signaling loop error:", e, flush=True)
        while True:
            await asyncio.sleep(1)
    finally:
        await pc.close()

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default=_DEFAULT_WS_BASE)
    ap.add_argument("--pubkey", help="override host pubkey (base64)")
    return ap.parse_args()

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_host(args.ws, args.pubkey))
