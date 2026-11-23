"""Base class and shared detection for the ship-repair minigames.

All coordinates are reference coordinates (see screen.py); the window object
maps them onto the live game window, whatever its size.
"""
import argparse
import logging
import time
from collections import namedtuple

import cv2
import numpy as np

import snapshots
from screen import (GameWindow, WindowNotFound, check_stop, match_marker,
                    timed_out, wait)

log = logging.getLogger("repair")

Point = namedtuple("Point", "x y")

# Where the minigame title ("Hammering", "Hull Patching", ...) is drawn.
TITLE_REGION = (1000, 130, 560, 270)
TITLE_THRESHOLD = 0.8  # measured: right title 0.97, best wrong title 0.66

# Centres of the five repair buttons, left to right. The first slot is hull
# scrubbing in port and bilge pumping at sea.
# Measured on the live game at 2560x1369 (Nov 2026). The 2020-era 1080p
# screenshots in tests/test_pics draw this bar ~19 ref px lower.
BUTTONS = [Point(840, 1226), Point(1061, 1226), Point(1275, 1226),
           Point(1500, 1226), Point(1715, 1226)]
COMPLETED_GREEN_FRACTION = 0.05  # measured: done 0.15, not done 0.00
STILL_OPEN_CACHE = 0.1  # seconds a still_open() answer is reused
IN_PROGRESS_FRACTION = 0.035  # yellow ring share: a game in progress
BANNER_GRACE = 0.35  # seconds after the start banner goes before acting

# A piece of the button bar's wood, used to tell that the repair UI is open.
BAR_MARKER = "repair_bar.png"
BAR_REGION = (870, 1160, 160, 160)
BAR_THRESHOLD = 0.85  # measured: repair UI 0.96, other screens <= 0.70


# The "Ready!" banner every game shows as it starts; input before it clears is
# ignored (and its letters fool the nail finder). Matched on the shape of its
# light-grey letters, since it is drawn see-through over different boards.
READY_MARKER = "ready_banner.png"
READY_REGION = (1025, 611, 513, 254)
READY_THRESHOLD = 0.6  # measured: banner 0.86-1.00, other frames <= 0.33


def _light_text(rgb):
    img = rgb.astype(int)
    lo, hi = img.min(axis=-1), img.max(axis=-1)
    mask = ((lo > 150) & (hi - lo < 45)).astype(np.uint8) * 255
    return np.dstack([mask] * 3)


def ready_banner_visible(win):
    score, _ = match_marker(_light_text(win.grab(*READY_REGION)), READY_MARKER, win.scale)
    return score >= READY_THRESHOLD


# After "Ready!" each game flashes its own word in the same place: "Scrub!",
# "Saw!", "Brace!", "Hammer!", "Patch!". Rather than a template per word,
# count tall bright letters. Measured over 255 live frames: banners have
# 3+ letters at least 40 px tall; gameplay has 0, or 1 (a barnacle glint).
BANNER_LETTER_HEIGHT = 30   # ref px
BANNER_MIN_LETTERS = 3


def banner_letters(win):
    """Number of tall, bright, banner-style letters in the banner area."""
    mask = (_light_text(win.grab(*READY_REGION))[..., 0] > 0).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    s = win.scale
    return sum(1 for i in range(1, count)
               if stats[i, 3] / s >= BANNER_LETTER_HEIGHT and stats[i, 4] / (s * s) >= 80)


def banner_visible(win):
    """True while any start-of-game banner ("Ready!", "Brace!", ...) shows."""
    return banner_letters(win) >= BANNER_MIN_LETTERS or ready_banner_visible(win)


def button_completed(win, button):
    """True if the button shows the green "done" ring and check mark."""
    img = win.grab(button.x - 45, button.y - 45, 90, 90).astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return bool(((g > 150) & (r < 90) & (b < 90)).mean() >= COMPLETED_GREEN_FRACTION)


