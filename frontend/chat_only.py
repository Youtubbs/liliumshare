#!/usr/bin/env python3
# Pure messaging session riding the WS relay: start this to chat "any time".
# Content is E2E encrypted; server relays only opaque blobs.

import argparse
import asyncio
import base64
import hmac
import json
import os
import sys
from hashlib import sha256

from nacl.public import PrivateKey, PublicKey
from nacl.bindings import crypto_scalarmult

from messaging import (
    Signaling,               # re-exported from signaling.py usage pattern
    spawn_chat_window,
    hkdf_sha256,
    b64e, b64d,
    http_base_from_ws,
    get_connkey,             # NOTE: directional fetch (host, friend)
)

KEYS_PATH = os.path.expanduser("~/.liliumshare/keys.json")


def load_my_pub():
    with open(KEYS_PATH, "r") as f:
        return json.load(f)["public"]


class WSChat:
    """
    Lightweight, WS-relayed chat with explicit role declaration.
    Initiator (this process when --initiate is passed) announces role='host'.
    The responder replies role='viewer'. Both sides fetch the connkey in the
    direction specified by the SENDER's role. This removes ambiguity when
    both (host,friend) and (friend,host) rows exist.
    """
    def __init__(self, ws_url: str, me: str, peer: str):
        self.ws_url = ws_url
        self.me = me
        self.peer = peer
        self.sig = Signaling(ws_url, me)

        self.sk = PrivateKey.generate()
        self.pk = self.sk.public_key
        self.key = None  # derived AEAD key
        self.loop = None

        print(f"[chat] me={self.me[:10]}… peer={self.peer[:10]}… eph_pub={b64e(bytes(self.pk))[:24]}…", flush=True)

    async def connect(self):
        await self.sig.connect()
        # remember the loop we are running on (the main asyncio loop)
        self.loop = asyncio.get_running_loop()

        if hasattr(self, "_set_status"):
            self._set_status("Connected • negotiating…")

        async def on_hello(msg):
            print("[chat/rx] chat-hello", flush=True)
            role = msg.get("role")
            epub = msg.get("epub")
            auth = msg.get("auth")
            if not epub or not auth:
                print("[chat/rx] hello missing fields", flush=True)
                return
            if role not in ("host", "viewer"):
                print("[chat/rx] hello missing/invalid role", flush=True)
                return

            http = http_base_from_ws(self.ws_url)
            try:
                # Sender says they are role=role.
                # If sender is 'host' -> host=peer (their pub), friend=me
                host = self.peer if role == "host" else self.me
                friend = self.me if role == "host" else self.peer
                print(f"[msg/connkey] fetch for incoming role={role} host={host[:10]}… friend={friend[:10]}…", flush=True)
                conn = get_connkey(http, host, friend)
                print("[msg/connkey] OK", flush=True)
            except Exception as e:
                print("[msg/connkey] error:", e, flush=True)
                return

            try:
                expect = hmac.new(b64d(conn), (role + "|" + epub).encode("utf-8"), sha256).digest()
                ok = hmac.compare_digest(b64d(auth), expect)
                print(f"[chat/auth] hello from role={role} -> {'OK' if ok else 'FAIL'}", flush=True)
                if not ok:
                    return
            except Exception as e:
                print("[chat/auth] verify error:", e, flush=True)
                return

            # Derive session key
            self._derive_key(epub, conn)

            # Reply with opposite role
            ack_role = "viewer" if role == "host" else "host"
            try:
                tag = hmac.new(b64d(conn), (ack_role + "|" + b64e(bytes(self.pk))).encode("utf-8"), sha256).digest()
                ack = {
                    "type": "chat-ack",
                    "to": self.peer,
                    "role": ack_role,
                    "epub": b64e(bytes(self.pk)),
                    "auth": b64e(tag),
                }
                print(f"[chat/tx] chat-ack role={ack_role}", flush=True)
                await self.sig.send(ack)
            except Exception as e:
                print("[chat/tx] ack send error:", e, flush=True)

        async def on_ack(msg):
            print("[chat/rx] chat-ack", flush=True)
            if self.key is not None:
                print("[chat] already keyed; ignoring ack", flush=True)
                return

            role = msg.get("role")
            epub = msg.get("epub")
            auth = msg.get("auth")
            if not epub or not auth:
                print("[chat/rx] ack missing fields", flush=True)
                return
            if role not in ("host", "viewer"):
                print("[chat/rx] ack missing/invalid role", flush=True)
                return

            http = http_base_from_ws(self.ws_url)
            try:
                # Sender of ACK claims a role; fetch connkey in that direction.
                host = self.peer if role == "host" else self.me
                friend = self.me if role == "host" else self.peer
                print(f"[msg/connkey] fetch for ack role={role} host={host[:10]}… friend={friend[:10]}…", flush=True)
                conn = get_connkey(http, host, friend)
                print("[msg/connkey] OK", flush=True)
            except Exception as e:
                print("[msg/connkey] error:", e, flush=True)
                return

            try:
                expect = hmac.new(b64d(conn), (role + "|" + epub).encode("utf-8"), sha256).digest()
                ok = hmac.compare_digest(b64d(auth), expect)
                print(f"[chat/auth] ack from role={role} -> {'OK' if ok else 'FAIL'}", flush=True)
                if not ok:
                    return
            except Exception as e:
                print("[chat/auth] verify error:", e, flush=True)
                return

            self._derive_key(epub, conn)

        async def on_msg(msg):
            if not self.key:
                return
            try:
                n = base64.b64decode(msg.get("n", ""))
                c = base64.b64decode(msg.get("c", ""))

                # --- AD must be sender|receiver exactly as sent ---
                sender = msg.get("from", "")
                receiver = msg.get("to", "")
                ad = f"{sender}|{receiver}".encode("utf-8")

                from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt
                pt = crypto_aead_xchacha20poly1305_ietf_decrypt(c, ad, n, self.key)
                text = pt.decode("utf-8", "replace")
                if hasattr(self, "_add_incoming"):
                    self._add_incoming(text)
                else:
                    print("[chat/rx] plaintext:", text, flush=True)
            except Exception as e:
                print("[chat/rx] decrypt error:", e, flush=True)


        self.sig.on("chat-hello", on_hello)
        self.sig.on("chat-ack", on_ack)
        self.sig.on("chat-msg", on_msg)
        self.sig.on("hello", lambda _: print("[chat/ws] hello from server", flush=True))
        asyncio.create_task(self.sig.loop())

    def _derive_key(self, peer_epub_b64: str, conn_key_b64: str):
        try:
            from nacl.bindings import crypto_scalarmult
            peer_pub = PublicKey(b64d(peer_epub_b64))
            shared = crypto_scalarmult(bytes(self.sk), bytes(peer_pub))

            # --- canonicalize the salt input so both sides match ---
            a, b = self.me, self.peer
            canon = f"{a}|{b}" if a <= b else f"{b}|{a}"
            salt = hmac.new(b64d(conn_key_b64), canon.encode("utf-8"), sha256).digest()

            self.key = hkdf_sha256(shared, salt, b"LiliumShare/secure-msg/v1", 32)
            print("[chat/kdf] OK — session key ready", flush=True)
            if hasattr(self, "_set_status"):
                self._set_status("Secure • ready")
        except Exception as e:
            print("[chat/kdf] error:", e, flush=True)
            self.key = None
            if hasattr(self, "_set_status"):
                self._set_status("Error deriving key")


    async def initiate(self):
        # Initiator = 'host' for this 1:1 DM.
        http = http_base_from_ws(self.ws_url)
        try:
            conn = get_connkey(http, self.me, self.peer)
            print("[msg/connkey] initiator fetch host=me friend=peer OK", flush=True)
        except Exception as e:
            print("[chat/connkey] error:", e, flush=True)
            return
        role = "host"
        hello = {
            "type": "chat-hello",
            "to": self.peer,
            "role": role,
            "epub": b64e(bytes(self.pk)),
            "auth": b64e(hmac.new(b64d(conn), (role + "|" + b64e(bytes(self.pk))).encode("utf-8"), sha256).digest()),
        }
        print(f"[chat/tx] chat-hello role={role}", flush=True)
        await self.sig.send(hello)

    def send_text(self, text: str) -> bool:
        if not self.key:
            print("[chat/tx] refused (key not ready)", flush=True)
            return False

        from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_encrypt
        from nacl.utils import random as nacl_random
        try:
            n = nacl_random(24)
            ad = f"{self.me}|{self.peer}".encode("utf-8")
            c = crypto_aead_xchacha20poly1305_ietf_encrypt(text.encode("utf-8"), ad, n, self.key)
            payload = {
                "type": "chat-msg",
                "to": self.peer,
                "from": self.me,
                "n": b64e(n),
                "c": b64e(c),
            }

            # schedule the send on the asyncio loop, even if we're in the Tk thread
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(self.sig.send(payload), self.loop)
            else:
                # fallback (shouldn't happen once connect() ran)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.sig.send(payload))
                except RuntimeError:
                    # no running loop in this thread
                    print("[chat/tx] error: no running loop", flush=True)
                    return False

            print("[chat/tx] msg", flush=True)
            return True
        except Exception as e:
            print("[chat/tx] error:", e, flush=True)
            return False


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--peer", required=True)
    ap.add_argument("--pubkey", help="override my pubkey")
    ap.add_argument("--initiate", action="store_true", help="send hello immediately")
    args = ap.parse_args()

    me = args.pubkey or load_my_pub()
    chat = WSChat(args.ws, me, args.peer)
    await chat.connect()

    # spawn UI
    ui = spawn_chat_window("LiliumShare Chat (WS)", send_fn=chat.send_text)
    # The UI injects its line-adder into chat._adder (handled inside spawn_chat_window)

    def _late_attach():
        t = ui
        if hasattr(t, "add_incoming"):
            chat._add_incoming = t.add_incoming
        if hasattr(t, "add_outgoing"):
            chat._add_outgoing = t.add_outgoing
        if hasattr(t, "set_status"):
            chat._set_status = t.set_status

    asyncio.get_event_loop().call_later(0.3, _late_attach)

    if args.initiate:
        await asyncio.sleep(0.3)
        await chat.initiate()

    # keep process alive
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
