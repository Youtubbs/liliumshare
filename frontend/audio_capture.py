import asyncio, numpy as np, sounddevice as sd
from aiortc import MediaStreamTrack
from av.audio.frame import AudioFrame

# Note: System audio loopback is OS-specific.
# This track defaults to microphone input for portability.
# You can configure device index via environment variable LILIUM_AUDIO_DEVICE.

class MicrophoneTrack(MediaStreamTrack):
    kind = "audio"
    def __init__(self, samplerate=48000, channels=1, frames_per_buffer=960):
        super().__init__()
        self.samplerate = samplerate
        self.channels = channels
        self.frames_per_buffer = frames_per_buffer
        self.queue = asyncio.Queue(maxsize=8)
        self.stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            blocksize=frames_per_buffer,
            callback=self._callback
        )
        self.stream.start()

    def _callback(self, indata, frames, time, status):
        try:
            self.queue.put_nowait(indata.copy())
        except asyncio.QueueFull:
            pass

    async def recv(self):
        pcm = await self.queue.get()
        frame = AudioFrame(format="s16", layout="mono", samples=len(pcm))
        frame.planes[0].update(pcm.tobytes())
        frame.pts, frame.time_base = self.next_timestamp()
        return frame
