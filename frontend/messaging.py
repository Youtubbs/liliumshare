# frontend/messaging.py
# Minimal, one-session end-to-end messaging utilities over WebRTC or WS relay.
# Contains: directional connkey helpers, AEAD helpers, a small DC-based
# MessagingSession (used by rtc_host/rtc_viewer), and a basic Tk chat window.

from __future__ import annotations

import base64
import hmac
import json
import os
import threading
from hashlib import sha256
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from nacl.bindings import (
    crypto_aead_xchacha20poly1305_ietf_decrypt,
    crypto_aead_xchacha20poly1305_ietf_encrypt,
)
from nacl.public import PrivateKey, PublicKey
from nacl.utils import random as nacl_random


# ------------------------- small helpers -------------------------

def http_base_from_ws(ws_url: str) -> str:
    u = urlparse(ws_url)
    scheme = "https" if u.scheme == "wss" else "http"
    return urlunparse((scheme, u.netloc, "", "", "", ""))


def get_connkey(http_base: str, host_pub: str, friend_pub: str) -> str:
    """Fetch conn_key for a specific (host, friend) direction."""
    url = f"{http_base}/api/friends/connkey"
    params = {"host": host_pub, "friend": friend_pub}
    print(f"[msg/api] GET {url} params={params}", flush=True)
    r = requests.get(url, params=params, timeout=8)
    print(f"[msg/api] -> {r.status_code}", flush=True)
    r.raise_for_status()
    d = r.json()
    print(f"[msg/api] body: {d}", flush=True)
    return d["conn_key"]  # base64 string


def get_connkey_any(http_base: str, a: str, b: str) -> Tuple[str, bool]:
    """Legacy helper: try (a,b) then (b,a); returns (connkey, flipped)."""
    print(f"[msg/connkey-any] trying host={a[:10]}… friend={b[:10]}…", flush=True)
    try:
        ck = get_connkey(http_base, a, b)
        return ck, False
    except Exception as e1:
        print("[msg/connkey-any] first direction failed:", e1, flush=True)
        print(f"[msg/connkey-any] trying host={b[:10]}… friend={a[:10]}…", flush=True)
        ck = get_connkey(http_base, b, a)
        return ck, True


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, out_len: int = 32) -> bytes:
    import hashlib
    if salt is None:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    t = b""
    okm = b""
    counter = 1
    while len(okm) < out_len:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:out_len]


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


# ------------------------- datachannel session -------------------------

