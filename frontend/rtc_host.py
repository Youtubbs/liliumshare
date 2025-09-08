#!/usr/bin/env python3
import argparse, asyncio, json, os, sys, time, fractions
from typing import Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, MediaStreamTrack
import numpy as np
try:
    import cv2  # only used for nicer synthetic visuals; optional
except Exception:
    cv2 = None
from av import VideoFrame

from signaling import Signaling

VIDEO_MODE = os.getenv("LILIUM_VIDEO_MODE", "portal").strip().lower()
KEYS_PATH = os.path.expanduser("~/.liliumshare/keys.json")


def load_pubkey():
    try:
        with open(KEYS_PATH, "r") as f:
            return json.load(f)["public"]
    except Exception as e:
        print("Could not read host pubkey at", KEYS_PATH, "error:", e, flush=True)
        sys.exit(1)


class SyntheticVideoTrack(MediaStreamTrack):
    """
    Simple synthetic generator: 1280x720 frames with alternating colored frame and a moving bar.
    Guaranteed to produce frames even if portal/camera fail.
    """
    kind = "video"

    def __init__(self, fps: int = 20, width: int = 1280, height: int = 720):
        super().__init__()
        self._fps = fps
        self._w = width
        self._h = height
        self._time_base = fractions.Fraction(1, fps)
        self._ts = 0

    async def recv(self):
        self._ts += 1
        t = time.time()

        # base image
        img = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        # alternating border color
        if int(t * 2) % 2 == 0:
            color = (0, 255, 0)
        else:
            color = (0, 0, 255)

        # draw border
        thick = 30
        img[:thick, :] = color
        img[-thick:, :] = color
        img[:, :thick] = color
        img[:, -thick:] = color

        # moving bar
        x = int((t * 120) % (self._w - 200))
        img[100:200, x:x + 200] = (255, 255, 255)

        # optional text via OpenCV
        if cv2 is not None:
            cv2.putText(img, "LiliumShare Synthetic", (50, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 0), 2)

        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = self._ts
        frame.time_base = self._time_base

        # pace the generator
        await asyncio.sleep(1.0 / self._fps)
        return frame


def _make_video_track() -> MediaStreamTrack:
    """
    Try the requested mode; if anything goes wrong, fall back to SyntheticVideoTrack.
    Modes:
      - portal     (xdg-desktop-portal via your screen_capture)
      - camera     (future: cv2 capture; for now synthetic fallback)
      - synthetic  (always works)
    """
    mode = VIDEO_MODE
    if mode == "synthetic":
        print("[host/capture] using synthetic frames (forced by env)", flush=True)
        return SyntheticVideoTrack()

    if mode == "portal":
        try:
            from screen_capture import ScreenTrack
            v = ScreenTrack(fps=20)
            # start_capture() may raise; we treat *any* exception as fallback
            # Some backends “succeed” but don’t produce frames; Synthetic still saves the day:
            try:
                loop = asyncio.get_event_loop()
                # Ensure start actually runs now; if it fails, we'll see the exception.
                loop.run_until_complete(v.start_capture()) if loop.is_running() is False else None
            except RuntimeError:
                # When already in an event loop, just trust deferred start
                pass
            print("[host/capture] portal OK (or will try); if frames stall, fallback is available via env LILIUM_VIDEO_MODE=synthetic", flush=True)
            return v
        except Exception as e:
            print("[host/capture] portal failed, falling back to synthetic:", e, flush=True)
            return SyntheticVideoTrack()

    if mode == "camera":
        # If you want real camera capture here, we can wire it;
        # for now, prefer a guaranteed working path:
        print("[host/capture] camera mode not wired; using synthetic fallback", flush=True)
        return SyntheticVideoTrack()

    # Unknown mode -> synthetic
    print(f"[host/capture] unknown LILIUM_VIDEO_MODE={mode}; using synthetic", flush=True)
    return SyntheticVideoTrack()


async def run_host(ws_url: str, pubkey_override: Optional[str]):
    host_pubkey = pubkey_override or load_pubkey()

    sig = Signaling(ws_url, host_pubkey)
    await sig.connect()
    print("Connected as", host_pubkey, flush=True)

    pc = RTCPeerConnection()

    @pc.on("iceconnectionstatechange")
    def _on_ice_state():
        print("[host] ICE state:", pc.iceConnectionState, flush=True)

    @pc.on("connectionstatechange")
    def _on_conn_state():
        print("[host] PC state:", pc.connectionState, flush=True)

    # Prepare video track now (robust fallback inside)
    video = _make_video_track()
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
        perms = msg.get("permissions", {})
        _current_viewer[0] = viewer
        print("[host] incoming-join from", viewer, "perms=", perms, flush=True)

        await pc.setLocalDescription(await pc.createOffer())
        await sig.send({"type": "offer", "to": viewer, "sdp": pc.localDescription.sdp})
        print("[host] sent offer", flush=True)

    async def on_answer(msg):
        sdp = msg.get("sdp")
        if not sdp:
            print("[host] answer missing sdp", flush=True)
            return
        print("[host] got answer; applying", flush=True)
        await pc.setRemoteDescription(RTCSessionDescription(sdp, "answer"))
        print("[host] set remote answer", flush=True)

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
            print("[host] addIceCandidate error:", e, flush=True)

    sig.on("incoming-join", on_incoming)
    sig.on("answer", on_answer)
    sig.on("ice", on_ice_from_viewer)
    sig.on("hello", lambda m: None)

    try:
        await sig.loop()
    except asyncio.CancelledError:
        print("[host] signaling cancelled; keeping process alive so frames keep flowing", flush=True)
        # Keep running; the PC will keep sending until process exits
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
    ap.add_argument("--ws", default="ws://localhost:8081/ws")
    ap.add_argument("--pubkey", help="override host pubkey (base64)")
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run_host(args.ws, args.pubkey))
    except KeyboardInterrupt:
        sys.exit(0)
