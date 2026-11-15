"""OpenGL video for the libretro host: hardware-rendered cores and GPU presentation.

One ``GLVideo`` lives on the emulation worker thread. It owns a WGL context on a window (the
game surface Redux shows over its canvas, or a private hidden window when there is none), an
optional framebuffer a hardware-rendered core draws into, a texture software frames are
uploaded to, and a small shader that scales the frame into the window with the system's
display settings (filter, scanlines, shape, zoom, offsets, brightness).

Nothing here touches Tk. The UI thread passes presentation settings as a plain dictionary.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import threading

# ---------------------------------------------------------------- constants
GL_TEXTURE_2D = 0x0DE1
GL_RGBA = 0x1908
GL_RGB = 0x1907
GL_BGRA = 0x80E1
GL_RGBA8 = 0x8058
GL_UNSIGNED_BYTE = 0x1401
GL_UNSIGNED_SHORT_5_6_5 = 0x8363
GL_UNSIGNED_SHORT_1_5_5_5_REV = 0x8366
GL_UNSIGNED_INT_8_8_8_8_REV = 0x8367
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_LINEAR = 0x2601
GL_CLAMP_TO_EDGE = 0x812F
GL_UNPACK_ROW_LENGTH = 0x0CF2
GL_UNPACK_ALIGNMENT = 0x0CF5
GL_PACK_ALIGNMENT = 0x0D05
GL_PACK_ROW_LENGTH = 0x0D02
GL_FRAMEBUFFER = 0x8D40
GL_READ_FRAMEBUFFER = 0x8CA8
GL_DRAW_FRAMEBUFFER = 0x8CA9
GL_RENDERBUFFER = 0x8D41
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_DEPTH_ATTACHMENT = 0x8D00
GL_DEPTH_STENCIL_ATTACHMENT = 0x821A
GL_DEPTH24_STENCIL8 = 0x88F0
GL_DEPTH_COMPONENT24 = 0x81A6
GL_FRAMEBUFFER_COMPLETE = 0x8CD5
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_STENCIL_BUFFER_BIT = 0x0400
GL_SCISSOR_TEST = 0x0C11
GL_DEPTH_TEST = 0x0B71
GL_STENCIL_TEST = 0x0B90
GL_BLEND = 0x0BE2
GL_CULL_FACE = 0x0B44
GL_FRAMEBUFFER_SRGB = 0x8DB9
GL_DITHER = 0x0BD0
GL_TRIANGLE_STRIP = 0x0005
GL_VERTEX_SHADER = 0x8B31
GL_FRAGMENT_SHADER = 0x8B30
GL_COMPILE_STATUS = 0x8B81
GL_LINK_STATUS = 0x8B82
GL_TEXTURE0 = 0x84C0
GL_PIXEL_PACK_BUFFER = 0x88EB
GL_PIXEL_UNPACK_BUFFER = 0x88EC
GL_BACK = 0x0405
GL_VERSION = 0x1F02
GL_RENDERER = 0x1F01
GL_VENDOR = 0x1F00
GL_MAJOR_VERSION = 0x821B
GL_MINOR_VERSION = 0x821C

WGL_CONTEXT_MAJOR_VERSION_ARB = 0x2091
WGL_CONTEXT_MINOR_VERSION_ARB = 0x2092
WGL_CONTEXT_FLAGS_ARB = 0x2094
WGL_CONTEXT_PROFILE_MASK_ARB = 0x9126
WGL_CONTEXT_CORE_PROFILE_BIT_ARB = 0x1
WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB = 0x2
WGL_CONTEXT_DEBUG_BIT_ARB = 0x1

# libretro hardware context types
HW_CONTEXT_OPENGL = 1
HW_CONTEXT_OPENGLES2 = 2
HW_CONTEXT_OPENGL_CORE = 3
HW_CONTEXT_OPENGLES3 = 4

# The filter ids Redux stores (video_filters.DISPLAY_FILTERS plus the legacy per-game ids).
FILTER_MODES = {"clean": 0, "sharp": 0, "smooth": 1, "scanlines": 2, "crt": 3, "composite": 4}

_VERTEX = b"""#version 330 core
uniform vec2 uv_scale;
uniform int flip;
uniform int rotation;
out vec2 uv;
void main() {
    vec2 p = vec2((gl_VertexID & 1) != 0 ? 1.0 : 0.0, (gl_VertexID & 2) != 0 ? 1.0 : 0.0);
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    vec2 s = p;
    if (rotation == 1) s = vec2(p.y, 1.0 - p.x);
    else if (rotation == 2) s = vec2(1.0 - p.x, 1.0 - p.y);
    else if (rotation == 3) s = vec2(1.0 - p.y, p.x);
    if (flip == 1) s.y = 1.0 - s.y;
    uv = s * uv_scale;
}
"""

_FRAGMENT = b"""#version 330 core
in vec2 uv;
out vec4 color;
uniform sampler2D tex;
uniform vec2 tex_size;
uniform vec2 out_size;
uniform vec2 origin;
uniform vec2 prescale;
uniform int mode;
uniform float scan;
uniform float lines;
uniform float bright;

