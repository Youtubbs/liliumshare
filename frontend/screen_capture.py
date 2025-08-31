import asyncio, time
import numpy as np
from av import VideoFrame
from aiortc import MediaStreamTrack
import mss

class ScreenTrack(MediaStreamTrack):
    kind = "video"
    def __init__(self, monitor_index=1, fps=20):
        super().__init__()
        self.fps = fps
        self.interval = 1.0 / fps
        self.sct = mss.mss()
        mons = self.sct.monitors
        self.monitor = mons[min(monitor_index, len(mons)-1)]

    async def recv(self):
        await asyncio.sleep(self.interval)
        img = np.array(self.sct.grab(self.monitor))
        frame = VideoFrame.from_ndarray(img, format="bgra")
        frame.pts, frame.time_base = self.next_timestamp()
        return frame
