"""Fish from a dock until stopped (normal fish; legendary fish are left to you).

Stand at a fishing spot with the rod out (the fishing skill bar showing), then:
    python fishing/fish_bot.py            (--max-fish N, --record, --debug)
Press ` (backtick) to stop; F9 saves a debug frame.

Each catch: cast at full power, Sink Lure (5) and later Stall Lure (1),
wait for a bite (the green "!") and hook it at once, then reel by holding
the button and let go (with Tug Line, 4, if it's ready) whenever the line
loses health or the red "!" shows. Heal Line (3) when the line runs low,
Pull Fish (2) while it's healthy. On a catch: OK on "Congratulations",
"Take Small Items" and the trash can on the loot window, and cast again.

Everything was measured from a recorded session (Nov 2026, 2560x1369 game
area; debug_frames/fishing_research/20261127-002202) and live tests.
"""
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "repair"))  # shared screen code

import snapshots  # noqa: E402
from repair_mini_game import session_hotkeys  # noqa: E402
from screen import (REPO_ROOT, GameWindow, check_stop, live_session,  # noqa: E402
                    match_marker, wait)

log = logging.getLogger("repair")

# -- where things are (reference coordinates) ----------------------------------------
# The gauge left of the scene: purple while a cast's power bar bounces, then
# the line's health, green, yellow once damaged, emptying from the top.
GAUGE_REGION = (70, 380, 52, 440)          # x, y, w, h around the bar
GAUGE_X = (80, 112)                        # the bar's columns
HEALTH_TOP, HEALTH_BOTTOM = 396, 783       # a full / empty health fill
CAST_TOP, CAST_BOTTOM = 419, 799           # a full / empty power bar
GAUGE_MIN_PIXELS = 150                     # fewer coloured pixels: an empty gauge
# The fishing skills, keys 1-6, along the bottom. A ready button is lit;
# while cooling down it's about half as bright (live, keys 5 and 6).
SKILLS = {"stall": 1, "pull": 2, "heal": 3, "tug": 4, "sink": 5, "eye": 6}
SKILL_X0, SKILL_PITCH, SKILL_Y, SKILL_HALF = 988, 100, 1261, 22
SKILL_READY = (56, 124, 59, 124, 101, 164)  # brightness of each ready button
SKILL_READY_SHARE = 0.75
# Where the green (bite) and red (fight) "!" can show: the scene above the
# skill bar, right of the gauge. They're drawn over the fish, so anywhere.
SCENE_REGION = (200, 23, 2360, 1150)
CAUGHT_REGION = (1950, 203, 550, 720)      # "Congratulations! You caught a ..."
CAUGHT_OK = (2198, 803)
LOOT_REGION = (450, 273, 700, 200)         # "Plundered from a ..." (top-left part)
TAKE_SMALL = (1090, 705)                   # "Take Small Items" / "Take It All!"
LOOT_TRASH = (606, 380)                    # closes the loot window
# Binning loot with a specially dyed item asks "Are you sure ...?": No.
CONFIRM_REGION = (1000, 420, 560, 640)
CONFIRM_NO = (1335, 928)
MARKER_MATCH = 0.8
# "Fishing" under the skill bar: there in every fishing state (0.94-1.0),
# gone in the open world (0.30; live, the session had ended and the
# character was swimming off the dock).
FISHING_LABEL_REGION = (900, 1273, 700, 119)
# Clicks go over open water: live, a press near the dock's rowboat started
# no cast.
CAST_POINT = (1284, 798)