vec3 sharp(vec2 coord) {
    // Sharp bilinear: whole-number nearest-neighbour enlargement, then a one-pixel blend at the
    // seams, so pixels stay square and even at any window size.
    vec2 texel = coord * tex_size;
    vec2 base = floor(texel);
    vec2 inner = fract(texel) - 0.5;
    vec2 range = 0.5 - 0.5 / prescale;
    vec2 f = (inner - clamp(inner, -range, range)) * prescale + 0.5;
    return texture(tex, (base + f) / tex_size).rgb;
}

void main() {
    vec3 c;
    if (mode == 1 || mode == 3) {
        c = texture(tex, uv).rgb;
    } else if (mode == 4) {
        vec2 dx = vec2(1.0 / tex_size.x, 0.0);
        vec3 soft = (texture(tex, uv - dx).rgb + 2.0 * texture(tex, uv).rgb + texture(tex, uv + dx).rgb) * 0.25;
        c = mix(sharp(uv), soft, 0.65);
    } else {
        c = sharp(uv);
    }
    if ((mode == 2 || mode == 3) && scan > 0.0) {
        float y = gl_FragCoord.y - origin.y;
        if (out_size.y >= lines * 2.0) {
            if (fract(y / out_size.y * lines) < 0.3) c *= 1.0 - scan;
        } else if (mod(floor(y), 2.0) == 0.0) {
            c *= 1.0 - scan;
        }
    }
    if (mode == 3) {
        vec3 grille = vec3(225.0 / 255.0);
        int column = int(mod(floor(gl_FragCoord.x), 3.0));
        if (column == 0) grille.r = 1.0; else if (column == 1) grille.g = 1.0; else grille.b = 1.0;
        c *= grille;
    }
    color = vec4(clamp(c * bright, 0.0, 1.0), 1.0);
}
"""


_HGLRC = C.c_void_p


class GLError(RuntimeError):
    pass


class _PFD(C.Structure):
    _fields_ = [("nSize", W.WORD), ("nVersion", W.WORD), ("dwFlags", W.DWORD), ("iPixelType", C.c_ubyte),
                ("cColorBits", C.c_ubyte), ("cRedBits", C.c_ubyte), ("cRedShift", C.c_ubyte),
                ("cGreenBits", C.c_ubyte), ("cGreenShift", C.c_ubyte), ("cBlueBits", C.c_ubyte),
                ("cBlueShift", C.c_ubyte), ("cAlphaBits", C.c_ubyte), ("cAlphaShift", C.c_ubyte),
                ("cAccumBits", C.c_ubyte), ("cAccumRedBits", C.c_ubyte), ("cAccumGreenBits", C.c_ubyte),
                ("cAccumBlueBits", C.c_ubyte), ("cAccumAlphaBits", C.c_ubyte), ("cDepthBits", C.c_ubyte),
                ("cStencilBits", C.c_ubyte), ("cAuxBuffers", C.c_ubyte), ("iLayerType", C.c_ubyte),
                ("bReserved", C.c_ubyte), ("dwLayerMask", W.DWORD), ("dwVisibleMask", W.DWORD),
                ("dwDamageMask", W.DWORD)]


def layout(frame_width, frame_height, display_aspect, presentation, window_width, window_height):
    """The game picture's rectangle (x, y, width, height) in window pixels, top-left origin.

    Mirrors the canvas renderer: the picture fits a 16:9 area of the window (the canvas's
    1280x720 logical frame), keeps the display aspect for the chosen shape, then zoom and the
    offsets (in logical units) apply."""
    scale = min(window_width / 1280, window_height / 720) if window_width and window_height else 1
    bounds_width, bounds_height = max(1, round(1280 * scale)), max(1, round(720 * scale))
    shape = presentation.get("shape", "auto")
    rotated = presentation.get("rotation", 0) in (1, 3)
    width, height = (frame_height, frame_width) if rotated else (frame_width, frame_height)
    aspect = (width / height if shape == "pixel" else 4 / 3 if shape == "4:3"
              else bounds_width / bounds_height if shape == "stretch"
              else (1 / display_aspect if rotated else display_aspect) if display_aspect else 4 / 3)
    if bounds_width >= bounds_height * aspect:
        fit_width, fit_height = max(1, round(bounds_height * aspect)), bounds_height
    else:
        fit_width, fit_height = bounds_width, max(1, round(bounds_width / aspect))
    zoom = presentation.get("zoom", 100) / 100
    fit_width, fit_height = max(1, round(fit_width * zoom)), max(1, round(fit_height * zoom))
    x = (window_width - fit_width) // 2 + round(presentation.get("offset_x", 0) * scale)
    y = (window_height - fit_height) // 2 + round(presentation.get("offset_y", 0) * scale)
    return x, y, fit_width, fit_height


class _Native:
    """The Win32/WGL entry points, loaded once."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        if os.name != "nt":
            raise GLError("OpenGL video needs Windows.")
        self.user32 = C.WinDLL("user32", use_last_error=True)
        self.gdi32 = C.WinDLL("gdi32", use_last_error=True)
        self.opengl32 = C.WinDLL("opengl32", use_last_error=True)
        self.kernel32 = C.WinDLL("kernel32", use_last_error=True)
        u, g, o = self.user32, self.gdi32, self.opengl32
        u.GetDC.argtypes = [W.HWND]; u.GetDC.restype = W.HDC
        u.ReleaseDC.argtypes = [W.HWND, W.HDC]; u.ReleaseDC.restype = C.c_int
        u.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]; u.GetClientRect.restype = W.BOOL
        u.IsWindow.argtypes = [W.HWND]; u.IsWindow.restype = W.BOOL
        u.IsWindowVisible.argtypes = [W.HWND]; u.IsWindowVisible.restype = W.BOOL
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                                      W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
        u.CreateWindowExW.restype = W.HWND
        u.DestroyWindow.argtypes = [W.HWND]; u.DestroyWindow.restype = W.BOOL
        g.ChoosePixelFormat.argtypes = [W.HDC, C.POINTER(_PFD)]; g.ChoosePixelFormat.restype = C.c_int
        g.SetPixelFormat.argtypes = [W.HDC, C.c_int, C.POINTER(_PFD)]; g.SetPixelFormat.restype = W.BOOL
        g.GetPixelFormat.argtypes = [W.HDC]; g.GetPixelFormat.restype = C.c_int
        g.SwapBuffers.argtypes = [W.HDC]; g.SwapBuffers.restype = W.BOOL
        o.wglCreateContext.argtypes = [W.HDC]; o.wglCreateContext.restype = _HGLRC
        o.wglMakeCurrent.argtypes = [W.HDC, _HGLRC]; o.wglMakeCurrent.restype = W.BOOL
        o.wglDeleteContext.argtypes = [_HGLRC]; o.wglDeleteContext.restype = W.BOOL
        o.wglGetProcAddress.argtypes = [C.c_char_p]; o.wglGetProcAddress.restype = C.c_void_p
        self.kernel32.GetProcAddress.argtypes = [W.HMODULE, C.c_char_p]
        self.kernel32.GetProcAddress.restype = C.c_void_p

    def proc_address(self, name):
        """A GL entry point's address, as libretro's get_proc_address expects; 0 when absent."""
        if isinstance(name, str):
            name = name.encode("ascii")
        address = self.opengl32.wglGetProcAddress(name) or 0
        if address in (0, 1, 2, 3, C.c_void_p(-1).value):
            address = self.kernel32.GetProcAddress(self.opengl32._handle, name) or 0
        return address


