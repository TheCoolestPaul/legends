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
import contextlib
import ctypes
import functools
import logging
import math
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

PARK_FROM_TOP = 8    # ref px below the top of the game where the cursor rests
# press(): menu buttons only react to a click the game saw while hovering.
# A teleport-and-click (and parking straight after) can all fall inside one
# lagging frame, and the click is lost. Give each phase a frame or two.
PRESS_HOVER = 0.05   # seconds over the button before pressing
PRESS_HOLD = 0.05    # seconds the button is held down
PRESS_AFTER = 0.05   # seconds before the cursor moves on
DRAG_STEP = 15       # ref px between cursor positions while dragging
DRAG_DELAY = 0.008   # seconds between those positions (~1 per game frame)

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


# -- stopping ----------------------------------------------------------------
VK_BACKTICK = 0xC0   # the ` ~ key (US/UK layouts)
VK_LBUTTON = 0x01
KILL_POLL = 0.02     # seconds between stop-key checks
KILL_GRACE = 2.0     # seconds the bot gets to stop by itself before it is killed

_stop = threading.Event()      # a stop was requested
_finished = threading.Event()  # the bot has shut down cleanly


def request_stop():
    _stop.set()


def reset_stop():
    _stop.clear()
    _finished.clear()


def key_down(vk):
    """True while the key is physically held, read straight from Windows."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def release_mouse():
    """Release the left mouse button if it is held, so a drag never sticks."""
    if sys.platform != "win32":
        return
    try:
        if key_down(VK_LBUTTON):
            import win32api
            import win32con
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    except Exception:
        log.exception("Could not release the mouse button")


class KillSwitch:
    """Watches the stop key on its own thread.

    It polls the key state instead of installing a keyboard hook, so it keeps
    working when Windows drops hooks from a busy process. On a press it logs,
    requests a stop and releases the mouse. If the bot has not shut down
    ``grace`` seconds later (stuck somewhere that never checks for a stop),
    the whole process is killed.
    """

    def __init__(self, vk=VK_BACKTICK, grace=KILL_GRACE, poll=KILL_POLL,
                 key_state=None, kill=None, hotkeys=None):
        """``hotkeys`` maps extra keys to callbacks: {vk or state_fn: callback}.

        Each callback runs on this thread when its key goes down.
        """
        self.grace = grace
        self.poll = poll
        self.key_state = key_state or (lambda: key_down(vk))
        self.kill = kill or (lambda: os._exit(1))
        self.hotkeys = [((lambda k=key: key_down(k)) if isinstance(key, int) else key, cb)
                        for key, cb in (hotkeys or {}).items()]
        self.triggered = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="kill-switch",
                                        daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _watch(self):
        # A key already held at start must be released before it counts.
        armed = not self.key_state()
        was_down = [state() for state, _ in self.hotkeys]
        while not _finished.is_set():
            down = self.key_state()
            if down and armed:
                self.trigger("Stop key pressed")
                return
            armed = armed or not down
            for i, (state, callback) in enumerate(self.hotkeys):
                now = state()
                if now and not was_down[i]:
                    try:
                        callback()
                    except Exception:
                        log.exception("Hotkey callback failed")
                was_down[i] = now
            time.sleep(self.poll)

    def trigger(self, reason):
        self.triggered.set()
        log.warning("%s; stopping.", reason)
        _stop.set()
        release_mouse()
        if not _finished.wait(self.grace):
            log.error("Bot did not stop within %.0fs; killing it.", self.grace)
            release_mouse()
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.kill()


@contextlib.contextmanager
def live_session(hotkeys=None, debug=False):
    """Run the body as a live bot session with every way of stopping it.

    Backtick (kill switch), Ctrl+C and pyautogui's mouse-in-a-corner
    fail-safe all end the session cleanly, and the mouse button is always
    released on the way out. ``hotkeys`` are extra keys for the watcher
    thread (see KillSwitch).
    """
    import pyautogui
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    pyautogui.PAUSE = 0.01
    reset_stop()
    KillSwitch(hotkeys=hotkeys).start()
    log.info("Press ` to stop (or Ctrl+C, or move the mouse into a screen corner).")
    try:
        yield
    except StopRequested:
        log.info("Stopped.")
    except KeyboardInterrupt:
        log.info("Stopped (Ctrl+C).")
    except pyautogui.FailSafeException:
        log.info("Stopped (mouse moved to a screen corner).")
    finally:
        release_mouse()
        _finished.set()


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

    def press(self, x, y):
        """A deliberate click for menu buttons: hover, press, hold, release.

        Slower than click() (~0.15 s) but survives the game's input lag.
        """
        self.move(x, y)
        time.sleep(PRESS_HOVER)
        self.mouse_down(x, y)
        try:
            time.sleep(PRESS_HOLD)
        finally:
            self.mouse_up(x, y)
        time.sleep(PRESS_AFTER)

    def drag(self, path, step=DRAG_STEP, delay=DRAG_DELAY, grab=0.0):
        """Hold the left button and move along ``path`` (ref points) smoothly.

        The game samples the cursor once per frame, so jumping straight
        between corners looks like leaving the line; this moves in small
        steps instead. ``grab`` seconds are spent hovering before the press
        and again holding still after it, so a lagging game sees the cursor
        on what it picks up. The button is always released, even on a stop.
        """
        self.move(*path[0])
        if grab:
            time.sleep(grab)
        self.mouse_down(*path[0])
        try:
            if grab:
                time.sleep(grab)
            for (x0, y0), (x1, y1) in zip(path, path[1:]):
                n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / step))
                for i in range(1, n + 1):
                    check_stop()
                    self.move(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n)
                    if delay:
                        time.sleep(delay)
        finally:
            self.mouse_up(*path[-1])

    def park_mouse(self):
        """Move the cursor out of the way: top centre of the game, just below
        the edge. (Left centre sat over the crew list, and a lagged click
        landing there opened a crewmate's profile.)"""
        import pyautogui
        if self.is_foreground():
            pyautogui.moveTo(*self.park_point())

    def park_point(self):
        return (self.rect.left + self.rect.width // 2,
                self.rect.top + max(5, round(PARK_FROM_TOP * self.scale)))


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

    def press(self, x, y):
        self.actions.append(("click", self.to_screen(x, y)))

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
