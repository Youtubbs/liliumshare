import asyncio, json, argparse, cv2
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole
from signaling import Signaling
from screen_capture import ScreenTrack
from audio_capture import MicrophoneTrack
from input_inject import apply_event

async def run_host(pubkey, ws_url):
    sig = Signaling(ws_url, pubkey)
    await sig.connect()

    # Permissions cache per viewer (populated by incoming-join)
    permissions_map = {}

    pc = None
    dc = None

    async def on_message(msg):
        nonlocal pc, dc
        t = msg.get("type")

        if t == "incoming-join":
            viewer = msg["viewer"]
            perms = msg.get("permissions", {})
            permissions_map[viewer] = perms

            # Auto-accept: host creates offer and sends to viewer
            if pc is None or pc.connectionState in ("closed","failed"):
                pc = RTCPeerConnection()
                # Data channel for input events
                dc = pc.createDataChannel("input")
                # Screen + audio tracks
                pc.addTrack(ScreenTrack())
                pc.addTrack(MicrophoneTrack())

                @dc.on("message")
                def on_dc_message(data):
                    try:
                        evt = json.loads(data)
                        apply_event(evt, permissions_map.get(viewer, {}))
                    except Exception as e:
                        print("input err:", e)

                @pc.on("icecandidate")
                async def on_ice(ev):
                    if ev.candidate:
                        await sig.send({"type":"ice","to":viewer,"candidate":ev.candidate.to_sdp(),"sdpMid":ev.candidate.sdp_mid,"sdpMLineIndex":ev.candidate.sdp_mline_index})

            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await sig.send({"type":"offer","to":viewer,"sdp":pc.localDescription.sdp,"typeSdp":pc.localDescription.type})
            print("Sent offer to viewer")

        elif t == "answer":
            if pc:
                await pc.setRemoteDescription(RTCSessionDescription(sdp=msg["sdp"], type=msg.get("typeSdp","answer")))
                print("Set remote description (answer)")

        elif t == "ice":
            # Ignored in aiortc simple demo; ICE trickle can be handled with addIceCandidate if needed
            pass

        elif t == "hello":
            print("Connected as", msg.get("you"))

    sig.on("incoming-join", on_message)
    sig.on("answer", on_message)
    sig.on("ice", on_message)
    sig.on("hello", on_message)

    await sig.loop()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubkey", help="Your public key (base64). If omitted, read from keys.json")
    ap.add_argument("--ws", default="ws://localhost:8080/ws")
    args = ap.parse_args()

    if not args.pubkey:
        import json, pathlib
        data = json.loads((pathlib.Path.home()/".liliumshare/keys.json").read_text())
        args.pubkey = data["public"]

    asyncio.run(run_host(args.pubkey, args.ws))