# GL functions the presenter calls: name -> (restype, argtypes)
_FUNCTIONS = {
    "glGetError": (C.c_uint, []), "glGetString": (C.c_char_p, [C.c_uint]),
    "glGetIntegerv": (None, [C.c_uint, C.POINTER(C.c_int)]),
    "glViewport": (None, [C.c_int] * 4), "glClearColor": (None, [C.c_float] * 4), "glClear": (None, [C.c_uint]),
    "glEnable": (None, [C.c_uint]), "glDisable": (None, [C.c_uint]),
    "glColorMask": (None, [C.c_ubyte] * 4), "glPixelStorei": (None, [C.c_uint, C.c_int]),
    "glGenTextures": (None, [C.c_int, C.POINTER(C.c_uint)]), "glDeleteTextures": (None, [C.c_int, C.POINTER(C.c_uint)]),
    "glBindTexture": (None, [C.c_uint, C.c_uint]), "glTexParameteri": (None, [C.c_uint, C.c_uint, C.c_int]),
    "glTexImage2D": (None, [C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p]),
    "glTexSubImage2D": (None, [C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p]),
    "glReadPixels": (None, [C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p]),
    "glReadBuffer": (None, [C.c_uint]),
    "glDrawArrays": (None, [C.c_uint, C.c_int, C.c_int]), "glFinish": (None, []),
    "glActiveTexture": (None, [C.c_uint]), "glBindSampler": (None, [C.c_uint, C.c_uint]),
    "glBindBuffer": (None, [C.c_uint, C.c_uint]),
    "glGenFramebuffers": (None, [C.c_int, C.POINTER(C.c_uint)]), "glDeleteFramebuffers": (None, [C.c_int, C.POINTER(C.c_uint)]),
    "glBindFramebuffer": (None, [C.c_uint, C.c_uint]),
    "glFramebufferTexture2D": (None, [C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_int]),
    "glGenRenderbuffers": (None, [C.c_int, C.POINTER(C.c_uint)]), "glDeleteRenderbuffers": (None, [C.c_int, C.POINTER(C.c_uint)]),
    "glBindRenderbuffer": (None, [C.c_uint, C.c_uint]),
    "glRenderbufferStorage": (None, [C.c_uint, C.c_uint, C.c_int, C.c_int]),
    "glFramebufferRenderbuffer": (None, [C.c_uint, C.c_uint, C.c_uint, C.c_uint]),
    "glCheckFramebufferStatus": (C.c_uint, [C.c_uint]),
    "glCreateShader": (C.c_uint, [C.c_uint]), "glDeleteShader": (None, [C.c_uint]),
    "glShaderSource": (None, [C.c_uint, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_int)]),
    "glCompileShader": (None, [C.c_uint]), "glGetShaderiv": (None, [C.c_uint, C.c_uint, C.POINTER(C.c_int)]),
    "glGetShaderInfoLog": (None, [C.c_uint, C.c_int, C.POINTER(C.c_int), C.c_char_p]),
    "glCreateProgram": (C.c_uint, []), "glDeleteProgram": (None, [C.c_uint]),
    "glAttachShader": (None, [C.c_uint, C.c_uint]), "glLinkProgram": (None, [C.c_uint]),
    "glGetProgramiv": (None, [C.c_uint, C.c_uint, C.POINTER(C.c_int)]),
    "glGetProgramInfoLog": (None, [C.c_uint, C.c_int, C.POINTER(C.c_int), C.c_char_p]),
    "glUseProgram": (None, [C.c_uint]), "glGetUniformLocation": (C.c_int, [C.c_uint, C.c_char_p]),
    "glUniform1i": (None, [C.c_int, C.c_int]), "glUniform1f": (None, [C.c_int, C.c_float]),
    "glUniform2f": (None, [C.c_int, C.c_float, C.c_float]),
    "glGenVertexArrays": (None, [C.c_int, C.POINTER(C.c_uint)]), "glDeleteVertexArrays": (None, [C.c_int, C.POINTER(C.c_uint)]),
    "glBindVertexArray": (None, [C.c_uint]),
}
_UNIFORMS = ("uv_scale", "flip", "rotation", "tex", "tex_size", "out_size", "origin", "prescale", "mode", "scan", "lines", "bright")
_HIDDEN_CLASS = "STATIC"


