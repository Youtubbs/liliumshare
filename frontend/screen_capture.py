# Portal-first capture for Wayland (GNOME). Falls back to synthetic pattern.

import asyncio, time, os
import numpy as np
from av import VideoFrame
from aiortc import MediaStreamTrack

from portal_capture import PortalGrabber, PortalError

class ScreenTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, fps=20):
        super().__init__()
        self.fps = fps
        self.dt = 1.0 / float(fps)
        self._t0 = time.time()
        self._mode = "synthetic"
        self._portal: PortalGrabber | None = None

    async def start_capture(self):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            try:
                self._portal = PortalGrabber()
                await self._portal.open()
                self._mode = "portal"
                print("[host/capture] using xdg-desktop-portal/pipewire (GIO)")
                return
            except Exception as e:
                print("[host/capture] portal failed, falling back to synthetic:", e)
                self._portal = None
        self._mode = "synthetic"
        print("[host/capture] using synthetic frames")

    def _synthetic(self, w=1280, h=720):
        t = time.time() - self._t0
        x = np.linspace(0, 1, w, dtype=np.float32)
        y = np.linspace(0, 1, h, dtype=np.float32)
        xv, yv = np.meshgrid(x, y)
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[..., 0] = ((xv * 255 + 60 * np.sin(t)) % 255).astype(np.uint8)
        img[..., 1] = ((yv * 255 + 60 * np.cos(t)) % 255).astype(np.uint8)
        img[..., 2] = ((xv * 127 + yv * 127 + 60 * np.sin(0.5 * t)) % 255).astype(np.uint8)
        img[0:20, :, :] = 0
        return img

    async def recv(self):
        await asyncio.sleep(self.dt)
        if self._mode == "portal" and self._portal is not None:
            try:
                bgr = self._portal.grab_bgr()
                if bgr is not None:
                    frame = VideoFrame.from_ndarray(bgr, format="bgr24")
                    frame.pts, frame.time_base = self.next_timestamp()
                    return frame
            except Exception as e:
                print("[host/capture] portal grab error; switching to synthetic:", e)
                self._mode = "synthetic"
        bgr = self._synthetic()
        frame = VideoFrame.from_ndarray(bgr, format="bgr24")
        frame.pts, frame.time_base = self.next_timestamp()
        return frame

