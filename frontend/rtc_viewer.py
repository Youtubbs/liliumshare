#!/usr/bin/env python3
import argparse, json, os, sys, pathlib, threading, queue, asyncio, time
import numpy as np
import cv2  # for JPEG decode
from typing import Optional
from urllib.parse import urlparse, urlunparse, urlencode
from urllib.request import urlopen
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.contrib.media import MediaBlackhole
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
_DEFAULT_HTTP_BASE = _NETCFG["http_base"]
_DEFAULT_WS_BASE   = _NETCFG["ws_base"]
# ------------------------------------------

# display uses pygame/SDL (reliable on Wayland)
KEYS_PATH = pathlib.Path.home() / ".liliumshare" / "keys.json"

def http_base_from_ws(ws_url: str) -> str:
    if not ws_url:
        return _DEFAULT_HTTP_BASE
    u = urlparse(ws_url)
    scheme = "https" if u.scheme == "wss" else "http"
    return urlunparse((scheme, u.netloc, "", "", "", ""))

def load_pubkey_flexible(ws_url: str, cli_pubkey: str | None) -> str:
    if cli_pubkey:
        return cli_pubkey
    env_pk = os.getenv("LILIUM_PUBKEY")
    if env_pk:
        return env_pk
    try:
        data = json.loads(KEYS_PATH.read_text()); return data["public"]
    except Exception:
        pass
    nick = os.getenv("LILIUM_NICK")
    if nick:
        base = http_base_from_ws(ws_url)
        url = f"{base}/api/users/by-nickname?{urlencode({'nickname': nick})}"
        try:
            with urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode())
                return data["pubkey"]
        except Exception as e:
            print(f"Nickname lookup failed for {nick}: {e}", flush=True)
    print("No pubkey found. Provide --pubkey or set LILIUM_PUBKEY (or create ~/.liliumshare/keys.json).", flush=True)
    sys.exit(1)

class ViewerApp:
    def __init__(self, title="LiliumShare Viewer"):
        self.title = title
        self.frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.connected = threading.Event()

    def push(self, bgr: np.ndarray):
        try:
            if self.frames.full():
                self.frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frames.put_nowait(bgr)
        except queue.Full:
            pass

async def _wait_ice_gathering_complete(pc: RTCPeerConnection):
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.Event()
    @pc.on("icegatheringstatechange")
    def _on_gather():
        if pc.iceGatheringState == "complete":
            done.set()
    await done.wait()

async def _viewer_async(app: ViewerApp, host_pubkey: str, ws_url: str, viewer_pubkey: str):
    sig = Signaling(ws_url, viewer_pubkey)
    print("[viewer-async] connecting WS…", flush=True)
    await sig.connect()
    print("[viewer-async] WS connected", flush=True)

    pc = RTCPeerConnection()

    @pc.on("iceconnectionstatechange")
    def _on_ice_state():
        print("[viewer-async] ICE state:", pc.iceConnectionState, flush=True)
        if pc.iceConnectionState in ("connected", "completed"):
            app.connected.set()

    @pc.on("connectionstatechange")
    def _on_pc_state():
        print("[viewer-async] PC state:", pc.connectionState, flush=True)

    audio_sink = MediaBlackhole()
    audio_started = False

    @pc.on("icecandidate")
    def on_ice(candidate):
        if candidate is None:
            return
        asyncio.create_task(sig.send({
            "type": "ice",
            "to": host_pubkey,
            "candidate": candidate.to_sdp(),
            "sdpMid": candidate.sdpMid,
            "sdpMLineIndex": candidate.sdpMLineIndex,
        }))

    @pc.on("track")
    async def on_track(track):
        nonlocal audio_started
        print("[viewer-async] Track received:", track.kind, flush=True)
        if track.kind == "audio":
            audio_sink.addTrack(track)
            if not audio_started:
                await audio_sink.start()
                audio_started = True
            return
        if track.kind == "video":
            async def pump():
                print("[viewer-async] video pump started", flush=True)   # <— add this
                frames = 0
                try:
                    while not app.stop.is_set():
                        frame = await track.recv()
                        img = frame.to_ndarray(format="bgr24")
                        app.push(img)
                        frames += 1
                        if frames % 60 == 0:
                            print(f"[viewer-async] pushed {frames} frames", flush=True)
                except Exception as e:
                    if not app.stop.is_set():
                        print("[viewer-async] pump error:", e, flush=True)
            asyncio.create_task(pump())

    @pc.on("datachannel")
    def on_datachannel(dc):
        print(f"[viewer-async] datachannel opened:", dc.label, flush=True)
        if dc.label == "video-fallback":
            counter = {"n": 0}
            def _on_msg(msg):
                if isinstance(msg, (bytes, bytearray)):
                    try:
                        arr = np.frombuffer(msg, dtype=np.uint8)
                        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
                        if img is not None:
                            app.push(img)
                            counter["n"] += 1
                            if counter["n"] % 60 == 0:
                                print(f"[viewer-async] fallback frames: {counter['n']}", flush=True)
                    except Exception as e:
                        print("[viewer-async] fallback decode error:", e, flush=True)
            dc.on("message", _on_msg)

    async def on_message(msg):
        t = msg.get("type")
        if t == "offer":
            sdp = msg.get("sdp")
            if not sdp:
                print("[viewer-async] Offer missing SDP", flush=True)
                return
            print("[viewer-async] got offer; creating answer", flush=True)
            await pc.setRemoteDescription(RTCSessionDescription(sdp, "offer"))
            await pc.setLocalDescription(await pc.createAnswer())
            await _wait_ice_gathering_complete(pc)
            await sig.send({"type": "answer", "to": host_pubkey, "sdp": pc.localDescription.sdp})
            print("[viewer-async] sent answer", flush=True)
        elif t == "ice":
            cand = msg.get("candidate"); sdpMid = msg.get("sdpMid"); idx = msg.get("sdpMLineIndex")
            if cand is not None:
                try:
                    await pc.addIceCandidate(RTCIceCandidate(sdpMid=sdpMid, sdpMLineIndex=idx, candidate=cand))
                except Exception as e:
                    print("[viewer-async] addIceCandidate error:", e, flush=True)
        elif t == "join-denied":
            print("[viewer-async] Join denied:", msg.get("reason"), flush=True)

    sig.on("offer", on_message)
    sig.on("ice", on_message)
    sig.on("join-denied", on_message)
    async def _hello(_): return
    sig.on("hello", _hello)

    # Send join-request so server notifies host
    print("[viewer-async] sending join-request", flush=True)
    await sig.send({"type": "join-request", "host": host_pubkey, "viewer": viewer_pubkey})

    async def stopper():
        while not app.stop.is_set():
            await asyncio.sleep(0.1)
        try:
            await pc.close()
        except:
            pass
        try:
            await sig.ws.close()
        except:
            pass

    await asyncio.gather(sig.loop(), stopper())

