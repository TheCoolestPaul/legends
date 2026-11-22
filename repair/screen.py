"""Window, coordinate and screen-capture helpers shared by the repair bots.

Every coordinate in the repair bots is written in *reference* coordinates:
screen pixels on the setup the bots were tuned on, a maximised
2560x1440 window whose client (game) area is 2560x1369 starting at
screen (0, 23) (read from the live window).

TLOPO is a Panda3D game, and its 2D UI scales with the window *height* and
stays centred horizontally. So a reference point is mapped to the live window
by scaling its offset from the reference client centre by
``live_height / 1369`` and adding it to the live client centre. That model was
checked against 1920x1080 screenshots in tests/test_pics: the title markers
match there at scale 0.74 (~ 1017 / 1369) and the button positions line up.

Capture only ever reads the game window's client area:

* pixels are copied from the game window itself (BitBlt from its device
  context, or PrintWindow if BitBlt gives a blank frame), so other windows
  on top of the game are never captured;
* every region is clipped to the game area, and anything outside it comes
  back black;
* only if both window methods return blank frames does it fall back to
  copying the game area from the screen, and it logs a warning when it does.
  Set TLOPO_CAPTURE=bitblt|printwindow|screen to force a method.

Mouse input is only sent while the game is the foreground window, and only
to points inside the game area.
"""
import ctypes
import functools
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("repair")

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKERS = REPO_ROOT / "images" / "markers"

# Reference client area (see module docstring).
REF_LEFT, REF_TOP, REF_WIDTH, REF_HEIGHT = 0, 23, 2560, 1369
REF_CENTER_X = REF_LEFT + REF_WIDTH / 2
REF_CENTER_Y = REF_TOP + REF_HEIGHT / 2

WINDOW_TITLES = (
    "The Legend of Pirates Online [BETA]",
    "The Legend of Pirates Online",
)

CAPTURE_MODES = ("bitblt", "printwindow", "screen")
PRINTWINDOW_REUSE = 0.015  # s; one PrintWindow frame serves grabs this close together
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2


def _make_dpi_aware():
    """Make Win32 calls, mss and pyautogui all agree on physical pixels."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_make_dpi_aware()


class StopRequested(Exception):
    """Raised inside bot loops when the stop hotkey was pressed."""


class WindowNotFound(Exception):
    """The game window is missing or minimised."""


_stop = threading.Event()


def install_stop_hotkey(key="`"):
    """Pressing ``key`` anywhere stops the bots at their next check."""
    import keyboard
    keyboard.add_hotkey(key, request_stop)
    log.info("Press %r to stop.", key)


def prepare_live(stop_key="`"):
    """Set up logging, input pacing and the stop hotkey for a live run."""
    import pyautogui
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    pyautogui.PAUSE = 0.01
    install_stop_hotkey(stop_key)


def request_stop():
    _stop.set()


def reset_stop():
    _stop.clear()


def check_stop():
    if _stop.is_set():
        raise StopRequested()


def wait(seconds):
    """Sleep, but wake up and raise StopRequested as soon as stop is pressed."""
    if _stop.wait(seconds):
        raise StopRequested()


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def contains(self, x, y):
        return (self.left <= x < self.left + self.width
                and self.top <= y < self.top + self.height)


def is_blank(img):
    """True for a uniform frame, which is what a failed window capture gives."""
    return img.size == 0 or int(img.max()) - int(img.min()) < 8


def _bitmap_to_rgb(bitmap, width, height):
    bits = bitmap.GetBitmapBits(True)
    return np.frombuffer(bits, np.uint8).reshape(height, width, 4)[:, :, 2::-1].copy()


def capture_bitblt(hwnd, x, y, width, height):
    """Copy part of a window's client area from its own device context."""
    import win32con
    import win32gui
    import win32ui
    hdc = win32gui.GetDC(hwnd)
    try:
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(src, width, height)
            mem.SelectObject(bitmap)
            mem.BitBlt((0, 0), (width, height), src, (x, y), win32con.SRCCOPY)
            return _bitmap_to_rgb(bitmap, width, height)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            mem.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, hdc)  # a GetDC handle is released, never deleted


def capture_printwindow(hwnd, width, height):
    """Ask a window to render its whole client area into a bitmap."""
    import win32gui
    import win32ui
    hdc = win32gui.GetDC(hwnd)
    try:
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(src, width, height)
            mem.SelectObject(bitmap)
            ok = ctypes.windll.user32.PrintWindow(
                hwnd, mem.GetSafeHdc(), PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            if not ok:
                raise OSError("PrintWindow failed")
            return _bitmap_to_rgb(bitmap, width, height)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            mem.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, hdc)