class GLVideo:
    """A GL context on one window, made and used on one thread."""

    def __init__(self, window=None):
        self.native = _Native.get()
        self.own_window = not window
        if self.own_window:
            # WS_POPUP, never shown: a drawable for headless sessions and tools.
            window = self.native.user32.CreateWindowExW(0, _HIDDEN_CLASS, "Redux GL", 0x80000000, 0, 0, 64, 64, None, None, None, None)
            if not window:
                raise GLError(f"Could not create a hidden GL window (error {C.get_last_error()}).")
        elif not self.native.user32.IsWindow(window):
            raise GLError("The game surface window is gone.")
        self.window = window
        self.dc = self.native.user32.GetDC(window)
        if not self.dc:
            self._release_window()
            raise GLError("Could not get the game surface's device context.")
        self.context = None
        self.gl = None
        self.fbo = self.fbo_texture = self.fbo_depth = 0
        self.fbo_size = (0, 0)
        self.texture = 0
        self.texture_size = (0, 0, None)
        self.program = self.vao = 0
        self.uniforms = {}
        self.version = (0, 0)
        self.description = ""

    # ------------------------------------------------------------ context
    def create(self, context_type=HW_CONTEXT_OPENGL_CORE, major=3, minor=3, debug=False):
        n = self.native
        if n.gdi32.GetPixelFormat(self.dc) == 0:
            pfd = _PFD()
            pfd.nSize, pfd.nVersion = C.sizeof(_PFD), 1
            pfd.dwFlags = 0x4 | 0x20 | 0x1  # draw to window, OpenGL, double buffered
            pfd.iPixelType, pfd.cColorBits, pfd.cAlphaBits = 0, 32, 8
            pfd.cDepthBits, pfd.cStencilBits = 24, 8
            fmt = n.gdi32.ChoosePixelFormat(self.dc, C.byref(pfd))
            if not fmt or not n.gdi32.SetPixelFormat(self.dc, fmt, C.byref(pfd)):
                raise GLError(f"The game surface does not take an OpenGL pixel format (error {C.get_last_error()}).")
        legacy = n.opengl32.wglCreateContext(self.dc)
        if not legacy or not n.opengl32.wglMakeCurrent(self.dc, legacy):
            if legacy:
                n.opengl32.wglDeleteContext(legacy)
            raise GLError("The graphics driver did not create an OpenGL context.")
        create_attribs = n.proc_address("wglCreateContextAttribsARB")
        context = None
        if create_attribs:
            factory = C.CFUNCTYPE(_HGLRC, W.HDC, _HGLRC, C.POINTER(C.c_int))(create_attribs)
            core = context_type == HW_CONTEXT_OPENGL_CORE
            # Redux's own shader needs 3.3; a core asking for more gets at least what it asked for.
            want = max((major, minor), (3, 3))
            candidates = [want] + [version for version in ((4, 6), (4, 5), (4, 3), (4, 1), (3, 3)) if version > want]
            for version in candidates:
                attribs = [WGL_CONTEXT_MAJOR_VERSION_ARB, version[0], WGL_CONTEXT_MINOR_VERSION_ARB, version[1],
                           WGL_CONTEXT_PROFILE_MASK_ARB,
                           WGL_CONTEXT_CORE_PROFILE_BIT_ARB if core else WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB,
                           WGL_CONTEXT_FLAGS_ARB, WGL_CONTEXT_DEBUG_BIT_ARB if debug else 0, 0]
                context = factory(self.dc, None, (C.c_int * len(attribs))(*attribs))
                if context:
                    break
        n.opengl32.wglMakeCurrent(None, None)
        if context:
            n.opengl32.wglDeleteContext(legacy)
        else:
            context = legacy  # the driver's default (a compatibility context)
        if not n.opengl32.wglMakeCurrent(self.dc, context):
            n.opengl32.wglDeleteContext(context)
            raise GLError("The OpenGL context could not be made current.")
        self.context = context
        self._load()
        swap = n.proc_address("wglSwapIntervalEXT")
        if swap:
            C.CFUNCTYPE(W.BOOL, C.c_int)(swap)(0)  # the emulation clock paces frames; DWM composes without tearing
        value = C.c_int()
        self.gl.glGetIntegerv(GL_MAJOR_VERSION, C.byref(value)); major_found = value.value
        self.gl.glGetIntegerv(GL_MINOR_VERSION, C.byref(value)); minor_found = value.value
        self.gl.glGetError()
        self.version = (major_found, minor_found)
        if self.version < (3, 3):
            raise GLError(f"OpenGL 3.3 is needed; the driver offers {major_found}.{minor_found}.")
        self.description = " • ".join(filter(None, ((self.gl.glGetString(GL_RENDERER) or b"").decode("utf-8", "replace"),
                                                     (self.gl.glGetString(GL_VERSION) or b"").decode("utf-8", "replace"))))
        self._build_program()
        return self

    def _load(self):
        class Functions:
            pass
        gl = Functions()
        for name, (restype, argtypes) in _FUNCTIONS.items():
            address = self.native.proc_address(name)
            if not address:
                raise GLError(f"The OpenGL driver lacks {name}.")
            setattr(gl, name, C.CFUNCTYPE(restype, *argtypes)(address))
        self.gl = gl

    def make_current(self):
        self.native.opengl32.wglMakeCurrent(self.dc, self.context)

    def proc_address(self, name):
        return self.native.proc_address(name)

    def _shader(self, kind, source):
        gl = self.gl
        shader = gl.glCreateShader(kind)
        text = C.c_char_p(source)
        gl.glShaderSource(shader, 1, C.byref(text), None)
        gl.glCompileShader(shader)
        status = C.c_int()
        gl.glGetShaderiv(shader, GL_COMPILE_STATUS, C.byref(status))
        if not status.value:
            log = C.create_string_buffer(4096)
            gl.glGetShaderInfoLog(shader, 4096, None, log)
            raise GLError("Shader did not compile: " + log.value.decode("utf-8", "replace"))
        return shader

    def _build_program(self):
        gl = self.gl
        vertex, fragment = self._shader(GL_VERTEX_SHADER, _VERTEX), self._shader(GL_FRAGMENT_SHADER, _FRAGMENT)
        program = gl.glCreateProgram()
        gl.glAttachShader(program, vertex); gl.glAttachShader(program, fragment)
        gl.glLinkProgram(program)
        status = C.c_int()
        gl.glGetProgramiv(program, GL_LINK_STATUS, C.byref(status))
        gl.glDeleteShader(vertex); gl.glDeleteShader(fragment)
        if not status.value:
            log = C.create_string_buffer(4096)
            gl.glGetProgramInfoLog(program, 4096, None, log)
            raise GLError("Shader did not link: " + log.value.decode("utf-8", "replace"))
        self.program = program
        self.uniforms = {name: gl.glGetUniformLocation(program, name.encode()) for name in _UNIFORMS}
        vao = C.c_uint()
        gl.glGenVertexArrays(1, C.byref(vao))
        self.vao = vao.value

    # ------------------------------------------------------------ core framebuffer
    def ensure_framebuffer(self, width, height, depth=True, stencil=True):
        """The framebuffer a hardware core renders into, at least width x height."""
        if self.fbo and self.fbo_size[0] >= width and self.fbo_size[1] >= height:
            return self.fbo
        gl = self.gl
        self._delete_framebuffer()
        width, height = max(1, int(width)), max(1, int(height))
        name = C.c_uint()
        gl.glGenTextures(1, C.byref(name)); self.fbo_texture = name.value
        gl.glBindTexture(GL_TEXTURE_2D, self.fbo_texture)
        gl.glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
        self._texture_parameters()
        gl.glGenFramebuffers(1, C.byref(name)); self.fbo = name.value
        gl.glBindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        gl.glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.fbo_texture, 0)
        if depth or stencil:
            gl.glGenRenderbuffers(1, C.byref(name)); self.fbo_depth = name.value
            gl.glBindRenderbuffer(GL_RENDERBUFFER, self.fbo_depth)
            gl.glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH24_STENCIL8 if stencil else GL_DEPTH_COMPONENT24, width, height)
            gl.glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT if stencil else GL_DEPTH_ATTACHMENT,
                                         GL_RENDERBUFFER, self.fbo_depth)
            gl.glBindRenderbuffer(GL_RENDERBUFFER, 0)
        status = gl.glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise GLError(f"The game framebuffer is incomplete (0x{status:04x}).")
        gl.glClearColor(0, 0, 0, 1)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT)
        gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self.fbo_size = (width, height)
        return self.fbo

    def _delete_framebuffer(self):
        gl = self.gl
        if self.fbo:
            gl.glDeleteFramebuffers(1, C.byref(C.c_uint(self.fbo)))
        if self.fbo_depth:
            gl.glDeleteRenderbuffers(1, C.byref(C.c_uint(self.fbo_depth)))
        if self.fbo_texture:
            gl.glDeleteTextures(1, C.byref(C.c_uint(self.fbo_texture)))
        self.fbo = self.fbo_texture = self.fbo_depth = 0
        self.fbo_size = (0, 0)

    def _texture_parameters(self):
        gl = self.gl
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    # ------------------------------------------------------------ software frames
    _FORMATS = {0: (GL_BGRA, GL_UNSIGNED_SHORT_1_5_5_5_REV, 2), 1: (GL_BGRA, GL_UNSIGNED_INT_8_8_8_8_REV, 4),
                2: (GL_RGB, GL_UNSIGNED_SHORT_5_6_5, 2)}

    def upload(self, data, width, height, pitch, pixel_format):
        """Copy a software frame straight from the core's buffer into the frame texture."""
        gl = self.gl
        fmt, kind, size = self._FORMATS[pixel_format]
        if not self.texture:
            name = C.c_uint()
            gl.glGenTextures(1, C.byref(name)); self.texture = name.value
        gl.glBindTexture(GL_TEXTURE_2D, self.texture)
        gl.glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0)
        if self.texture_size != (width, height, pixel_format):
            gl.glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, fmt, kind, None)
            self._texture_parameters()
            self.texture_size = (width, height, pixel_format)
        gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        gl.glPixelStorei(GL_UNPACK_ROW_LENGTH, pitch // size)
        gl.glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height, fmt, kind, data)
        gl.glPixelStorei(GL_UNPACK_ROW_LENGTH, 0)
        gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)

    # ------------------------------------------------------------ drawing
    def client_size(self):
        rect = W.RECT()
        if not self.native.user32.GetClientRect(self.window, C.byref(rect)):
            return 0, 0
        return rect.right - rect.left, rect.bottom - rect.top

    def present(self, source, width, height, presentation, display_aspect, *, hardware, bottom_left=True, rotation=0):
        """Draw the latest frame into the window and swap. ``source`` is 'fbo' or 'texture'."""
        gl = self.gl
        window_width, window_height = self.client_size()
        if window_width <= 0 or window_height <= 0:
            return
        texture = self.fbo_texture if hardware else self.texture
        if not texture:
            return
        tex_width, tex_height = self.fbo_size if hardware else self.texture_size[:2]
        settings = dict(presentation or {})
        settings["rotation"] = rotation
        x, y, w, h = layout(width, height, display_aspect, settings, window_width, window_height)
        gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)
        for capability in (GL_SCISSOR_TEST, GL_DEPTH_TEST, GL_STENCIL_TEST, GL_BLEND, GL_CULL_FACE, GL_FRAMEBUFFER_SRGB, GL_DITHER):
            gl.glDisable(capability)
        gl.glColorMask(1, 1, 1, 1)
        gl.glViewport(0, 0, window_width, window_height)
        gl.glClearColor(0, 0, 0, 1)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        gl_y = window_height - y - h
        gl.glViewport(x, gl_y, w, h)
        gl.glUseProgram(self.program)
        gl.glBindVertexArray(self.vao)
        gl.glActiveTexture(GL_TEXTURE0)
        gl.glBindSampler(0, 0)
        gl.glBindTexture(GL_TEXTURE_2D, texture)
        u, f2, f1, i1 = self.uniforms, gl.glUniform2f, gl.glUniform1f, gl.glUniform1i
        f2(u["uv_scale"], width / tex_width, height / tex_height)
        i1(u["flip"], 0 if (hardware and bottom_left) else 1)
        i1(u["rotation"], int(rotation) & 3)
        i1(u["tex"], 0)
        f2(u["tex_size"], tex_width, tex_height)
        f2(u["out_size"], w, h)
        f2(u["origin"], x, gl_y)
        shown_width, shown_height = (height, width) if rotation in (1, 3) else (width, height)
        f2(u["prescale"], max(1.0, float(w // shown_width)), max(1.0, float(h // shown_height)))
        i1(u["mode"], FILTER_MODES.get(settings.get("filter", "clean"), 0))
        f1(u["scan"], (settings.get("scanlines", 40) if settings.get("filter") in ("scanlines", "crt") else 0) / 100)
        f1(u["lines"], float(settings.get("native_height") or shown_height))
        f1(u["bright"], settings.get("brightness", 100) / 100)
        gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        gl.glBindVertexArray(0)
        gl.glUseProgram(0)
        gl.glBindTexture(GL_TEXTURE_2D, 0)
        self.native.gdi32.SwapBuffers(self.dc)

    def read_framebuffer(self, width, height, bottom_left=True):
        """The core framebuffer's visible region as packed RGB, top row first."""
        gl = self.gl
        gl.glBindBuffer(GL_PIXEL_PACK_BUFFER, 0)
        gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, self.fbo)
        gl.glReadBuffer(GL_COLOR_ATTACHMENT0)
        gl.glPixelStorei(GL_PACK_ALIGNMENT, 1)
        gl.glPixelStorei(GL_PACK_ROW_LENGTH, 0)
        buffer = C.create_string_buffer(width * height * 3)
        gl.glReadPixels(0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE, buffer)
        gl.glPixelStorei(GL_PACK_ALIGNMENT, 4)
        gl.glBindFramebuffer(GL_READ_FRAMEBUFFER, 0)
        data = buffer.raw
        if not bottom_left:
            return data
        stride = width * 3
        return b"".join(data[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))

    # ------------------------------------------------------------ teardown
    def destroy(self):
        n = self.native
        try:
            if self.context and self.gl:
                n.opengl32.wglMakeCurrent(self.dc, self.context)
                gl = self.gl
                self._delete_framebuffer()
                if self.texture:
                    gl.glDeleteTextures(1, C.byref(C.c_uint(self.texture)))
                if self.vao:
                    gl.glDeleteVertexArrays(1, C.byref(C.c_uint(self.vao)))
                if self.program:
                    gl.glDeleteProgram(self.program)
        finally:
            n.opengl32.wglMakeCurrent(None, None)
            if self.context:
                n.opengl32.wglDeleteContext(self.context)
            self.context = None
            if self.dc:
                n.user32.ReleaseDC(self.window, self.dc)
            self.dc = None
            self._release_window()

    def _release_window(self):
        if self.own_window and self.window:
            self.native.user32.DestroyWindow(self.window)
        self.window = None


_probe_result = None
_probe_lock = threading.Lock()


def probe():
    """(available, description): whether this PC offers OpenGL 3.3 for Redux's video. Cached."""
    global _probe_result
    with _probe_lock:
        if _probe_result is None:
            video = None
            try:
                video = GLVideo().create(HW_CONTEXT_OPENGL_CORE, 3, 3)
                _probe_result = (True, video.description)
            except Exception as error:
                _probe_result = (False, str(error))
            finally:
                if video:
                    try:
                        video.destroy()
                    except Exception:
                        pass
        return _probe_result