def _start_worker(app: ViewerApp, host_pubkey: str, ws_url: str, viewer_pubkey: str):
    asyncio.run(_viewer_async(app, host_pubkey, ws_url, viewer_pubkey))

def _pygame_gui_loop(app: ViewerApp):
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        import pygame
    except Exception as e:
        print("[viewer-gui] pygame not installed. pip install pygame — error:", e, flush=True)
        while not app.stop.is_set():
            time.sleep(0.1)
        return

    pygame.init()
    try:
        win_w, win_h = 1280, 720
        screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
        pygame.display.set_caption(app.title)
        clock = pygame.time.Clock()

        font = None
        try:
            pygame.font.init()
            font = pygame.font.SysFont(None, 24)
        except Exception:
            pass

        def draw_waiting():
            screen.fill((30, 30, 30))
            if font:
                txt = font.render("Waiting for video… (Q/Esc to quit)", True, (220, 220, 220))
                screen.blit(txt, (20, win_h // 2))
            pygame.display.flip()

        draw_waiting()
        last_surface = None
        running = True

        while running and not app.stop.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                if event.type == pygame.VIDEORESIZE:
                    win_w, win_h = event.w, event.h
                    screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)

            try:
                frame = app.frames.get_nowait()
                rgb = frame[:, :, ::-1]
                h, w, _ = rgb.shape
                import pygame  # local import ok
                surf = pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB")
                last_surface = surf
            except queue.Empty:
                pass

            if last_surface is None:
                draw_waiting()
            else:
                sw, sh = last_surface.get_size()
                scale = min(win_w / sw, win_h / sh)
                tw, th = max(1, int(sw * scale)), max(1, int(sh * scale))
                import pygame
                blit = pygame.transform.smoothscale(last_surface, (tw, th))
                screen.fill((0, 0, 0))
                screen.blit(blit, ((win_w - tw)//2, (win_h - th)//2))
                pygame.display.flip()

            clock.tick(60)
    finally:
        try:
            import pygame
            pygame.quit()
        except Exception:
            pass

def run_viewer(host_pubkey: str, ws_url: str, pubkey_cli: Optional[str]):
    viewer_pubkey = load_pubkey_flexible(ws_url, pubkey_cli)
    print("[viewer] my pubkey:", viewer_pubkey, flush=True)

    app = ViewerApp()
    worker = threading.Thread(target=_start_worker, args=(app, host_pubkey, ws_url, viewer_pubkey), daemon=True)
    worker.start()
    print("[viewer] worker thread started", flush=True)

    _pygame_gui_loop(app)

    app.stop.set()
    try:
        worker.join(timeout=2.0)
    except:
        pass

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="Host public key")
    ap.add_argument("--ws", default=_DEFAULT_WS_BASE)
    ap.add_argument("--pubkey", help="Override identity (base64 RSA public key)")
    return ap.parse_args()

if __name__ == "__main__":
    args = parse_args()
    run_viewer(args.host, args.ws, args.pubkey)