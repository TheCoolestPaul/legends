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

Mouse input only ever goes to points inside the game area. By default it is
posted to the game window as window messages ("background" input): the game
needn't be in front and the real cursor never moves, so the PC stays usable
(keep the real cursor off the game, or its moves mix with the bot's). With
TLOPO_INPUT=mouse the real cursor is driven instead, and only while the game
is the foreground window.
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
# "background": mouse input is posted to the game window as messages, so the
#   game needn't be in front and the real cursor is never moved.
# "mouse": the real cursor is moved and clicked (pyautogui), only while the
#   game is the foreground window.
INPUT_MODES = ("background", "mouse")
DEFAULT_INPUT = "background"   # TLOPO_INPUT=mouse switches back
BG_PAUSE = 0.01         # s after each posted message (= pyautogui.PAUSE)
BG_CLICK_HOVER = 0.02   # s on a new point before pressing (background click)
BG_CLICK_HOLD = 0.01    # s the button is held
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
MK_LBUTTON, MK_RBUTTON, MK_XBUTTON1, XBUTTON1 = 0x0001, 0x0002, 0x0020, 0x0001
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


_background_windows = []      # GameWindows sending input as window messages


# -- the player's own clicks ---------------------------------------------------
VK_RBUTTON = 0x02
GA_ROOT = 2


class InputLog:
    """When the player pressed the real mouse buttons, to tell whether a
    click the game lost collided with one of theirs (live, Nov 2026: clicking
    while the bot played sometimes cost it a click).

    Background input is posted to the game window and never shows in
    GetAsyncKeyState, so every button change seen here is the player's. Only
    the left/right button state and whether the cursor was over the game are
    kept, for the last KEEP seconds; nothing leaves the process.
    """
    POLL = 0.004     # s between checks (button presses last 50+ ms)
    KEEP = 120.0     # s of history kept

    def __init__(self):
        self.hwnd = None
        self._events = []            # (time, button, down, over the game)
        self._lock = threading.Lock()
        self._thread = None

    def start(self, hwnd):
        """Watch the buttons from now on (idempotent; ``hwnd`` is the game)."""
        self.hwnd = hwnd
        if self._thread is None and sys.platform == "win32":
            self._thread = threading.Thread(target=self._watch, name="input-log", daemon=True)
            self._thread.start()

    def _watch(self):
        held = {VK_LBUTTON: False, VK_RBUTTON: False}
        while True:
            try:
                for vk in held:
                    down = key_down(vk)
                    if down != held[vk]:
                        held[vk] = down
                        self.add(time.monotonic(), "L" if vk == VK_LBUTTON else "R", down,
                                 self._over_game())
            except Exception:
                log.debug("input log check failed", exc_info=True)
            time.sleep(self.POLL)

    def _over_game(self):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        root = user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT)
        return bool(self.hwnd) and root == self.hwnd

    def add(self, t, button, down, over_game):
        with self._lock:
            self._events.append((t, button, down, over_game))
            cut = 0
            while cut < len(self._events) and self._events[cut][0] < t - self.KEEP:
                cut += 1
            del self._events[:cut]

    def near(self, t0, before=0.3, after=0.3):
        """The player's button changes from ``before`` s before ``t0`` to
        ``after`` s after it: [[button, "down"/"up", ms from t0, over game]]."""
        with self._lock:
            return [[b, "down" if d else "up", round(1000 * (t - t0)), over]
                    for t, b, d, over in self._events if t0 - before <= t <= t0 + after]


INPUT_LOG = InputLog()


def release_mouse():
    """Release the left mouse button if it is held, so a drag never sticks.

    In background-input mode the real mouse is the player's own: only the
    buttons the bot posted to the game are released.
    """
    if _background_windows:
        for win in list(_background_windows):
            try:
                win.release_buttons()
            except Exception:
                log.exception("Could not release the game's mouse buttons")
        return
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
                 key_state=None, kill=None, hotkeys=None, stop_file=None, deadline=None):
        """``hotkeys`` maps extra keys to callbacks: {vk or state_fn: callback}.

        Each callback runs on this thread when its key goes down.
        ``stop_file``: stop as if the key was pressed once this file exists
        (how the overlay stops a bot it started). ``deadline``: a
        time.monotonic() time at which to stop (the time limit).
        """
        self.stop_file = stop_file
        self.deadline = deadline
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
            if self.stop_file and os.path.exists(self.stop_file):
                self.trigger("Stop requested from the overlay")
                return
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.trigger("Time limit reached")
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
            terminate_children()
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.kill()