# -- timing ------------------------------------------------------------------------
# Power-bar level (rising) at which the second click goes: the bar fills in
# ~0.4 s and the click lands a few hundredths of a second later. Live,
# 0.88 gave a cast short of full.
CAST_SET_AT = 0.95
CAST_TIMEOUT = 8.0       # s for the lure to land
BAR_WAIT = 2.5           # s for the power bar to come up after the first click
# Fish swim side to side at their own depth and bite a lure held still in
# front of them (the user, Nov 2026): rest on the bottom first (the best
# fish), then work the lure up a step at a time, stalling at each depth.
BOTTOM_WAIT = 60.0       # s on the bottom before working the lure up
STEP_REEL = 0.6          # s of reeling per step up
STEP_WAIT = 5.0          # s held (Stall Lure) at each depth
# The fishing game ends itself after about a minute on the dock without a
# cast (live, Nov 2026), so cast again promptly; a lure in the water can
# wait as long as it takes.
BITE_TIMEOUT = 600.0     # s without a bite before reeling in and casting again
REEL_IN_TIMEOUT = 60.0
# Holding while the fish pulls costs line fast (the user: even a few ms
# hold loses a decent amount), so while holding only the small gauge is
# read, as fast as it comes; the slower scene checks ("!" marks, the catch
# panel) run while the button is up.
LOOK_EVERY = 0.01        # s between gauge reads while holding
LOOK_RELEASED = 0.05     # ... and while not
SCENE_EVERY = 10         # while holding, check for the catch panel every n gauge reads
DROP = 1.5 / (HEALTH_BOTTOM - HEALTH_TOP)   # a loss this big means the fish is pulling
# Tug Line stops the fish at once, so after it the reel can go on almost
# straight away; without it, wait it out, longer each time it still pulls
# (trying again costs line too).
TUG_RESUME = 0.4
PAUSE = 1.5              # s to let go when the fish pulls and Tug Line isn't ready
PAUSE_MAX = 4.0
HEAL_BELOW = 0.45        # line health share for Heal Line
PULL_ABOVE = 0.7         # ... and for Pull Fish
FIGHT_TIMEOUT = 300.0
LOOT_WAIT = 12.0         # s after OK for the loot window (it has its own 60 s countdown)
CATCH_LOG = REPO_ROOT / "debug_frames" / "fishing_catches"


# -- reading the screen ------------------------------------------------------------
class Gauge:
    """What the gauge shows: kind "cast" (power bar), "green"/"yellow"/
    "orange"/"red" (line health) or None (empty: on the dock), and its
    fill level 0-1."""

    def __init__(self, kind=None, level=0.0):
        self.kind, self.level = kind, level

    def __repr__(self):
        return "Gauge(%s, %.3f)" % (self.kind, self.level)

    @property
    def line(self):
        return self.kind in ("green", "yellow", "orange", "red")


def colour_masks(img):
    a = img.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # The line goes green, yellow, orange (live: 255,127,0) as it's hurt;
    # red is a guess at the last stage.
    return {"green": (g > 180) & (r < 100) & (b < 100),
            "yellow": (r > 180) & (g > 180) & (b < 100),
            "orange": (r > 200) & (g > 90) & (g < 170) & (b < 60),
            "red": (r > 200) & (g < 60) & (b < 60),
            "cast": (b > 200) & (r > 90) & (r < 170) & (g > 90) & (g < 170)}


