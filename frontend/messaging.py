# frontend/messaging.py
# Minimal, one-session end-to-end messaging utilities over WebRTC or WS relay.
# Contains: directional connkey helpers, AEAD helpers, a small DC-based
# MessagingSession (used by rtc_host/rtc_viewer), and a basic Tk chat window.

from __future__ import annotations

import base64
import hmac
import json
import os
import subprocess
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
try:
    from plyer import notification as _notify
except Exception:
    _notify = None
try:
    from signaling import Signaling as Signaling
except Exception:
    Signaling = None



# ------------------------- small helpers -------------------------

def http_base_from_ws(ws_url: str) -> str:
    u = urlparse(ws_url)
    scheme = "https" if u.scheme == "wss" else "http"
    return urlunparse((scheme, u.netloc, "", "", "", ""))

# AI made this work somehow. However, when I try to change anything it stops working so I suggest leaving it alone. 
def _try_connkey(http_base: str, host: str, friend: str):
    """
    Try a list of legacy/new endpoints (GET then POST) until one works.
    Returns the conn_key (base64) or raises the last exception.
    """
    import requests

    routes = [
        # new-style first
        ("GET",  "/api/friends/connkey", {"host": host, "friend": friend}, None),
        ("POST", "/api/friends/connkey", None, {"host": host, "friend": friend}),
        # common legacy shapes
        ("GET",  "/api/connkey",         {"host": host, "friend": friend}, None),
        ("POST", "/api/connkey",         None, {"host": host, "friend": friend}),
        # very old: “any direction”
        ("GET",  "/api/connkey-any",     {"a": host, "b": friend}, None),
        ("POST", "/api/connkey-any",     None, {"a": host, "b": friend}),
    ]

    last_err = None
    for method, path, params, body in routes:
        url = http_base.rstrip("/") + path
        try:
            if method == "GET":
                r = requests.get(url, params=params, timeout=8)
            else:
                r = requests.post(url, json=body, timeout=8)
            if r.status_code == 404:
                # fast-fail to next route
                continue
            r.raise_for_status()
            data = r.json()
            # normalize field names
            if "conn_key" in data:
                return data["conn_key"]
            if "connKey" in data:
                return data["connKey"]
            if "key" in data:
                return data["key"]
            # if body is e.g. {"ok":true,"conn_key":"..."}
            for k in ("conn_key", "connKey", "key"):
                if isinstance(data.get("data"), dict) and k in data["data"]:
                    return data["data"][k]
            # no known field, keep trying
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("No conn-key endpoint succeeded")

def _fetch_connkey(http_base: str, host: str, friend: str) -> str:
    """Fetch existing connkey. Raises HTTPError on non-200 (including 404)."""
    url = http_base.rstrip("/") + "/api/friends/connkey"
    r = requests.get(url, params={"host": host, "friend": friend}, timeout=8)
    if r.status_code == 200:
        data = r.json()
        # normalized field
        if "conn_key" in data:
            return data["conn_key"]
        # rarely, servers wrap data
        if isinstance(data.get("data"), dict) and "conn_key" in data["data"]:
            return data["data"]["conn_key"]
        raise RuntimeError("connkey response missing 'conn_key'")
    r.raise_for_status()  # propagate 404, 5xx, etc.


def _generate_connkey(http_base: str, host: str, friend: str) -> str:
    """Ask backend to create/refresh the (host,friend) connkey. Returns conn_key."""
    url = http_base.rstrip("/") + "/api/friends/connkey/generate"
    r = requests.post(url, json={"host": host, "friend": friend}, timeout=8)
    # When friendship isn’t accepted, backend returns 409
    if r.status_code == 409:
        raise RuntimeError("friendship not accepted (409) — accept / upsert friendship first")
    r.raise_for_status()
    data = r.json()
    if "conn_key" in data:
        return data["conn_key"]
    # some handlers might put it under data.conn_key
    if isinstance(data.get("data"), dict) and "conn_key" in data["data"]:
        return data["data"]["conn_key"]
    # last resort, allow OK without echoing key (we’ll re-fetch)
    return None


def _ensure_connkey(http_base: str, host: str, friend: str) -> str:
    """
    Fetch connkey; if missing, generate it (requires accepted friendship) and fetch again.
    """
    try:
        return _fetch_connkey(http_base, host, friend)
    except requests.HTTPError as e:
        # If not found -> create then fetch again
        if e.response is not None and e.response.status_code == 404:
            print("[msg/api] connkey missing → generating…", flush=True)
            _generate_connkey(http_base, host, friend)
            return _fetch_connkey(http_base, host, friend)
        raise
    except Exception:
        # fall back to legacy scan if anything else fails
        return _try_connkey(http_base, host, friend)