class MessagingSession:
    """
    DC-based secure message session used by rtc_host/rtc_viewer.

    Protocol (directional connkey):
      1) Host calls start_handshake_as_host() → sends hello {role='host', epub, auth}
      2) Viewer receives, verifies HMAC using get_connkey(host=host_pub, friend=viewer_pub),
         derives key, replies hello-ack {role='viewer', epub, auth}
      3) Host verifies ack with same direction, derives key → messages may flow

    This matches the explicit role logic used in chat_only.py now.
    """
    def __init__(
        self,
        *,
        role: str,                 # "host" or "viewer"
        me_pubkey: str,
        other_pubkey: str,
        ws_url: str,
        send_raw: Callable[[str], None],
        on_plaintext: Callable[[str], None]
    ):
        self.role = role
        self.me_pub = me_pubkey
        self.other_pub = other_pubkey
        self.ws_url = ws_url
        self.send_raw = send_raw
        self.on_plaintext = on_plaintext

        self._sk = PrivateKey.generate()
        self._pk = self._sk.public_key

        self._aead_key: Optional[bytes] = None
        self._sealed = False

        print(f"[msg/session] role={self.role} me={self.me_pub[:10]}… other={self.other_pub[:10]}… eph={b64e(bytes(self._pk))[:24]}…", flush=True)

    def close(self):
        self._aead_key = None
        self._sealed = True
        print("[msg/session] closed", flush=True)

    def _auth_tag(self, conn_key_b64: str, role: str, epub_b64: str) -> str:
        key = b64d(conn_key_b64)
        msg = (role + "|" + epub_b64).encode("utf-8")
        return b64e(hmac.new(key, msg, sha256).digest())

    def _derive_session_key(self, peer_epub_b64: str, conn_key_b64: str):
        try:
            from nacl.bindings import crypto_scalarmult
            peer_pub = PublicKey(b64d(peer_epub_b64))
            shared = crypto_scalarmult(bytes(self._sk), bytes(peer_pub))

            # canonical salt so both sides get the same key
            a, b = self.me_pub, self.other_pub
            canon = f"{a}|{b}" if a <= b else f"{b}|{a}"
            salt = hmac.new(b64d(conn_key_b64), canon.encode("utf-8"), sha256).digest()

            info = b"LiliumShare/secure-msg/v1"
            self._aead_key = hkdf_sha256(shared, salt, info, out_len=32)
            print("[msg/kdf] OK — DC session ready", flush=True)
        except Exception as e:
            print("[msg/kdf] error:", e, flush=True)
            self._aead_key = None


    # ---- handshake (host starts) ----

    def start_handshake_as_host(self):
        """Call on the DC when opened on the host side."""
        http_base = http_base_from_ws(self.ws_url)
        # Host must fetch (host=me, friend=other)
        conn_key = get_connkey(http_base, self.me_pub, self.other_pub)
        hello = {
            "t": "hello",
            "role": "host",
            "epub": b64e(bytes(self._pk)),
            "auth": self._auth_tag(conn_key, "host", b64e(bytes(self._pk))),
        }
        try:
            self.send_raw(json.dumps(hello))
            print("[msg/tx] hello (host)", flush=True)
        except Exception as e:
            print("[msg/tx] hello send error:", e, flush=True)

    def _handle_hello(self, msg: dict):
        role = msg.get("role")
        epub = msg.get("epub")
        auth = msg.get("auth")
        if role not in ("host", "viewer") or not epub or not auth:
            print("[msg/rx] hello invalid fields", flush=True)
            return

        http_base = http_base_from_ws(self.ws_url)
        # Direction dictated by sender role.
        host = self.other_pub if role == "host" else self.me_pub
        friend = self.me_pub if role == "host" else self.other_pub
        try:
            conn_key = get_connkey(http_base, host, friend)
        except Exception as e:
            print("[msg/connkey] error (hello):", e, flush=True)
            return

        expect = hmac.new(b64d(conn_key), (role + "|" + epub).encode("utf-8"), sha256).digest()
        if not hmac.compare_digest(b64d(auth), expect):
            print("[msg/auth] hello FAIL", flush=True)
            return
        print("[msg/auth] hello OK", flush=True)

        self._derive_session_key(epub, conn_key)

        # Viewer replies with ack
        if self.role == "viewer" and role == "host":
            ack = {
                "t": "hello-ack",
                "role": "viewer",
                "epub": b64e(bytes(self._pk)),
                "auth": self._auth_tag(conn_key, "viewer", b64e(bytes(self._pk))),
            }
            try:
                self.send_raw(json.dumps(ack))
                print("[msg/tx] hello-ack (viewer)", flush=True)
            except Exception as e:
                print("[msg/tx] ack send error:", e, flush=True)

    def _handle_hello_ack(self, msg: dict):
        role = msg.get("role")
        epub = msg.get("epub")
        auth = msg.get("auth")
        if role != "viewer" or self.role != "host" or not epub or not auth:
            print("[msg/rx] ack invalid / unexpected", flush=True)
            return

        http_base = http_base_from_ws(self.ws_url)
        try:
            # Same direction (host=me, friend=other)
            conn_key = get_connkey(http_base, self.me_pub, self.other_pub)
        except Exception as e:
            print("[msg/connkey] error (ack):", e, flush=True)
            return

        expect = hmac.new(b64d(conn_key), (role + "|" + epub).encode("utf-8"), sha256).digest()
        if not hmac.compare_digest(b64d(auth), expect):
            print("[msg/auth] ack FAIL", flush=True)
            return
        print("[msg/auth] ack OK", flush=True)

        self._derive_session_key(epub, conn_key)

    def on_json(self, raw_json: str):
        try:
            msg = json.loads(raw_json)
        except Exception:
            return

        t = msg.get("t")
        if t == "hello":
            self._handle_hello(msg)
            return
        if t == "hello-ack":
            self._handle_hello_ack(msg)
            return

        if t == "msg":
            if not self._aead_key:
                return
            try:
                nonce = base64.b64decode(msg["n"])
                ct = base64.b64decode(msg["c"])
                sender = msg.get("from", "")
                receiver = msg.get("to", "")
                ad = f"{sender}|{receiver}".encode("utf-8")
                pt = crypto_aead_xchacha20poly1305_ietf_decrypt(ct, ad, nonce, self._aead_key)
                text = pt.decode("utf-8", "replace")
                self.on_plaintext(text)
            except Exception as e:
                print("[msg/rx] decrypt error:", e, flush=True)


    def send_text(self, text: str):
        if not self._aead_key:
            print("[msg/tx] refused (key not ready)", flush=True)
            return False
        try:
            nonce = nacl_random(24)
            ad = f"{self.me_pub}|{self.other_pub}".encode("utf-8")
            ct = crypto_aead_xchacha20poly1305_ietf_encrypt(text.encode("utf-8"), ad, nonce, self._aead_key)
            out = {"t": "msg", "from": self.me_pub, "to": self.other_pub, "n": b64e(nonce), "c": b64e(ct)}
            self.send_raw(json.dumps(out))
            print("[msg/tx] msg", flush=True)
            return True
        except Exception as e:
            print("[msg/tx] error:", e, flush=True)
            return False



