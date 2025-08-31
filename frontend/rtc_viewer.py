import asyncio, json, argparse, cv2, numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRecorder
from signaling import Signaling

last_xy = {"x": None, "y": None}
dc_ref = {"dc": None}  # store data channel reference for callbacks

def send_evt(evt):
    dc = dc_ref["dc"]
    if dc and dc.readyState == "open":
        try:
            dc.send(json.dumps(evt))
        except Exception as e:
            print("send err:", e)

def on_mouse(event, x, y, flags, param):
    # Compute relative movement (dx, dy)
    if last_xy["x"] is None:
        last_xy["x"], last_xy["y"] = x, y
    dx, dy = x - last_xy["x"], y - last_xy["y"]
    last_xy["x"], last_xy["y"] = x, y

    if event == cv2.EVENT_MOUSEMOVE and (dx or dy):
        send_evt({"type": "mouse", "action": "move", "dx": int(dx), "dy": int(dy)})
    elif event == cv2.EVENT_LBUTTONDOWN:
        send_evt({"type": "mouse", "action": "click", "button": "left", "down": True})
    elif event == cv2.EVENT_LBUTTONUP:
        send_evt({"type": "mouse", "action": "click", "button": "left", "down": False})
    elif event == cv2.EVENT_RBUTTONDOWN:
        send_evt({"type": "mouse", "action": "click", "button": "right", "down": True})
    elif event == cv2.EVENT_RBUTTONUP:
        send_evt({"type": "mouse", "action": "click", "button": "right", "down": False})
    elif event == cv2.EVENT_MOUSEWHEEL:
        # vertical wheel; flags > 0 means up on some platforms
        delta = 120 if flags > 0 else -120
        send_evt({"type": "mouse", "action": "scroll", "dx": 0, "dy": delta})

async def run_viewer(pubkey, host_pubkey, ws_url):
    sig = Signaling(ws_url, pubkey)
    await sig.connect()

    pc = RTCPeerConnection()

    @pc.on("track")
    def on_track(track):
        print("Track received:", track.kind)
        # For video, display frames using OpenCV
        if track.kind == "video":
            async def show():
                cv2.namedWindow("LiliumShare Viewer", cv2.WINDOW_NORMAL)
                cv2.setMouseCallback("LiliumShare Viewer", on_mouse)
                while True:
                    frame = await track.recv()
                    img = frame.to_ndarray(format="bgr24")
                    cv2.imshow("LiliumShare Viewer", img)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        break
                    elif key == 27:  # ESC
                        send_evt({"type":"keyboard","action":"key","key":"esc","down":True})
                        send_evt({"type":"keyboard","action":"key","key":"esc","down":False})
                    elif key == ord('\r') or key == 13:
                        send_evt({"type":"keyboard","action":"key","key":"enter","down":True})
                        send_evt({"type":"keyboard","action":"key","key":"enter","down":False})
                    # WASD demo: send wasd letters
                    elif key in (ord('w'), ord('a'), ord('s'), ord('d')):
                        ch = chr(key)
                        send_evt({"type":"keyboard","action":"type","text":ch})
                    # Alt-Tab demo (only works if host set immersion=true)
                    elif key == ord('t'):
                        send_evt({"type":"keyboard","action":"key","key":"alt","down":True})
                        send_evt({"type":"keyboard","action":"key","key":"tab","down":True})
                        send_evt({"type":"keyboard","action":"key","key":"tab","down":False})
                        send_evt({"type":"keyboard","action":"key","key":"alt","down":False})
                cv2.destroyAllWindows()
            asyncio.create_task(show())
        elif track.kind == "audio":
            # For brevity, discard or implement playback via sounddevice
            pass

    # Create data channel for input events
    dc = pc.createDataChannel("input")
    dc_ref["dc"] = dc

    async def on_message(msg):
        t = msg.get("type")
        if t == "offer":
            await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg.get("typeSdp","offer")))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await sig.send({"type":"answer","to":host_pubkey,"sdp":pc.localDescription.sdp,"typeSdp":pc.localDescription.type})
        elif t == "hello":
            print("Connected as", msg.get("you"))

    sig.on("offer", on_message)
    sig.on("hello", on_message)

    async def on_denied(msg):
        print("Join denied:", msg.get("reason"))
    sig.on("join-denied", on_denied)

    # Send join-request so server notifies host
    await sig.send({"type":"join-request","host":host_pubkey,"viewer":pubkey})

    await sig.loop()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubkey", help="Your public key (base64). If omitted, read from keys.json")
    ap.add_argument("--host", required=True, help="Host's public key (base64)")
    ap.add_argument("--ws", default="ws://localhost:8080/ws")
    args = ap.parse_args()

    if not args.pubkey:
        import json, pathlib
        data = json.loads((pathlib.Path.home()/".liliumshare/keys.json").read_text())
        args.pubkey = data["public"]

    asyncio.run(run_viewer(args.pubkey, args.host, args.ws))
