import ctypes as C
import threading
import unittest
from pathlib import Path
from unittest import mock

from redux import gl_video
from redux.gl_video import FILTER_MODES, layout
from redux.video_filters import DISPLAY_FILTERS, FILTERS


class LayoutTests(unittest.TestCase):
    def test_the_picture_fits_the_16_9_frame_at_the_display_aspect_and_centres(self):
        self.assertEqual(layout(320, 240, 4 / 3, {}, 1920, 1080), (240, 0, 1440, 1080))
        self.assertEqual(layout(256, 224, 4 / 3, {}, 3840, 2160), (480, 0, 2880, 2160))
        # A 16:10 window keeps the 16:9 frame's size and centres the picture in the whole window.
        self.assertEqual(layout(320, 240, 4 / 3, {}, 1920, 1200), (240, 60, 1440, 1080))

    def test_shape_zoom_and_offsets_follow_the_canvas_renderer(self):
        self.assertEqual(layout(320, 240, 4 / 3, {"shape": "stretch"}, 1280, 720), (0, 0, 1280, 720))
        self.assertEqual(layout(256, 224, 4 / 3, {"shape": "pixel"}, 1280, 720)[2:], (823, 720))
        x, y, w, h = layout(320, 240, 4 / 3, {"zoom": 50, "offset_x": 100, "offset_y": -20}, 2560, 1440)
        self.assertEqual((w, h), (960, 720))
        self.assertEqual((x, y), ((2560 - 960) // 2 + 200, (1440 - 720) // 2 - 40))

    def test_a_rotated_core_shows_portrait(self):
        x, y, w, h = layout(320, 240, 4 / 3, {"rotation": 1}, 1280, 720)
        self.assertLess(w, h)
        self.assertAlmostEqual(w / h, 3 / 4, places=2)

    def test_every_display_filter_has_a_shader_mode(self):
        self.assertTrue(set(DISPLAY_FILTERS) <= set(FILTER_MODES))
        self.assertTrue(set(FILTERS) <= set(FILTER_MODES))


@unittest.skipUnless(gl_video.probe()[0], "no OpenGL 3.3 on this machine")
class OpenGLTests(unittest.TestCase):
    def test_framebuffer_readback_is_top_row_first_and_uploads_take_every_software_format(self):
        video = gl_video.GLVideo().create()
        try:
            gl = video.gl
            video.ensure_framebuffer(4, 2, True, True)
            gl.glBindFramebuffer(gl_video.GL_FRAMEBUFFER, video.fbo)
            gl.glViewport(0, 0, 4, 2)
            gl.glEnable(gl_video.GL_SCISSOR_TEST)
            gl.glScissor = C.CFUNCTYPE(None, C.c_int, C.c_int, C.c_int, C.c_int)(video.proc_address("glScissor"))
            gl.glScissor(0, 1, 4, 1)  # GL row 1 is the top row of a bottom-left-origin image
            gl.glClearColor(1, 0, 0, 1); gl.glClear(gl_video.GL_COLOR_BUFFER_BIT)
            gl.glScissor(0, 0, 4, 1)
            gl.glClearColor(0, 0, 1, 1); gl.glClear(gl_video.GL_COLOR_BUFFER_BIT)
            gl.glDisable(gl_video.GL_SCISSOR_TEST)
            rgb = video.read_framebuffer(4, 2, bottom_left=True)
            self.assertEqual(rgb[:3], b"\xff\x00\x00", "the top row comes first")
            self.assertEqual(rgb[-3:], b"\x00\x00\xff")
            for pixel_format, pixel in ((0, b"\x00\x7c"), (1, b"\x00\x00\xff\x00"), (2, b"\x00\xf8")):
                data = C.create_string_buffer(pixel * 6)
                video.upload(C.addressof(data), 3, 2, 3 * len(pixel), pixel_format)
                self.assertEqual(gl.glGetError(), 0, pixel_format)
                video.present("texture", 3, 2, {"filter": "crt"}, 4 / 3, hardware=False)
                self.assertEqual(gl.glGetError(), 0)
        finally:
            video.destroy()


if __name__ == "__main__":
    unittest.main()