def button_in_progress(win, button):
    """True if the button shows the yellow progress ring: its game is being
    played. On a crewed ship that can be another player, and then clicking
    it opens nothing for us. Measured: in progress 0.046-0.131, idle or done
    0.023 or less."""
    img = win.grab(button.x - 45, button.y - 45, 90, 90).astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    yellow = (r > 170) & (g > 140) & (b < 90) & (r - b > 100)
    return bool(yellow.mean() >= IN_PROGRESS_FRACTION)


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
        self.info = {}      # what the last step saw; saved with snapshots
        self._misses = {}

    def __repr__(self):
        return self.name

    # -- debugging --------------------------------------------------------
    def snapshot(self, reason, **info):
        """Save the current frame with what this game last saw."""
        return snapshots.save(self.win, reason, game=self, info={**self.info, **info})

    def found(self, what):
        self._misses[what] = 0

    def nothing_found(self, what, after=15):
        """Count a step that found no ``what``; snapshot once after a streak."""
        self._misses[what] = self._misses.get(what, 0) + 1
        if self._misses[what] == after:
            self.snapshot("no_" + what)

    # -- detection --------------------------------------------------------
    def title_score(self, title_image=None):
        if title_image is None:
            title_image = self.win.grab(*TITLE_REGION)
        return match_marker(title_image, self.title_marker, self.win.scale)[0]

    def is_active(self, title_image=None):
        return self.title_score(title_image) >= TITLE_THRESHOLD

    def is_completed(self):
        return button_completed(self.win, self.button)

    def still_open(self):
        """Is this game still on screen? Call before acting inside a long
        step: on a crewed ship someone else can finish the repair and the
        panel vanishes mid-step. Cached for STILL_OPEN_CACHE seconds."""
        now = time.monotonic()
        cached = getattr(self, "_open_cache", None)
        if cached is not None and now - cached[0] < STILL_OPEN_CACHE:
            return cached[1]
        open_now = self.is_active()
        self._open_cache = (now, open_now)
        if not open_now:
            log.info("%s is no longer on screen; stopping", self.name)
        return open_now

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
        self.snapshot("did_not_open")
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
        self.info, self._misses = {}, {}
        self.timed_out = False
        banner_gone_at = None
        while True:
            check_stop()
            self.win.refresh()
            snapshots.take_requested(self.win, self)
            if self.is_completed():
                log.info("%s done in %.0fs", self.name, time.monotonic() - started)
                return True
            if not self.is_active():
                # Also what finishing the last game looks like: the ship is
                # repaired and the next one starts with every button reset.
                log.info("%s closed (ship repaired, or the game was left)", self.name)
                return False
            if timed_out(deadline):
                log.warning("%s gave up after %ds", self.name, self.timeout)
                self.snapshot("timeout")
                self.timed_out = True
                return False
            if banner_visible(self.win):
                banner_gone_at = None
                wait(0.05)  # "Ready!" / "Brace!"...: the game isn't taking input yet
                continue
            # Live: input is still ignored for a moment after the banner
            # goes, so the first moves of a game were lost. Give it a beat.
            if banner_gone_at is None:
                banner_gone_at = time.monotonic()
            if time.monotonic() - banner_gone_at < BANNER_GRACE:
                wait(0.05)
                continue
            # The frame is taken before this step runs, so what's attached is
            # what the *previous* step saw (it may already have acted on it).
            snapshots.record(self.win, self, {"previous_step": self.info})
            self.step()

    def step(self):
        """One round of play. Subclasses implement this."""
        raise NotImplementedError


def parse_args(description):
    """Command-line options shared by every entry point."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--record", action="store_true",
                        help="save about one frame per second of every game "
                             "to debug_frames/ (for building test fixtures)")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()
    snapshots.recording = args.record
    return args


def session_hotkeys():
    """F9 saves a debug frame of whatever is on screen."""
    return {snapshots.VK_F9: snapshots.request}


def run_standalone(game_class):
    """Entry point for running a single minigame from its own file."""
    from screen import live_session
    args = parse_args("Play %s once." % game_class.name)
    with live_session(hotkeys=session_hotkeys(), debug=args.debug):
        log.info("F9 saves a debug frame%s.", "; recording frames" if args.record else "")
        try:
            game_class().repair()
        except WindowNotFound as e:
            log.error("%s", e)