class GameWindow:
    """The live game window. Methods take and return reference coordinates."""

    def __init__(self, capture=None):
        forced = capture or os.environ.get("TLOPO_CAPTURE") or None
        if forced is not None and forced not in CAPTURE_MODES:
            raise ValueError("capture must be one of %s" % (CAPTURE_MODES,))
        self.forced_capture = forced
        self.capture_mode = forced
        self.hwnd = None
        self.rect = None
        self._sct = None
        self._frame = None
        self._frame_time = 0.0
        self._input_paused = False

    # -- window geometry -------------------------------------------------
    def _find_window(self):
        import win32gui
        hwnd = 0
        for title in WINDOW_TITLES:
            hwnd = win32gui.FindWindow(None, title)
            if hwnd:
                break
        if not hwnd:
            raise WindowNotFound("TLOPO window not found")
        if win32gui.IsIconic(hwnd):
            raise WindowNotFound("TLOPO window is minimised")
        _, _, width, height = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
        if width < 100 or height < 100:
            raise WindowNotFound("TLOPO window is too small")
        return hwnd, Rect(left, top, width, height)

    def refresh(self):
        """Re-read the window position/size. Call once per loop iteration."""
        hwnd, rect = self._find_window()
        if hwnd != self.hwnd:
            self.capture_mode = self.forced_capture  # new window: probe again
            self._frame = None
        if rect != self.rect:
            log.info("Game area %dx%d at (%d, %d), scale %.3f",
                     rect.width, rect.height, rect.left, rect.top,
                     rect.height / REF_HEIGHT)
            self._frame = None
        self.hwnd, self.rect = hwnd, rect
        return rect

    @property
    def scale(self):
        return self.rect.height / REF_HEIGHT

    # -- coordinate mapping ---------------------------------------------
    def to_screen(self, x, y):
        s = self.scale
        sx = self.rect.left + self.rect.width / 2 + (x - REF_CENTER_X) * s
        sy = self.rect.top + self.rect.height / 2 + (y - REF_CENTER_Y) * s
        return round(sx), round(sy)

    def region_to_screen(self, left, top, width, height):
        x0, y0 = self.to_screen(left, top)
        x1, y1 = self.to_screen(left + width, top + height)
        return Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    def image_to_ref(self, region, ix, iy):
        """Convert a pixel in an image returned by grab(*region) to ref coords."""
        left, top = region[0], region[1]
        return left + ix / self.scale, top + iy / self.scale

    # -- capture ----------------------------------------------------------
    def grab(self, left, top, width, height):
        """Capture a reference-coordinate region as an RGB numpy array."""
        return self._grab_rect(self.region_to_screen(left, top, width, height))

    def pixel(self, x, y):
        sx, sy = self.to_screen(x, y)
        r, g, b = self._grab_rect(Rect(sx, sy, 1, 1))[0, 0]
        return int(r), int(g), int(b)

    def _grab_rect(self, want):
        """Capture a screen rect, reading only the part inside the game area.

        The result always has the requested size; pixels outside the game
        area are black.
        """
        out = np.zeros((want.height, want.width, 3), np.uint8)
        c = self.rect
        x0, y0 = max(want.left, c.left), max(want.top, c.top)
        x1 = min(want.left + want.width, c.left + c.width)
        y1 = min(want.top + want.height, c.top + c.height)
        if x1 > x0 and y1 > y0:
            out[y0 - want.top:y1 - want.top, x0 - want.left:x1 - want.left] = \
                self._capture_client(x0 - c.left, y0 - c.top, x1 - x0, y1 - y0)
        return out

    def _capture_client(self, x, y, width, height):
        """Pixels of the game area at client coords (already clipped)."""
        if self.capture_mode is None:
            self.capture_mode = self._probe_capture()
        if self.capture_mode == "bitblt":
            return capture_bitblt(self.hwnd, x, y, width, height)
        if self.capture_mode == "printwindow":
            return self._printwindow_frame()[y:y + height, x:x + width]
        return self._capture_screen(x, y, width, height)

    def _probe_capture(self):
        """Pick the first window-capture method that gives a real frame."""
        w, h = self.rect.width, self.rect.height
        probes = [("bitblt", lambda: capture_bitblt(self.hwnd, 0, 0, w, h)),
                  ("printwindow", lambda: capture_printwindow(self.hwnd, w, h))]
        for mode, probe in probes:
            try:
                frame = probe()
            except Exception as e:
                log.debug("%s capture failed: %s", mode, e)
                continue
            if not is_blank(frame):
                log.info("Capturing from the game window (%s).", mode)
                return mode
        log.warning("Window capture gave blank frames; copying the game area from "
                    "the screen instead. Keep other windows off the game.")
        return "screen"

    def _printwindow_frame(self):
        now = time.monotonic()
        if self._frame is None or now - self._frame_time > PRINTWINDOW_REUSE:
            self._frame = capture_printwindow(self.hwnd, self.rect.width, self.rect.height)
            self._frame_time = now
        return self._frame

    def _capture_screen(self, x, y, width, height):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        shot = self._sct.grab({"left": self.rect.left + x, "top": self.rect.top + y,
                               "width": width, "height": height})
        return np.asarray(shot)[:, :, 2::-1]  # BGRA -> RGB

    # -- input ------------------------------------------------------------
    def is_foreground(self):
        import win32gui
        return win32gui.GetForegroundWindow() == self.hwnd

    def _input_target(self, x, y):
        """Screen point for (x, y), once it is safe to send input there.

        Waits while the game is not the foreground window, so input never
        lands on another window. Returns None for points outside the game.
        """
        while not self.is_foreground():
            if not self._input_paused:
                log.warning("Game window is not in front; pausing until it is.")
                self._input_paused = True
            wait(0.5)
            self.refresh()
        if self._input_paused:
            log.info("Game window is in front again; resuming.")
            self._input_paused = False
        point = self.to_screen(x, y)
        if not self.rect.contains(*point):
            log.debug("Skipping input at %s: outside the game area", point)
            return None
        return point

    def click(self, x, y):
        import pyautogui
        point = self._input_target(x, y)
        if point:
            pyautogui.click(*point)

    def move(self, x, y):
        import pyautogui
        point = self._input_target(x, y)
        if point:
            pyautogui.moveTo(*point)

    def mouse_down(self, x, y):
        import pyautogui
        point = self._input_target(x, y)
        if point:
            pyautogui.mouseDown(*point)

    def mouse_up(self, x, y):
        # Always release, even if the game lost focus mid-drag.
        import pyautogui
        pyautogui.mouseUp()

    def park_mouse(self):
        """Move the cursor to the left edge of the game so it hides nothing."""
        import pyautogui
        if self.is_foreground():
            pyautogui.moveTo(self.rect.left + 10, self.rect.top + self.rect.height // 2)


class ImageWindow(GameWindow):
    """A GameWindow backed by a screenshot file, for offline tests.

    ``client`` is the game area inside the screenshot. Input methods record
    what they would have done instead of moving the real mouse.
    """

    def __init__(self, image, client):
        super().__init__()
        self.image = np.asarray(image)[:, :, :3]
        self.rect = client
        self.actions = []

    def refresh(self):
        return self.rect

    def _capture_client(self, x, y, width, height):
        left, top = self.rect.left + x, self.rect.top + y
        return self.image[top:top + height, left:left + width]

    def click(self, x, y):
        self.actions.append(("click", self.to_screen(x, y)))

    def move(self, x, y):
        self.actions.append(("move", self.to_screen(x, y)))

    def mouse_down(self, x, y):
        self.actions.append(("down", self.to_screen(x, y)))

    def mouse_up(self, x, y):
        self.actions.append(("up", self.to_screen(x, y)))

    def park_mouse(self):
        pass


# -- template matching ----------------------------------------------------
@functools.lru_cache(maxsize=None)
def load_marker(relative_path):
    """Load a marker image (captured at reference scale) as grayscale."""
    path = MARKERS / relative_path
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(path)
    return img


@functools.lru_cache(maxsize=256)
def _scaled_marker(relative_path, scale):
    img = load_marker(relative_path)
    if abs(scale - 1.0) < 1e-3:
        return img
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


def match_marker(image_rgb, relative_path, scale):
    """Best normalised match score of a marker in an RGB image, at ``scale``.

    Returns (score, (x, y)) where (x, y) is the marker's centre in image
    pixels, or (0.0, None) if the marker does not fit in the image.
    """
    tpl = _scaled_marker(relative_path, round(scale, 3))
    gray = cv2.cvtColor(np.ascontiguousarray(image_rgb), cv2.COLOR_RGB2GRAY)
    if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
        return 0.0, None
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), (loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2)


def timed_out(deadline):
    return time.monotonic() > deadline
