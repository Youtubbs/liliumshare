# frontend/portal_capture.py
# Wayland screen capture via XDG Desktop Portal + PipeWire using GIO (no introspection, no dbus-next).

import time, asyncio, secrets
from typing import Optional, Tuple, Dict

import numpy as np

import gi
gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst

Gst.init(None)

class PortalError(Exception):
    pass

def _u(v):
    # Unpack GLib.Variant that may be nested a{sv}, (u a{sv}), etc.
    return v.unpack() if isinstance(v, GLib.Variant) else v

class _PortalClient:
    """
    Minimal client for org.freedesktop.portal.ScreenCast using GIO.
    We do not use introspection; we call methods with explicit signatures and
    wait for org.freedesktop.portal.Request::Response on the returned path.
    """
    DEST = "org.freedesktop.portal.Desktop"
    PATH = "/org/freedesktop/portal/desktop"
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self):
        self.conn: Gio.DBusConnection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, member: str, params: GLib.Variant, timeout_ms: int = 30000) -> GLib.Variant:
        # Synchronous DBus call (returns immediately for methods that yield a Request object)
        return self.conn.call_sync(
            self.DEST, self.PATH, self.IFACE, member,
            params, None, Gio.DBusCallFlags.NONE, timeout_ms, None
        )

    def _wait_request(self, req_path: str, timeout_ms: int = 30000) -> Tuple[int, Dict]:
        """
        Wait for Request::Response on req_path. Returns (code, results_dict).
        """
        result = {"done": False, "code": None, "dict": {}}
        def handler(conn, sender_name, object_path, interface_name, signal_name, parameters, user_data):
            try:
                code, amap = parameters.unpack()  # (u a{sv})
                result["code"] = code
                result["dict"] = {k: _u(v) for k, v in amap.items()}
            finally:
                result["done"] = True

        sub_id = self.conn.signal_subscribe(
            self.DEST,
            "org.freedesktop.portal.Request",
            "Response",
            req_path,
            None,
            Gio.DBusSignalFlags.NO_MATCH_RULE,
            handler,
            None,
        )
        try:
            end = time.monotonic() + (timeout_ms / 1000.0)
            ctx = GLib.MainContext.default()
            while not result["done"] and time.monotonic() < end:
                # Block until an event arrives or a small timeout passes.
                ctx.iteration(True)
            if not result["done"]:
                raise PortalError("portal request timed out")
            return int(result["code"]), result["dict"]
        finally:
            self.conn.signal_unsubscribe(sub_id)

    def create_session(self, token_base: str) -> str:
        opts = GLib.Variant('a{sv}', {
            "session_handle_token": GLib.Variant('s', token_base),
            "handle_token": GLib.Variant('s', token_base + "-create"),
        })
        reply = self._call("CreateSession", GLib.Variant('(a{sv})', (opts,)))
        req_path = reply.unpack()[0]
        code, out = self._wait_request(req_path)
        if code != 0:
            raise PortalError(f"CreateSession denied: rc={code}")
        session = out.get("session_handle")
        if not session:
            raise PortalError("CreateSession: no session_handle")
        return session

    def select_sources(self, session: str, token_base: str):
        # types=1 (monitor), cursor_mode=2 (embedded)
        opts = GLib.Variant('a{sv}', {
            "types": GLib.Variant('u', 1),
            "multiple": GLib.Variant('b', False),
            "cursor_mode": GLib.Variant('u', 2),
            "handle_token": GLib.Variant('s', token_base + "-select"),
        })
        reply = self._call("SelectSources", GLib.Variant('(oa{sv})', (session, opts)))
        req_path = reply.unpack()[0]
        self._wait_request(req_path)  # ignore out; success/failure is enough

    def start(self, session: str, token_base: str, parent_window: str = "") -> Dict:
        opts = GLib.Variant('a{sv}', {
            "handle_token": GLib.Variant('s', token_base + "-start"),
        })
        reply = self._call("Start", GLib.Variant('(soa{sv})', (session, parent_window, opts)))
        req_path = reply.unpack()[0]
        code, out = self._wait_request(req_path)
        if code != 0:
            raise PortalError(f"Start denied: rc={code}")
        return out

    def open_pipewire_remote(self, session: str) -> int:
        # We need the returned Unix FD; use the *with_unix_fd_list* variant.
        reply, out_fds = self.conn.call_with_unix_fd_list_sync(
            self.DEST, self.PATH, self.IFACE, "OpenPipeWireRemote",
            GLib.Variant('(oa{sv})', (session, GLib.Variant('a{sv}', {}))),
            None, Gio.DBusCallFlags.NONE, 30000, None
        )
        if out_fds.get_length() < 1:
            # Some implementations also put 'h' in the body. Try both:
            body = reply.unpack()
            if isinstance(body, tuple) and len(body) and isinstance(body[0], int):
                return int(body[0])
            raise PortalError("OpenPipeWireRemote: no FD returned")
        return out_fds.get(0)

class PipewireAppsink:
    """
    Pull frames from PipeWire using GStreamer appsink:
      pipewiresrc fd=<fd> ! videoconvert ! video/x-raw,format=BGR ! appsink
    """
    def __init__(self, pw_fd: int):
        self._latest = None
        desc = (
            f"pipewiresrc fd={pw_fd} ! "
            f"videoconvert ! video/x-raw,format=BGR ! "
            f"appsink name=sink emit-signals=true max-buffers=1 drop=true"
        )
        self.pipeline = Gst.parse_launch(desc)
        self.appsink = self.pipeline.get_by_name("sink")
        self.appsink.connect("new-sample", self._on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _on_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = s.get_value("width")
            h = s.get_value("height")
            buf = sample.get_buffer()
            ok, mapinfo = buf.map(1)  # READ
            if not ok:
                return 1
            try:
                arr = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape(h, w, 3)
                self._latest = arr.copy()
            finally:
                buf.unmap(mapinfo)
            return 0
        except Exception:
            return 1

    def get(self):
        return self._latest

    def close(self):
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

class PortalGrabber:
    """
    High-level capture helper:
      1) CreateSession
      2) SelectSources (monitor, embedded cursor)
      3) Start (shows the chooser dialog)
      4) OpenPipeWireRemote → pipewiresrc appsink
    """
    def __init__(self):
        self._client = _PortalClient()
        self._sink: Optional[PipewireAppsink] = None

    async def open(self):
        token = secrets.token_hex(6)
        session = self._client.create_session(token)
        self._client.select_sources(session, token)
        _ = self._client.start(session, token, parent_window="")
        fd = self._client.open_pipewire_remote(session)
        self._sink = PipewireAppsink(fd)

    def grab_bgr(self):
        return None if not self._sink else self._sink.get()

    def close(self):
        if self._sink:
            self._sink.close()
            self._sink = None