def terminate_children():
    """Terminate this process's multiprocessing children (the potion
    planner's simulation workers). os._exit() doesn't take them down, and
    live (Nov 2026) a stop mid-planning left 14 workers busy on every core."""
    import multiprocessing
    for child in multiprocessing.active_children():
        try:
            child.terminate()
        except Exception:
            log.exception("Could not terminate worker %s", child.pid)


@contextlib.contextmanager
def live_session(hotkeys=None, debug=False, max_minutes=None):
    """Run the body as a live bot session with every way of stopping it.

    Backtick (kill switch), Ctrl+C, the overlay's stop file (TLOPO_STOP_FILE)
    and the time limit (``max_minutes``) all end the session cleanly, and
    the mouse button is always released on the way out. ``hotkeys`` are
    extra keys for the watcher thread (see KillSwitch).
    """
    import pyautogui
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    pyautogui.PAUSE = 0.01
    reset_stop()
    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    KillSwitch(hotkeys=hotkeys, stop_file=os.environ.get("TLOPO_STOP_FILE") or None,
               deadline=deadline).start()
    log.info("Press ` to stop (or Ctrl+C).")
    if max_minutes:
        log.info("Stopping after %g minutes.", max_minutes)
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

    def __init__(self, capture=None, input=None):
        forced = capture or os.environ.get("TLOPO_CAPTURE") or None
        if forced is not None and forced not in CAPTURE_MODES:
            raise ValueError("capture must be one of %s" % (CAPTURE_MODES,))
        input = input or os.environ.get("TLOPO_INPUT") or DEFAULT_INPUT
        if input not in INPUT_MODES:
            raise ValueError("input must be one of %s" % (INPUT_MODES,))
        self.forced_capture = forced
        self.capture_mode = forced
        self.input_mode = input
        self.hwnd = None
        self.rect = None
        self._sct = None
        self._frame = None
        self._frame_time = 0.0
        self._input_paused = False
        self._held = 0          # MK_* buttons the bot holds (background mode)
        self._cursor = None     # last client point posted (background mode)

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

    @property
    def background(self):
        return self.input_mode == "background"

    def _input_target(self, x, y):
        """Screen point for (x, y), once it is safe to send input there.

        With real-mouse input, waits while the game is not the foreground
        window, so input never lands on another window. Background input
        goes to the game window only, so it needn't wait. Returns None for
        points outside the game.
        """
        while not self.background and not self.is_foreground():
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

    # Background input: window messages carry client coordinates and the
    # buttons held, like the ones Windows sends for the real mouse.
    #
    # How Panda3D (the game engine) takes them, found live with the real
    # cursor off the game (Nov 2026):
    # * WM_MOUSEMOVE is useless: Panda then asks Windows to report the
    #   cursor leaving, it isn't over the game, so WM_MOUSELEAVE follows at
    #   once and the pointer counts as gone. Buttons clicked then are lost,
    #   the hammer's target circle isn't drawn, scrubbing does nothing.
    # * Any button-down moves the pointer and keeps it in the window. So a
    #   hover is an X1 ("mouse4") press and release, which the game ignores,
    #   a click is a left press with no move before it, and a drag steps
    #   with repeated left presses.
    # * Posted all at once, a saw cut reached the game as one jump; pace
    #   every message like pyautogui does (BG_PAUSE).
    def _post(self, msg, point, wparam=0, pause=True):
        """Post a mouse message for screen ``point`` to the game window."""
        if self not in _background_windows:
            _background_windows.append(self)
            INPUT_LOG.start(self.hwnd)
        cx, cy = point[0] - self.rect.left, point[1] - self.rect.top
        self._cursor = (cx, cy)
        ctypes.windll.user32.PostMessageW(self.hwnd, msg, self._held | wparam,
                                          (cy & 0xFFFF) << 16 | (cx & 0xFFFF))
        if pause:
            time.sleep(BG_PAUSE)

    def _post_hover(self, point):
        """Put the game's pointer on ``point`` (see above), without a click."""
        self._post(WM_XBUTTONDOWN, point, XBUTTON1 << 16 | MK_XBUTTON1, pause=False)
        self._post(WM_XBUTTONUP, point, XBUTTON1 << 16)

    def _at(self, point):
        return self._cursor == (point[0] - self.rect.left, point[1] - self.rect.top)

    def _post_button(self, down_msg, up_msg, flag, point, down, pause=True):
        if down:
            self._held |= flag
            self._post(down_msg, point, pause=pause)
        else:
            self._held &= ~flag
            self._post(up_msg, point, pause=pause)

    def release_buttons(self):
        """Release whatever the bot holds in the game (background mode)."""
        if not self._held or self.hwnd is None or self._cursor is None:
            return
        point = (self.rect.left + self._cursor[0], self.rect.top + self._cursor[1])
        if self._held & MK_LBUTTON:
            self._post_button(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, point, False)
        if self._held & MK_RBUTTON:
            self._post_button(WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, point, False)

    def click(self, x, y):
        point = self._input_target(x, y)
        if not point:
            return
        if self.background:
            # Live: the game reads the pointer once a frame, so clicks
            # posted back to back all landed on the last point. Hover a
            # beat first, unless already there (hammering: the glow window
            # is ~30 ms, so the press must go at once).
            if not self._at(point):
                self._post_hover(point)
                time.sleep(BG_CLICK_HOVER)
            self._post_button(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, point, True,
                              pause=False)
            try:
                time.sleep(BG_CLICK_HOLD)
            finally:
                self._post_button(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, point, False)
        else:
            import pyautogui
            pyautogui.click(*point)

    def move(self, x, y):
        point = self._input_target(x, y)
        if not point:
            return
        if self.background:
            if self._held & MK_LBUTTON:
                self._post(WM_LBUTTONDOWN, point)     # a drag step
            else:
                self._post_hover(point)
        else:
            import pyautogui
            pyautogui.moveTo(*point)

    def mouse_down(self, x, y):
        point = self._input_target(x, y)
        if not point:
            return
        if self.background:
            self._post_button(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, point, True)
        else:
            import pyautogui
            pyautogui.mouseDown(*point)

    def mouse_up(self, x, y):
        # Always release, even if the game lost focus mid-drag.
        if self.background:
            if self._held & MK_LBUTTON:
                point = self.to_screen(x, y)
                if not self.rect.contains(*point) and self._cursor is not None:
                    point = (self.rect.left + self._cursor[0],
                             self.rect.top + self._cursor[1])
                self._post_button(WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, point, False)
            return
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

    def right_click(self, x, y):
        """A deliberate right click (potions: rotate the pair in hand)."""
        import pyautogui
        point = self._input_target(x, y)
        if point and self.background:
            self._post_hover(point)
            time.sleep(PRESS_HOVER)
            self._post_button(WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, point, True)
            try:
                time.sleep(PRESS_HOLD)
            finally:
                self._post_button(WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, point, False)
            time.sleep(PRESS_AFTER)
        elif point:
            pyautogui.moveTo(*point)
            time.sleep(PRESS_HOVER)
            pyautogui.mouseDown(*point, button="right")
            try:
                time.sleep(PRESS_HOLD)
            finally:
                pyautogui.mouseUp(button="right")
            time.sleep(PRESS_AFTER)

    def park_mouse(self):
        """Move the cursor out of the way: top centre of the game, just below
        the edge. (Left centre sat over the crew list, and a lagged click
        landing there opened a crewmate's profile.)"""
        if self.background:
            self._post_hover(self.park_point())
            return
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

    def right_click(self, x, y):
        self.actions.append(("right", self.to_screen(x, y)))

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