def read_gauge(img, scale=1.0):
    """Gauge from a grab of GAUGE_REGION."""
    x0 = round((GAUGE_X[0] - GAUGE_REGION[0]) * scale)
    x1 = round((GAUGE_X[1] - GAUGE_REGION[0]) * scale)
    masks = colour_masks(img[:, x0:x1])
    kind = max(masks, key=lambda k: int(masks[k].sum()))
    mask = masks[kind]
    if mask.sum() < GAUGE_MIN_PIXELS * scale * scale:
        return Gauge()
    rows = np.nonzero(mask.sum(axis=1) >= max(3, mask.shape[1] // 3))[0]
    if not len(rows):
        return Gauge()
    top = GAUGE_REGION[1] + rows.min() / scale
    lo, hi = (CAST_TOP, CAST_BOTTOM) if kind == "cast" else (HEALTH_TOP, HEALTH_BOTTOM)
    return Gauge(kind, float(np.clip((hi - top) / (hi - lo), 0.0, 1.0)))


def skill_ready(img, skill, scale=1.0):
    """Is a skill's button lit (not cooling down)? ``img`` is a grab whose
    top-left is ref (0, SKILL_Y - SKILL_HALF), across the whole bar."""
    i = SKILLS[skill] - 1
    cx = round((SKILL_X0 + i * SKILL_PITCH) * scale)
    h = round(SKILL_HALF * scale)
    patch = img[:2 * h, max(0, cx - h):cx + h]
    return float(patch.max(axis=2).mean()) >= SKILL_READY_SHARE * SKILL_READY[i]


def find_mark(img, colour, scale=1.0):
    """Centre (in the image) of an "!" of ``colour`` ("green": a bite,
    "red": the fish pulling): a bar with a dot just below it, or None.
    Measured at full size: the green bar 11x61 px with a 13x12 dot 12 px
    below, the red one 22x107 with a 22x22 dot 17 px below. The limits
    below are in half-size pixels."""
    a = img[::2, ::2].astype(np.int16)               # half size is plenty and 4x faster
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    if colour == "green":
        mask = (g > 190) & (r < 90) & (b < 90)
    else:
        mask = (r > 190) & (g < 70) & (b < 70)
    if mask.sum() < 20:
        return None
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    s = scale
    bars =[st for st in stats[1:] if 3 * s <= st[2] <= 16 * s and 25 * s <= st[3] <= 75 * s
            and st[3] >= 2.5 * st[2]]
    dots = [st for st in stats[1:] if 3 * s <= st[2] <= 16 * s and 3 * s <= st[3] <= 16 * s]
    for x, y, w, h, _ in bars:
        for dx, dy, dw, dh, _ in dots:
            if abs((dx + dw / 2) - (x + w / 2)) <= 5 * s and 0 <= dy - (y + h) <= 20 * s:
                return (2 * (x + w / 2), 2 * (y + h / 2))
    return None


class Table:
    """Reads the fishing screen from a GameWindow (live or a screenshot)."""

    def __init__(self, win):
        self.win = win

    def gauge(self):
        return read_gauge(self.win.grab(*GAUGE_REGION), self.win.scale)

    def skills(self):
        grab = self.win.grab(0, SKILL_Y - SKILL_HALF, 2560, 2 * SKILL_HALF)
        return {k: skill_ready(grab, k, self.win.scale) for k in SKILLS}

    def ready(self, skill):
        return self.skills()[skill]

    def mark(self, colour):
        found = find_mark(self.win.grab(*SCENE_REGION), colour, self.win.scale)
        if found is None:
            return None
        return (SCENE_REGION[0] + found[0] / self.win.scale,
                SCENE_REGION[1] + found[1] / self.win.scale)

    def confirm(self):
        """ "Are you sure you want to trash ...?" (a specially dyed item)."""
        score, _ = match_marker(self.win.grab(*CONFIRM_REGION), "fishing/are_you_sure.png",
                                self.win.scale)
        return score >= MARKER_MATCH

    def fishing(self):
        """Is the fishing game up (not the open world)?"""
        score, _ = match_marker(self.win.grab(*FISHING_LABEL_REGION), "fishing/fishing_label.png",
                                self.win.scale)
        return score >= MARKER_MATCH

    def caught(self):
        score, _ = match_marker(self.win.grab(*CAUGHT_REGION), "fishing/you_caught.png",
                                self.win.scale)
        return score >= MARKER_MATCH

    def loot(self):
        score, _ = match_marker(self.win.grab(*LOOT_REGION), "fishing/plundered.png",
                                self.win.scale)
        return score >= MARKER_MATCH


# -- the fight ---------------------------------------------------------------------
class Reel:
    """When to hold the reel button, given the line's health over time.

    Holding while the fish pulls costs line health, and the game doesn't
    always say when it pulls (live: a shark fight showed no "!" or text at
    all), so a health drop is the sure sign: let go at once, wait a pause,
    hold again; the pause grows while the fish keeps pulling and resets
    once a hold goes by without a loss. A red "!" also means let go.
    """

    def __init__(self, now):
        self.holding = False
        self.level = None          # health when the current hold started / last steady
        self.resume_at = now       # don't hold before this
        self.pause = PAUSE
        self.held_since = None

    def update(self, now, level, red_mark=False):
        """Returns "hold", "release" or None (no change), and remembers the
        health it saw."""
        if self.holding:
            pulled = red_mark or (self.level is not None and level < self.level - DROP)
            if pulled:
                self.holding = False
                self.resume_at = now + self.pause
                self.pause = min(PAUSE_MAX, self.pause * 1.5)
                self.level = level
                return "release"
            if self.held_since is not None and now - self.held_since > 1.0:
                self.pause = PAUSE          # a calm stretch: the fish is tired again
            self.level = max(self.level, level) if self.level is not None else level
            return None
        self.level = level
        if now >= self.resume_at and not red_mark:
            self.holding = True
            self.held_since = now
            return "hold"
        return None


# -- playing -------------------------------------------------------------------------
class Fisher:
    def __init__(self, win):
        self.win = win
        self.table = Table(win)
        self.caught = self.lost = self.no_bite = 0

    def click(self):
        self.win.click(*CAST_POINT)

    def use(self, skill):
        log.debug("skill %s (%d)", skill, SKILLS[skill])
        self.win.key(str(SKILLS[skill]))

    def cast(self):
        """Cast at full power: one click starts the power bar bouncing, the
        next sets it. True once the lure is in the water; "out" if no bar
        came up because a lure is already in the water; False if neither.

        The gauge alone can't say where the lure is: live, it stays green
        on the dock after a catch until the next cast. So the click comes
        first, and only a bar that came and went counts as a cast."""
        t = self.table
        # Live: a quick first click on an idle dock was sometimes missed;
        # the deliberate hover-press-hold one menu buttons get is not.
        self.win.press(*CAST_POINT)
        deadline = time.monotonic() + CAST_TIMEOUT
        bar_by = time.monotonic() + BAR_WAIT
        last = None
        while time.monotonic() < deadline:
            check_stop()
            g = t.gauge()
            if g.kind == "cast":
                rising = last is not None and g.level > last
                if rising and g.level >= CAST_SET_AT:
                    self.click()
                    after = t.gauge()
                    log.info("Cast set at %.0f%% power (bar at %.0f%% just after).",
                             100 * g.level, 100 * after.level if after.kind == "cast" else 0)
                    break
                last = g.level
            elif last is None and time.monotonic() > bar_by:
                if g.line:
                    return "out"
                log.warning("The cast's power bar didn't show.")
                return False
            time.sleep(0.01)
        else:
            log.warning("The cast's power bar didn't show.")
            return False
        while time.monotonic() < deadline:
            check_stop()
            if t.gauge().line:
                return True
            wait(0.1)
        log.warning("The lure didn't land (a stolen lure? re-equip one from Lures).")
        return False

    def watch(self, seconds, since):
        """Watch the lure for ``seconds``: "bite" (green "!", hooked with a
        click), "gone" (the lure is back on the dock) or None."""
        t = self.table
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            check_stop()
            snapshots.take_requested(self.win, "fishing")
            if t.mark("green"):
                self.click()                       # hook it: a late click loses the fish
                log.info("Bite! Hooked it after %.0fs.", time.monotonic() - since)
                return "bite"
            if not t.gauge().line:
                return "gone"
            wait(0.05)
        return None

    def reel_step(self, seconds):
        """Reel for ``seconds``: "fish" if one came on (the line lost health
        or a "!" showed; live, a fish took the lure on the way up), "gone"
        if the lure is back, else None."""
        t = self.table
        start = t.gauge()
        self.win.mouse_down(*CAST_POINT)
        try:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                check_stop()
                g = t.gauge()
                if not g.line:
                    return "gone"
                if (start.line and g.level < start.level - DROP) or t.mark("green") \
                        or t.mark("red"):
                    return "fish"
                wait(0.05)
        finally:
            self.win.mouse_up(*CAST_POINT)
        return None

    def wait_for_bite(self):
        """Sink the lure to the bottom and wait, then work it up a step at a
        time, stalled at each depth. True once a fish is on, False when the
        lure is back on the dock without one."""
        t = self.table
        start = time.monotonic()
        if t.ready("sink"):
            self.use("sink")
        found = self.watch(BOTTOM_WAIT, start)
        if found == "bite":
            return True
        log.debug("nothing on the bottom; working the lure up")
        while found != "gone" and time.monotonic() - start < BITE_TIMEOUT:
            found = self.reel_step(STEP_REEL)
            if found == "fish":
                log.info("A fish took the lure on the way up.")
                return True
            if found == "gone":
                break
            if t.ready("stall"):
                self.use("stall")
            found = self.watch(STEP_WAIT, start)
            if found == "bite":
                return True
        log.info("No bite this cast; casting again.")
        self.no_bite += 1
        return False

    def reel_in(self):
        """Bring the lure back to the dock. True when it's back, "fish" if
        one came on instead."""
        deadline = time.monotonic() + REEL_IN_TIMEOUT
        while time.monotonic() < deadline:
            found = self.reel_step(1.0)
            if found == "gone":
                return True
            if found == "fish":
                return "fish"
        return False

    def fight(self):
        """Reel the hooked fish in. Returns "caught" or "lost"."""
        t = self.table
        start = time.monotonic()
        reel = Reel(start)
        n = 0
        red = False
        try:
            while time.monotonic() - start < FIGHT_TIMEOUT:
                check_stop()
                now = time.monotonic()
                g = t.gauge()
                if not reel.holding or n % SCENE_EVERY == 0:
                    if t.caught():
                        return "caught"
                if not reel.holding:
                    red = t.mark("red") is not None
                n += 1
                if not g.line:
                    # The gauge empties when the fish is landed (the
                    # Congratulations panel follows) or lost.
                    self.win.mouse_up(*CAST_POINT)
                    reel.holding = False
                    if self.wait_caught(3.0):
                        return "caught"
                    return "lost"
                action = reel.update(now, g.level, red)
                if action == "release":
                    self.win.mouse_up(*CAST_POINT)
                    tug = t.ready("tug")
                    if tug:
                        self.use("tug")
                        reel.resume_at = time.monotonic() + TUG_RESUME
                    log.debug("fish pulling (line %.1f%%%s): let go%s", 100 * g.level,
                              ", red !" if red else "", ", Tug Line" if tug else
                              ", waiting %.1fs" % (reel.resume_at - time.monotonic()))
                    continue
                if action == "hold":
                    self.win.mouse_down(*CAST_POINT)
                    continue
                if not reel.holding and g.level < HEAL_BELOW and t.ready("heal"):
                    self.use("heal")
                elif reel.holding and n % SCENE_EVERY == 1 and g.level > PULL_ABOVE \
                        and t.ready("pull"):
                    self.use("pull")
                time.sleep(LOOK_EVERY if reel.holding else LOOK_RELEASED)
            log.warning("The fight took over %.0fs; letting go.", FIGHT_TIMEOUT)
            return "lost"
        finally:
            self.win.mouse_up(*CAST_POINT)

    def wait_caught(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_stop()
            if self.table.caught():
                return True
            wait(0.2)
        return False

    def collect(self):
        """OK on "Congratulations", then take the small items and bin the
        loot window."""
        t = self.table
        self.log_catch()
        self.win.press(*CAUGHT_OK)
        self.win.park_mouse()
        deadline = time.monotonic() + LOOT_WAIT
        while time.monotonic() < deadline:
            check_stop()
            wait(0.3)
            self.win.refresh()
            if t.caught():
                self.win.press(*CAUGHT_OK)         # the first press was lost
                self.win.park_mouse()
                continue
            if t.confirm():
                self.answer_confirm()
                continue
            if t.loot():
                self.win.press(*TAKE_SMALL)
                wait(0.6)
                self.win.press(*LOOT_TRASH)
                self.win.park_mouse()
                wait(0.6)
                if t.confirm():
                    self.answer_confirm()
                    continue
                if not t.loot():
                    return True
        return not t.loot() and not t.caught()

    def answer_confirm(self):
        """ "Are you sure you want to trash a specially dyed clothing
        item?": No (the user, Nov 2026), then "Take It All!" (where "Take
        Small Items" was) to keep it and clear the window. If the window
        stays, its own 60 s countdown closes it."""
        log.info("The loot has a specially dyed item: keeping it (No to trashing it).")
        self.win.press(*CONFIRM_NO)
        wait(0.6)
        if self.table.loot():
            self.win.press(*TAKE_SMALL)
            self.win.park_mouse()
            wait(0.6)

    def log_catch(self):
        """Keep a small picture of the catch panel (fish, weight, rewards)."""
        try:
            from PIL import Image
            CATCH_LOG.mkdir(parents=True, exist_ok=True)
            Image.fromarray(self.win.grab(*CAUGHT_REGION)).save(
                CATCH_LOG / (time.strftime("%Y%m%d-%H%M%S") + ".png"))
        except Exception:
            log.debug("couldn't save the catch panel", exc_info=True)

    def fish_once(self):
        """One cast to one catch (or not). Returns "caught", "lost",
        "no bite", "stuck" or "not fishing" (the fishing game isn't up:
        nothing is clicked)."""
        self.win.refresh()
        if not self.table.fishing():
            return "not fishing"
        if self.table.caught() or self.table.loot() or self.table.confirm():
            self.collect()
        hooked = False
        cast = self.cast()
        if cast == "out":                          # a lure already in the water
            hooked = self.reel_in() == "fish"
            if not hooked:
                cast = self.cast()
        if not hooked:
            if cast is not True:
                return "stuck"
            if not self.wait_for_bite():
                return "no bite"
        result = self.fight()
        if result == "caught":
            self.caught += 1
            log.info("Caught %d so far.", self.caught)
            self.collect()
        else:
            self.lost += 1
            log.info("The fish got away (%d lost so far).", self.lost)
        return result


def fish_loop(win, max_fish=0):
    fisher = Fisher(win)
    stuck = 0
    while True:
        result = fisher.fish_once()
        if result == "not fishing":
            log.warning("The fishing game isn't up (no fishing skill bar); stand at a fishing "
                        "spot with the rod out. Stopping.")
            snapshots.save(win, "not_fishing", "fishing", force=True)
            break
        if result == "stuck":
            stuck += 1
            snapshots.save(win, "fishing_stuck", "fishing", force=True)
            if stuck >= 3:
                log.warning("Couldn't cast three times in a row; stopping.")
                break
            wait(2.0)
            continue
        stuck = 0
        if max_fish and fisher.caught >= max_fish:
            log.info("Caught %d; stopping (limit).", fisher.caught)
            break
    log.info("Done: %d caught, %d got away, %d casts without a bite.",
             fisher.caught, fisher.lost, fisher.no_bite)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fish from a dock.")
    parser.add_argument("--max-fish", type=int, default=0, help="stop after this many (0: no limit)")
    parser.add_argument("--max-minutes", type=float, default=0,
                        help="stop after this many minutes (0: no limit)")
    parser.add_argument("--record", action="store_true", help="save frames to debug_frames/")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()
    snapshots.recording = args.record
    with live_session(hotkeys=session_hotkeys(), debug=args.debug, max_minutes=args.max_minutes):
        fish_loop(GameWindow(), max_fish=args.max_fish)


if __name__ == "__main__":
    main()