def get_connkey(http_base: str, host_pub: str, friend_pub: str) -> str:
    """
    Public entry: always ensure a (host,friend) key exists and return it.
    """
    print(f"[msg/api] connkey for host={host_pub[:10]}… friend={friend_pub[:10]}…", flush=True)
    ck = _ensure_connkey(http_base, host_pub, friend_pub)
    print("[msg/api] connkey: OK", flush=True)
    return ck


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

def system_notify(title: str, message: str):
    """
    Best-effort desktop notification:
      - try plyer (cross-platform)
      - then Linux 'notify-send'
      - otherwise no-op
    """
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=5)
        return
    except Exception:
        pass
    try:
        subprocess.Popen(["notify-send", title, message])
    except Exception:
        pass

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
            # after catching an exception when fetching connkey:
            try:
                # tell the chat window thread if present
                th = threading.current_thread()
                if hasattr(th, "set_status"):
                    th.set_status("Conn-key fetch failed (see console)")
            except Exception:
                pass

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
            # after catching an exception when fetching connkey:
            try:
                # tell the chat window thread if present
                th = threading.current_thread()
                if hasattr(th, "set_status"):
                    th.set_status("Conn-key fetch failed (see console)")
            except Exception:
                pass
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
            # after catching an exception when fetching connkey:
            try:
                # tell the chat window thread if present
                th = threading.current_thread()
                if hasattr(th, "set_status"):
                    th.set_status("Conn-key fetch failed (see console)")
            except Exception:
                pass
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
    on_close: Optional[Callable[None]] = None
):
    """
    DM-style chat window with selectable transcript and visible gray highlight.
    """
    import tkinter as tk
    from tkinter import ttk
    import threading
    import platform
    import queue

    # Colors & layout
    COLOR_BG        = "#F4F6F8"
    COLOR_INCOMING  = "#FFF5C2"   # light yellow
    COLOR_OUTGOING  = "#D6EBFF"   # light blue
    COLOR_SYSTEM    = "#EEEEEE"   # gray bubble
    COLOR_SEL_BG    = "#C7C7C7"   # <-- visible gray highlight
    COLOR_SEL_INACT = "#BEBEBE"
    ROW_PAD_Y       = 6

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
        ttk.Label(top, textvariable=status_var, anchor="w").pack(side="left")

        # ---- MIDDLE: transcript (Text + Scrollbar)
        mid = ttk.Frame(root)
        mid.pack(fill="both", expand=True, padx=8, pady=4)

        transcript = tk.Text(
            mid,
            wrap="word",
            bg=COLOR_BG,
            relief="flat",
            cursor="xterm",
            exportselection=True,              # keep PRIMARY selection here
            insertwidth=0,                     # hide caret
            selectbackground=COLOR_SEL_BG,     # gray when focused
            selectforeground="#000000",
            inactiveselectbackground=COLOR_SEL_INACT,  # gray when unfocused
            takefocus=True,
        )
        sb = ttk.Scrollbar(mid, orient="vertical", command=transcript.yview)
        transcript.configure(yscrollcommand=sb.set)
        transcript.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Fallback + ensure sel wins over bubble tags
        transcript.tag_configure("sel", background=COLOR_SEL_BG, foreground="#000000")

        # Bubble-ish tag styles
        pad_left  = 10
        pad_right = 10
        transcript.tag_configure(
            "incoming",
            background=COLOR_INCOMING,
            spacing1=ROW_PAD_Y, spacing3=ROW_PAD_Y,
            lmargin1=pad_left, lmargin2=pad_left,
            rmargin=120,
            justify="left",
        )
        transcript.tag_configure(
            "outgoing",
            background=COLOR_OUTGOING,
            spacing1=ROW_PAD_Y, spacing3=ROW_PAD_Y,
            lmargin1=120, lmargin2=120,
            rmargin=pad_right,
            justify="right",
        )
        transcript.tag_configure(
            "system",
            background=COLOR_SYSTEM,
            spacing1=ROW_PAD_Y, spacing3=ROW_PAD_Y,
            lmargin1=pad_left, lmargin2=pad_left,
            rmargin=pad_right,
            justify="center",
        )
        transcript.tag_configure("mono", font=("Courier", 10))

        # ⬅️ critical: make selection visible OVER bubbles
        transcript.tag_raise("sel")

        # Focus on click so active selection color shows
        transcript.bind("<Button-1>", lambda e: (transcript.focus_set(), None))

        # ---- Read-only behavior (block edits but allow selection/copy)
        def _block_edit(_e=None):
            return "break"

        for seq in (
            "<KeyPress>", "<BackSpace>", "<Delete>", "<Return>",
            "<<Paste>>", "<Control-v>", "<Shift-Insert>",
            "<Command-v>" if platform.system() == "Darwin" else None,
        ):
            if seq:
                transcript.bind(seq, _block_edit)

        # Copy / Select All
        def _do_copy(_e=None):
            try:
                sel = transcript.get("sel.first", "sel.last")
            except tk.TclError:
                sel = ""
            if sel:
                root.clipboard_clear()
                root.clipboard_append(sel)
            return "break"

        def _do_select_all(_e=None):
            transcript.tag_add("sel", "1.0", "end-1c")
            transcript.see("end")
            return "break"

        transcript.bind("<Control-c>", _do_copy)
        transcript.bind("<Control-a>", _do_select_all)
        if platform.system() == "Darwin":
            transcript.bind("<Command-c>", _do_copy)
            transcript.bind("<Command-a>", _do_select_all)

        # Context menu
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Copy", command=lambda: _do_copy())
        menu.add_command(label="Select All", command=lambda: _do_select_all())
        def _popup(e):
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
        transcript.bind("<Button-3>", _popup)  # Win/Linux
        transcript.bind("<Button-2>", _popup)  # Some X11 setups

        # ---- BOTTOM: entry + Send
        bottom = ttk.Frame(root)
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        entry = tk.Text(bottom, height=3, wrap="word", exportselection=False)  # ⬅︎ don't steal selection
        entry.grid(row=0, column=0, sticky="ew")
        send_btn = ttk.Button(bottom, text="Send")
        send_btn.grid(row=0, column=1, padx=(6, 0), sticky="e")
        bottom.columnconfigure(0, weight=1)
        entry.focus_set()

        # ---- Append helpers
        def _append(kind: str, text: str):
            tag = "incoming" if kind == "incoming" else "outgoing" if kind == "outgoing" else "system"
            transcript.insert("end", text.strip() + "\n", (tag,))
            transcript.see("end")

        # Thread-safe queue for UI updates
        q = queue.Queue()

        def _pump():
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "incoming":
                        _append("incoming", payload)
                    elif kind == "outgoing":
                        _append("outgoing", payload)
                    elif kind == "status":
                        status_var.set(payload)
                    elif kind == "system":
                        _append("system", payload)
            except queue.Empty:
                pass
            finally:
                root.after(50, _pump)

        def add_incoming(s: str):  q.put(("incoming", s))
        def add_outgoing(s: str):  q.put(("outgoing", s))
        def set_status(s: str):    q.put(("status", s))

        # Expose methods to networking thread
        def _attach_api():
            th = threading.current_thread()
            setattr(th, "add_incoming", add_incoming)
            setattr(th, "add_outgoing", add_outgoing)
            setattr(th, "set_status",   set_status)
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


# --- compatibility for older code expecting ChatWindow class ---
class ChatWindow:
    """
    Thin wrapper around spawn_chat_window, exposing:
      - post_incoming(text)
      - post_outgoing(text)
      - set_status(text)
      - _send_fn(text) -> bool   (set by caller; we call it when sending)
    """
    def __init__(self, title: str, send_fn, on_close=None):
        self._send_fn = send_fn
        self._th = spawn_chat_window(title, send_fn=self._send_text, on_close=on_close)

    def _send_text(self, text: str) -> bool:
        try:
            return bool(self._send_fn(text)) if self._send_fn else False
        except Exception:
            return False

    # names expected by rtc_viewer
    def post_incoming(self, s: str):
        if hasattr(self._th, "add_incoming"): self._th.add_incoming(s)

    def post_outgoing(self, s: str):
        if hasattr(self._th, "add_outgoing"): self._th.add_outgoing(s)

    def set_status(self, s: str):
        if hasattr(self._th, "set_status"): self._th.set_status(s)

try:
    from signaling import Signaling 
except Exception:
    pass
