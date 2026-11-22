"""Base class and shared detection for the ship-repair minigames.

All coordinates are reference coordinates (see screen.py); the window object
maps them onto the live game window, whatever its size.
"""
import logging
import time
from collections import namedtuple

from screen import (GameWindow, StopRequested, WindowNotFound, check_stop,
                    match_marker, timed_out, wait)

log = logging.getLogger("repair")

Point = namedtuple("Point", "x y")

# Where the minigame title ("Hammering", "Hull Patching", ...) is drawn.
TITLE_REGION = (1000, 130, 560, 270)
TITLE_THRESHOLD = 0.8  # measured: right title 0.97, best wrong title 0.66

# Centres of the five repair buttons, left to right. The first slot is hull
# scrubbing in port and bilge pumping at sea.
BUTTONS = [Point(841, 1245), Point(1061, 1245), Point(1276, 1245),
           Point(1497, 1245), Point(1716, 1245)]
COMPLETED_GREEN_FRACTION = 0.05  # measured: done 0.15, not done 0.00

# A piece of the button bar's wood, used to tell that the repair UI is open.
BAR_MARKER = "repair_bar.png"
BAR_REGION = (870, 1160, 160, 160)
BAR_THRESHOLD = 0.85  # measured: repair UI 0.96, other screens <= 0.70


def button_completed(win, button):
    """True if the button shows the green "done" ring and check mark."""
    img = win.grab(button.x - 45, button.y - 45, 90, 90).astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return bool(((g > 150) & (r < 90) & (b < 90)).mean() >= COMPLETED_GREEN_FRACTION)


def repair_bar_visible(win):
    score, _ = match_marker(win.grab(*BAR_REGION), BAR_MARKER, win.scale)
    return score >= BAR_THRESHOLD


class RepairMiniGame:
    name = None
    title_marker = None  # path under images/markers/
    button = None        # Point in BUTTONS
    timeout = 180        # seconds before giving up on one game
    start_timeout = 3    # seconds to wait for the game to open after a click

    def __init__(self, window=None):
        self.win = window if window is not None else GameWindow()

    def __repr__(self):
        return self.name

    # -- detection --------------------------------------------------------
    def title_score(self, title_image=None):
        if title_image is None:
            title_image = self.win.grab(*TITLE_REGION)
        return match_marker(title_image, self.title_marker, self.win.scale)[0]

    def is_active(self, title_image=None):
        return self.title_score(title_image) >= TITLE_THRESHOLD

    def is_completed(self):
        return button_completed(self.win, self.button)

    # -- control ----------------------------------------------------------
    def start(self):
        """Click this game's button and wait for it to open."""
        self.win.click(*self.button)
        self.win.park_mouse()
        deadline = time.monotonic() + self.start_timeout
        while not timed_out(deadline):
            wait(0.2)
            if self.is_active():
                return True
        log.warning("%s did not open after clicking its button", self.name)
        return False

    def repair(self):
        """Open this game if needed and play it. Returns True if completed."""
        self.win.refresh()
        if self.is_completed():
            return True
        if not self.is_active() and not self.start():
            return False
        return self.play()

    def play(self):
        """Run step() until the game is completed, closes or times out."""
        log.info("Playing %s", self.name)
        started = time.monotonic()
        deadline = started + self.timeout
        while True:
            check_stop()
            self.win.refresh()
            if self.is_completed():
                log.info("%s done in %.0fs", self.name, time.monotonic() - started)
                return True
            if not self.is_active():
                log.info("%s closed before it was completed", self.name)
                return False
            if timed_out(deadline):
                log.warning("%s gave up after %ds", self.name, self.timeout)
                return False
            self.step()

    def step(self):
        """One round of play. Subclasses implement this."""
        raise NotImplementedError


def run_standalone(game_class):
    """Entry point for running a single minigame from its own file."""
    import pyautogui
    from screen import prepare_live
    prepare_live()
    try:
        game_class().repair()
    except StopRequested:
        log.info("Stopped.")
    except pyautogui.FailSafeException:
        log.info("Stopped (mouse moved to a screen corner).")
    except WindowNotFound as e:
        log.error("%s", e)