# ------------------------- chat UI -------------------------

def spawn_chat_window(
    title: str,
    send_fn: Callable[[str], bool],
    on_close: Optional[Callable[[], None]] = None
):
    """
    DM-style chat window:
      - Status label at top-left
      - Scrollable message area with colored bubbles:
          * incoming (left)  = light yellow
          * outgoing (right) = light blue
      - Entry box at bottom with Send button
      - Enter to send; Shift+Enter for newline
    Exposes thread attributes for networking layer:
      - thread.add_incoming(text)
      - thread.add_outgoing(text)
      - thread.set_status(text)
    """
    import tkinter as tk
    from tkinter import ttk
    import queue
    import threading

    # Colors & layout
    COLOR_BG        = "#F4F6F8"
    COLOR_INCOMING  = "#FFF5C2"  # light yellow
    COLOR_OUTGOING  = "#D6EBFF"  # light blue
    COLOR_SYSTEM    = "#EEEEEE"
    BUBBLE_WRAP     = 420        # px wrap width for text bubbles
    ROW_PAD_Y       = 6
    COL_PAD_X       = 8

    def _thread():
        root = tk.Tk()
        root.title(title)
        root.geometry("560x520")
        root.minsize(420, 360)
        root.configure(bg=COLOR_BG)

        # ---- TOP: status
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=(8, 4))
        status_var = tk.StringVar(value="Connecting…")
        status_lbl = ttk.Label(top, textvariable=status_var, anchor="w")
        status_lbl.pack(side="left")

        # ---- MIDDLE: scrollable message area (Canvas + interior Frame)
        mid = tk.Frame(root, bg=COLOR_BG)
        mid.pack(fill="both", expand=True, padx=8, pady=4)

        canvas = tk.Canvas(mid, highlightthickness=0, bg=COLOR_BG)
        vbar = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)

        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # The inner frame that holds bubbles
        inner = tk.Frame(canvas, bg=COLOR_BG)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_config_inner(_e=None):
            # Update scrollregion to match inner frame size
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Make inner frame width follow canvas width
            canvas_width = canvas.winfo_width()
            canvas.itemconfigure(inner_id, width=canvas_width)

        def _on_config_canvas(_e=None):
            _on_config_inner()

        inner.bind("<Configure>", _on_config_inner)
        canvas.bind("<Configure>", _on_config_canvas)

        # Mouse-wheel scrolling (Linux/Win/macOS)
        def _on_mousewheel(event):
            # Linux uses Button-4/5; Windows/macOS use <MouseWheel>
            if event.num == 4:
                canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                canvas.yview_scroll(+3, "units")
            elif event.delta:
                # On Windows delta is +/-120; on macOS apparently is +/-1 or +/-120
                step = -1 if event.delta > 0 else +1
                canvas.yview_scroll(step * 3, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)   # Win/macOS
        canvas.bind_all("<Button-4>", _on_mousewheel)     # Linux up
        canvas.bind_all("<Button-5>", _on_mousewheel)     # Linux down

        # ---- BOTTOM: entry + Send
        bottom = ttk.Frame(root)
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        entry = tk.Text(bottom, height=3, wrap="word")
        entry.grid(row=0, column=0, sticky="ew")
        send_btn = ttk.Button(bottom, text="Send")
        send_btn.grid(row=0, column=1, padx=(6, 0), sticky="e")
        bottom.columnconfigure(0, weight=1)

        entry.focus_set()

        # ---- Utilities for making bubbles
        def _add_bubble(text: str, side: str):
            """
            side: 'left' (incoming) or 'right' (outgoing) or 'system'
            """
            row = tk.Frame(inner, bg=COLOR_BG)
            row.pack(fill="x", pady=(ROW_PAD_Y, ROW_PAD_Y))

            if side == "left":
                container = tk.Frame(row, bg=COLOR_INCOMING, bd=0, highlightthickness=0)
                anchor_side = "w"
                padx = (0, COL_PAD_X)
            elif side == "right":
                container = tk.Frame(row, bg=COLOR_OUTGOING, bd=0, highlightthickness=0)
                anchor_side = "e"
                padx = (COL_PAD_X, 0)
            else:
                container = tk.Frame(row, bg=COLOR_SYSTEM, bd=0, highlightthickness=0)
                anchor_side = "center"
                padx = (COL_PAD_X, COL_PAD_X)

            # The bubble label
            lbl = tk.Label(
                container,
                text=text,
                wraplength=BUBBLE_WRAP,
                justify="left",
                bg=container["bg"],
                padx=10,
                pady=6,
            )
            lbl.pack()

            # Place container on left/right/center
            if anchor_side == "w":
                container.pack(side="left", anchor="w", padx=padx)
            elif anchor_side == "e":
                container.pack(side="right", anchor="e", padx=padx)
            else:
                container.pack(anchor="center", padx=padx)

            # Autoscroll to bottom
            root.after(10, lambda: canvas.yview_moveto(1.0))

        # Thread-safe queue for UI updates
        q = queue.Queue()

        def _pump():
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "incoming":
                        _add_bubble(payload, "left")
                    elif kind == "outgoing":
                        _add_bubble(payload, "right")
                    elif kind == "status":
                        status_var.set(payload)
                    elif kind == "system":
                        _add_bubble(payload, "system")
            except queue.Empty:
                pass
            finally:
                root.after(50, _pump)

        def add_incoming(s: str):
            q.put(("incoming", s))

        def add_outgoing(s: str):
            q.put(("outgoing", s))

        def set_status(s: str):
            q.put(("status", s))

        # Expose methods to networking thread
        def _attach_api():
            th = threading.current_thread()
            setattr(th, "add_incoming", add_incoming)
            setattr(th, "add_outgoing", add_outgoing)
            setattr(th, "set_status", set_status)
        root.after(0, _attach_api)

        # ---- Sending logic
        def _do_send():
            msg = entry.get("1.0", "end").strip()
            if not msg:
                return
            ok = send_fn(msg)
            if ok:
                add_outgoing(msg)
            else:
                q.put(("system", "(not ready)"))
            entry.delete("1.0", "end")

        def _on_return(ev):
            # Enter sends, Shift+Enter inserts newline
            if ev.state & 0x0001:  # Shift modifier
                return
            _do_send()
            return "break"

        entry.bind("<Return>", _on_return)
        send_btn.configure(command=_do_send)

        def _close():
            try:
                if on_close:
                    on_close()
            finally:
                root.destroy()

        root.protocol("WM_DELETE_WINDOW", _close)

        # Start pump
        root.after(50, _pump)
        root.mainloop()

    th = threading.Thread(target=_thread, daemon=True)
    th.start()
    return th

try:
    from signaling import Signaling 
except Exception:
    pass
