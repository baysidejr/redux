import ctypes as C
import struct
import unittest

from redux.audio_output import AudioOutputError, WaveOutAudio, _WaveFormat, _WaveHeader


class FakeWinMM:
    def __init__(self):
        self.headers = []
        self.written = []
        self.unprepared = 0
        self.closed = False

    def waveOutOpen(self, handle, device, fmt, callback, instance, flags):
        C.cast(handle, C.POINTER(C.c_void_p))[0] = 123
        self.format = C.cast(fmt, C.POINTER(_WaveFormat)).contents
        self.rate, self.channels, self.bits = self.format.sample_rate, self.format.channels, self.format.bits
        return 0

    def waveOutPrepareHeader(self, handle, pointer, size): return 0

    def waveOutWrite(self, handle, pointer, size):
        header = C.cast(pointer, C.POINTER(_WaveHeader)).contents
        self.headers.append(header)
        self.written.append(C.string_at(header.data, header.length))
        return 0

    def waveOutUnprepareHeader(self, handle, pointer, size):
        self.unprepared += 1
        return 0

    def waveOutPause(self, handle): return 0
    def waveOutRestart(self, handle): return 0

    def waveOutReset(self, handle):
        for header in self.headers: header.flags |= 1
        return 0

    def waveOutClose(self, handle): self.closed = True; return 0


class WaveOutTests(unittest.TestCase):
    def test_format_buffer_lifetime_bounded_queue_and_cleanup(self):
        api = FakeWinMM()
        output = WaveOutAudio(48000, _api=api)
        self.assertEqual((api.rate, api.channels, api.bits), (48000, 2, 16))
        pcm = struct.pack("<hh", -32768, 32767) * 800
        for _ in range(12): output.write(pcm)
        self.assertEqual(len(api.written), 8)
        self.assertTrue(all(chunk == pcm for chunk in api.written))
        self.assertEqual(output.dropped_frames, 3200)
        output.pause(); output.resume(); output.close(); output.close()
        self.assertEqual(api.unprepared, 8)
        self.assertTrue(api.closed)

    def test_partial_frames_and_invalid_sample_rate(self):
        with self.assertRaises(AudioOutputError): WaveOutAudio(1, _api=FakeWinMM())
        output = WaveOutAudio(44100, _api=FakeWinMM())
        self.addCleanup(output.close)
        with self.assertRaisesRegex(AudioOutputError, "complete PCM16 stereo"):
            output.write(b"123")


if __name__ == "__main__":
    unittest.main()
