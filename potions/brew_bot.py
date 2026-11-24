"""Play the potion-brewing table until the potion is done (or the board fills).

Run from anywhere, with a potion open at the brewing table:
    python potions/brew_bot.py            (--once stops after one potion,
                                           --record saves frames, --debug logs more)
It brews the open potion, presses "Brew Again" and repeats until stopped.
Press ` (backtick) to stop; F9 saves a debug frame.

Shares the repair bots' screen code: the game is captured from its own
window, every position is in reference coordinates (2560x1369 client) and
scaled to the real window, and input only goes to the game while it's in
front.
"""
import logging
import sys
import threading
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "repair"))  # shared screen code
sys.path.insert(0, str(HERE))

import snapshots  # noqa: E402
from brewing import COLUMNS, Board, best_move, oriented  # noqa: E402
from repair_mini_game import session_hotkeys  # noqa: E402
from screen import (REPO_ROOT, GameWindow, check_stop, live_session,  # noqa: E402
                    match_marker, wait)

log = logging.getLogger("repair")

INGREDIENTS = REPO_ROOT / "images" / "potions" / "ingredients"
# Geometry, measured live (Nov 2026) in reference coordinates.
COL_X0, COL_PITCH = 1348, 95.33          # column centres
ROW0_Y = (1133, 1188)                    # bottom cell, even / odd columns
ROW_PITCH = 110.67
HAND_ROW = 9                             # the pair in hand hovers above the top row
BOARD_ROWS = 9
NEXT_PAIR = ((967, 137), (1062, 192))    # the "next" preview, left and right
RECIPE_X, RECIPE_Y0, RECIPE_PITCH = 526, 481, 116.2
RECIPE_SLOTS = 6                         # rows of the recipe list read
CHECK_REGION_X, CHECK_REGION_W = (600,), 70   # check mark beside a slot (ref x, width)
CHECK_HALF_H = 30
CHECK_DARK = 0.15                        # share of dark pixels that means "checked"
GAME_OVER_REGION = (900, 400, 400, 120)  # where the "Game Over" title shows
CONTINUE_BUTTON = (1410, 648)            # Game Over dialog: back to the recipe list
BREWED_REGION = (980, 320, 600, 130)     # where "Congratulations!" shows
BREW_AGAIN_BUTTON = (1285, 915)          # Congratulations dialog: same potion again
MAXED_REGION = (1500, 470, 400, 110)     # "Potions Maxed Out" (inventory full) title
MAXED_OK_BUTTON = (1615, 763)            # ... "Continue anyway?" OK
NEXT_POTION_TIMEOUT = 10.0               # seconds for the next board after Brew Again
CONGRATS_CONTINUE_BUTTON = (1654, 915)   # Congratulations dialog: to the recipe list
SWITCH_RECIPE_BUTTON = (882, 1127)       # under the recipe: "switch recipe"
SWITCH_OK_BUTTON = MAXED_OK_BUTTON       # "Switch Recipe ... lose your progress?" OK
LIST_TITLE_REGION = (1450, 120, 450, 90) # "Potion Recipe List"
LIST_X, LIST_Y0, LIST_PITCH = 1580, 223, 42.1   # recipe list rows (first page)
# The first page of the recipe list on a level-20 account (Nov 2026). Rows
# shift as recipes unlock, so --recipes also takes row numbers.
RECIPES = [
    "Marksman Draught I", "Swashbuckler Stew I", "Cannoneer Draft I", "Mystic Mixture I",
    "Swift Foot I", "Belchin' Brew", "Flatulent Fizz", "Deadeye I", "Boom Brew I",
    "Lively Bucko Brew I", "Ghastly Visage", "Pantano Silver Spirit I",
    "Marksman Draught II", "Swashbuckler Stew II", "Clap O'Thunder", "Cannoneer Draft II",
    "Mystic Mixture II", "Swift Foot II", "Puke Potion", "Deadeye II", "Boom Brew II",
    "Lively Bucko Brew II",
]
UNKNOWN = ("?", 0)                       # a piece that couldn't be read
# Everything the reader looks at (board, hand row, next pair, recipe and its
# check marks), captured once per look instead of once per cell.
TABLE_REGION = (440, 60, 1680, 1220)
# Recipe slots: an unmade ingredient's icon is drawn in grey, so its colour
# comes from the hex border. Live, Pincer Potion's grey Crab Soul (blue 6)
# matched purple 6 best. Median of the saturated pixels in a ring between
# the icon and the rivets (ref px from the slot centre), measured Nov 2026:
BORDER_RING = (42, 50)
BORDER_COLOURS = {
    "red": (129, 39, 43), "blue": (60, 88, 136), "green": (82, 102, 35),
    "orange": (141, 81, 21), "purple": (120, 64, 108),
    None: (195, 173, 126),               # bare parchment: no slot here
}
MIN_BORDER_PIXELS = 300                  # fewer coloured pixels: a gray border
RECIPE_MIN_SCORE = 0.5                   # tier match within a known border colour
MATCH_WAIT = 1.5                         # s for the board to reach the prediction
DROP_HOLD = 0.05                         # s the button is held to drop a pair
PIECE_SCALE = 0.84                       # board pieces vs the ingredient chart
INNER = (0.2, 0.8)                       # part of a chart hex compared (no corners)
SEARCH = 8                               # ref px of slack around a cell centre
MIN_SCORE = 0.8                          # below this a cell is empty
# Live: merges and chains animate for seconds after a drop, and the next
# pair only shows once they're done. Reads mid-animation are wrong, so the
# board counts as settled once a pair is in hand and nothing has changed
# for STEADY seconds.
STEADY = 0.5
TURN_TIMEOUT = 12.0                      # seconds to wait for the next pair
AIM_Y = ROW0_Y[0] - 4 * ROW_PITCH        # cursor height while aiming (mid board)
AIM_TIMEOUT = 4.0                        # s for the pair to follow the cursor
AIM_RESEND = 0.5                         # re-send the cursor move this often
AIM_RETRIES = 3                          # turns in a row the pair may fail to follow
# Pairs dealt before the bot trusts which colours this island deals. With
# four colours, one is missing from 24 pieces about 0.1% of the time.
POOL_CHECK_PAIRS = 12
# Recipes are normally judged by a simulated plan (planner.py). If planning
# fails, fall back to this: every tier-3 and tier-4 recipe won in simulation;
# tier 5 depends on the recipe and tier 6 (Pincer Potion) won ~25%.
MAX_TIER_PLAYED = 4


def cell_centre(c, k):
    return round(COL_X0 + c * COL_PITCH), round(ROW0_Y[c % 2] - k * ROW_PITCH)


def recipe_centre(i):
    return RECIPE_X, round(RECIPE_Y0 + i * RECIPE_PITCH)


def aim_x(c):
    """Cursor x that holds the pair over columns c, c+1 (live: any x over
    column c does; just right of its centre is safely inside)."""
    return round(COL_X0 + c * COL_PITCH) + 10


@lru_cache(maxsize=4)
def templates(scale):
    """{(colour, tier): image} at board size for a window ``scale``."""
    out = {}
    for path in sorted(INGREDIENTS.glob("*.png")):
        colour, tier = path.stem.rsplit("_", 1)
        img = np.asarray(Image.open(path).convert("RGB"))
        h, w = img.shape[:2]
        img = img[int(h * INNER[0]):int(h * INNER[1]), int(w * INNER[0]):int(w * INNER[1])]
        f = PIECE_SCALE * scale
        out[(colour, int(tier))] = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return out


class PotionTable:
    """Reads the brewing table and moves the pair in hand."""

    def __init__(self, win):
        self.win = win
        self._frame = None
        self.hand_seen_at = None

    # -- capture ----------------------------------------------------------------
    def look(self):
        """Capture the whole table once; reads until the next look use it.
        (Live, a board read grabbed 72+ small regions one by one.)"""
        want = self.win.region_to_screen(*TABLE_REGION)
        self._frame = (want, self.win._grab_rect(want))

    def _grab(self, left, top, width, height):
        """Like win.grab, but cut from the last look() when it covers it."""
        if self._frame is not None:
            want = self.win.region_to_screen(left, top, width, height)
            frame_rect, img = self._frame
            x0, y0 = want.left - frame_rect.left, want.top - frame_rect.top
            if (x0 >= 0 and y0 >= 0 and x0 + want.width <= frame_rect.width
                    and y0 + want.height <= frame_rect.height):
                return img[y0:y0 + want.height, x0:x0 + want.width]
        return self.win.grab(left, top, width, height)

    # -- reading --------------------------------------------------------------
    def _patch(self, x, y):
        tmpl = templates(round(self.win.scale, 3))
        th, tw = next(iter(tmpl.values())).shape[:2]
        s = self.win.scale
        half_w, half_h = tw / s / 2 + SEARCH, th / s / 2 + SEARCH
        patch = np.ascontiguousarray(self._grab(round(x - half_w), round(y - half_h),
                                                round(2 * half_w), round(2 * half_h)))
        if patch.shape[0] < th or patch.shape[1] < tw:
            return tmpl, None
        return tmpl, patch

    def piece_at(self, x, y):
        """(piece, score) for the piece centred near ref (x, y); piece is
        None when nothing there matches well."""
        tmpl, patch = self._patch(x, y)
        if patch is None:
            return None, 0.0
        best, best_score = None, -1.0
        for piece, t in tmpl.items():
            score = float(cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED).max())
            if score > best_score:
                best, best_score = piece, score
        return (best if best_score >= MIN_SCORE else None), best_score

    def is_piece(self, x, y, piece):
        """Quick check: is ``piece`` at ref (x, y)? (one comparison, not 36)"""
        tmpl, patch = self._patch(x, y)
        if patch is None or piece not in tmpl:
            return False
        return float(cv2.matchTemplate(patch, tmpl[piece], cv2.TM_CCOEFF_NORMED).max()) >= MIN_SCORE

    def board_is(self, cols):
        """Quick check of the last look against an expected board: every
        expected piece where it should be and nothing just above each column."""
        for c, col in enumerate(cols):
            for k, piece in enumerate(col):
                if piece == UNKNOWN:
                    if self.piece_at(*cell_centre(c, k))[0] is None:
                        return False
                elif not self.is_piece(*cell_centre(c, k), piece):
                    return False
            if len(col) < BOARD_ROWS and self.piece_at(*cell_centre(c, len(col)))[0] is not None:
                return False
        return True

    def hand_is(self, pair, c):
        """Quick check of the last look: is ``pair`` in hand over c, c+1?"""
        return (self.is_piece(*cell_centre(c, HAND_ROW), pair[0])
                and self.is_piece(*cell_centre(c + 1, HAND_ROW), pair[1]))

    def read_columns(self):
        """Pieces in each column, bottom first.

        Pieces never float, so an unreadable cell below a readable one is a
        piece we failed to recognise: it's kept as UNKNOWN (it blocks, and
        merges with nothing) rather than cutting the column short. Live, a
        column cut short at one bad cell hid two pieces and the bot filled
        it to the top. A column ends after two empty cells in a row.
        """
        self.look()
        cols = []
        for c in range(COLUMNS):
            col, gap = [], []
            for k in range(BOARD_ROWS):
                piece, _ = self.piece_at(*cell_centre(c, k))
                if piece is None:
                    gap.append(k)
                    if len(gap) == 2:
                        break
                    continue
                col.extend([UNKNOWN] * len(gap))
                gap = []
                col.append(piece)
            cols.append(col)
        return cols

    def read_hand(self):
        """(left piece, right piece, left column) of the pair in hand, or None."""
        self.look()
        held = []
        for c in range(COLUMNS):
            piece, _ = self.piece_at(*cell_centre(c, HAND_ROW))
            if piece is not None:
                held.append((c, piece))
        if len(held) == 2 and held[1][0] == held[0][0] + 1:
            return held[0][1], held[1][1], held[0][0]
        return None

    def read_next(self):
        """The "next" pair shown top left, or None."""
        self.look()
        pair = tuple(self.piece_at(*p)[0] for p in NEXT_PAIR)
        return pair if None not in pair else None

    def read_recipe(self):
        """Counter of (colour, tier) the recipe still needs: slots shown in
        the list without a check mark beside them."""
        self.look()
        needs = Counter()
        for i in range(RECIPE_SLOTS):
            piece = self.recipe_piece(i)
            if piece is not None and not self.slot_filled(i):
                needs[piece] += 1
        return needs

    def recipe_piece(self, i):
        """The ingredient in recipe slot ``i``, or None. The icon gives the
        tier; the border gives the colour (unmade icons are grey)."""
        x, y = recipe_centre(i)
        colour = self.border_colour(x, y)
        if colour is None:                  # no slot here, or a gray border
            return self.piece_at(x, y)[0]
        tmpl, patch = self._patch(x, y)
        if patch is None:
            return None
        # Grey icons match less well than board pieces (live: a grey Venom
        # Gem, red 5, scored 0.76, the next tier 0.38), but the border has
        # already said there's an ingredient of this colour here.
        score, piece = max((float(cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED).max()), key)
                           for key, t in tmpl.items() if key[0] == colour)
        return piece if score >= RECIPE_MIN_SCORE else None

    def border_colour(self, x, y):
        """Colour of the hex border around ref (x, y): a BORDER_COLOURS key,
        or None when it can't tell (a gray border, or no slot)."""
        s = self.win.scale
        r1 = BORDER_RING[1]
        img = self._grab(x - r1, y - r1, 2 * r1 + 1, 2 * r1 + 1).astype(int)
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.hypot(xx - w / 2, yy - h / 2) / s
        px = img[(d >= BORDER_RING[0]) & (d <= BORDER_RING[1])]
        px = px[px.max(axis=1) - px.min(axis=1) > 40]
        if len(px) < MIN_BORDER_PIXELS * s * s:
            return None
        med = np.median(px, axis=0)
        return min(BORDER_COLOURS, key=lambda k: float(np.sum((med - BORDER_COLOURS[k]) ** 2)))

    def slot_filled(self, i):
        """A filled recipe slot has a dark check mark beside it (live: 24%
        of that patch is dark when checked, ~6% when not)."""
        _, y = recipe_centre(i)
        patch = self._grab(*CHECK_REGION_X, y - CHECK_HALF_H, CHECK_REGION_W, 2 * CHECK_HALF_H)
        return float((patch.mean(axis=-1) < 120).mean()) >= CHECK_DARK

    def brewed(self):
        """True while the "Congratulations! You have made ..." dialog is up."""
        score, _ = match_marker(self.win.grab(*BREWED_REGION), "potions/congratulations.png",
                                self.win.scale)
        return score >= 0.8

    def maxed_out(self):
        """True while "Potions Maxed Out ... Continue anyway?" is up."""
        score, _ = match_marker(self.win.grab(*MAXED_REGION), "potions/maxed_out.png",
                                self.win.scale)
        return score >= 0.8

    def switch_confirm(self):
        """True while "Switch Recipe: you will lose your progress" is up."""
        score, _ = match_marker(self.win.grab(*MAXED_REGION), "potions/switch_recipe.png",
                                self.win.scale)
        return score >= 0.8

    def recipe_list(self):
        """True while the potion recipe list is showing."""
        score, _ = match_marker(self.win.grab(*LIST_TITLE_REGION), "potions/recipe_list.png",
                                self.win.scale)
        return score >= 0.8

    def game_over(self):
        """True while the "Game Over" (out of space) dialog is up."""
        score, _ = match_marker(self.win.grab(*GAME_OVER_REGION), "potions/game_over.png",
                                self.win.scale)
        return score >= 0.8

    # -- acting ---------------------------------------------------------------
    def hold_over(self, c, pair=None):
        """Move the pair in hand over columns c, c+1; returns the hand once
        it is there, or None. Knowing ``pair`` makes each check quick."""
        x = aim_x(c)
        self.win.move(x, AIM_Y)
        deadline = time.monotonic() + AIM_TIMEOUT
        last_move, nudge = time.monotonic(), 1
        while time.monotonic() < deadline:
            check_stop()
            # Live: right after planning (all cores busy) the game missed the
            # move and the pair stayed put; re-send it, a pixel off, so the
            # game sees fresh movement (same fix as the hammering bot).
            if time.monotonic() - last_move >= AIM_RESEND:
                self.win.move(x + nudge, AIM_Y)
                nudge, last_move = -nudge, time.monotonic()
            if pair is not None:
                self.look()
                for held in (pair, pair[::-1]):
                    if self.hand_is(held, c):
                        return held[0], held[1], c
            else:
                hand = self.read_hand()
                if hand and hand[2] == c:
                    return hand
            wait(0.02)
        return None

    def rotate(self, hand):
        """Swap the two pieces in hand; returns the new hand or None."""
        left, right, c = hand
        self.win.right_click(aim_x(c), AIM_Y)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            self.look()
            if self.hand_is((right, left), c):
                return right, left, c
            wait(0.02)
        return None

    def drop(self, c):
        """Drop the pair. The cursor is already resting over the column (the
        pair followed it there), so a short press is enough; menu buttons
        need the full hover-and-hold press()."""
        x, y = aim_x(c), AIM_Y
        self.win.move(x, y)
        self.win.mouse_down(x, y)
        try:
            time.sleep(DROP_HOLD)
        finally:
            self.win.mouse_up(x, y)

    def wait_for(self, expected_cols, expected_pair, c, done=False):
        """After a drop: wait until ``expected_pair`` is in hand over c and
        the board matches ``expected_cols``, then return (cols, hand) at
        once. Live, the next pair only shows once merges have finished, so
        a matching board is a finished one. Anything unexpected (or no known
        next pair) falls back to wait_settled(). ``done``: this drop should
        finish the potion, so watch for the Congratulations dialog."""
        if expected_pair is None:
            return self.wait_settled()
        deadline = time.monotonic() + TURN_TIMEOUT
        self.hand_seen_at = None
        while time.monotonic() < deadline:
            check_stop()
            self.look()
            now = time.monotonic()
            if self.hand_is(expected_pair, c):
                if self.hand_seen_at is None:
                    self.hand_seen_at = now
                if self.board_is(expected_cols):
                    return expected_cols, (expected_pair[0], expected_pair[1], c)
                if now - self.hand_seen_at > MATCH_WAIT:
                    break                       # not the board we expected
            else:
                waited = now - deadline + TURN_TIMEOUT
                if (done or waited > MATCH_WAIT) and (self.game_over() or self.brewed()):
                    return None, None
                if waited > 2 * MATCH_WAIT and self.read_hand():
                    break                       # a pair, but not the one expected
            wait(0.02)
        return self.wait_settled()

    def wait_settled(self):
        """Wait for a pair in hand and a board that has stopped changing.
        Returns (columns, hand), or (None, None) on timeout or game over."""
        deadline = time.monotonic() + TURN_TIMEOUT
        last, since = None, None
        self.hand_seen_at = None
        while time.monotonic() < deadline:
            check_stop()
            # Live: after "Game Over" the last pair stays drawn in hand, so
            # check the dialogs every time, not only when the hand is gone.
            if self.game_over() or self.brewed():
                return None, None
            hand = self.read_hand()
            if hand and self.hand_seen_at is None:
                self.hand_seen_at = time.monotonic()
            cols = self.read_columns() if hand else None
            now = time.monotonic()
            if hand and cols == last and last is not None:
                if now - since >= STEADY:
                    return cols, hand
            else:
                last, since = cols, now
            wait(0.05)
        return None, None


def describe(cols):
    """Short text of a board: each column bottom first, e.g. 'g1,b2 | - | ...'."""
    return " | ".join(",".join("%s%d" % (c[0], t) for c, t in col) or "-" for col in cols)


class Brewer:
    def __init__(self, win, any_tier=False):
        self.win = win
        self.table = PotionTable(win)
        self.mismatches = 0
        self.result = None
        self.turns = 0
        self.pool = {"red", "green", "blue"}   # colours being dealt
        self.any_tier = any_tier
        self.strategy, self.evaluate = "standard", None
        self.recipe = None                  # the needs this plan is for
        self.plan_pool = None               # colours the current plan assumes
        self.dealt, self.dealt_pairs = set(), 0
        self.pool_checked = False
        self.replan = None                  # background re-plan: {"pool", "result"/"error"}

    def choose_plan(self, needs):
        """Pick a strategy for this recipe from a simulated plan (planner.py)
        and decide whether to play it. False: skip it. If planning fails, fall
        back to a plain tier limit."""
        if not needs:
            return True                      # recipe unreadable: just play
        try:
            import planner
            from brewing import scorer
            self.recipe = Counter(needs)
            self.plan_pool = planner.known_pool(needs) or planner.POOL
            p = planner.plan(needs, self.plan_pool)
            self.strategy = p["strategy"]
            self.evaluate = scorer(self.strategy, needs, self.plan_pool)
        except Exception:
            log.exception("Couldn't plan this recipe; using the tier %d limit", MAX_TIER_PLAYED)
            top = max(k for _, k in needs)
            if top > MAX_TIER_PLAYED and not self.any_tier:
                log.warning("This recipe needs a tier %d ingredient; skipping it (--any-tier "
                            "plays it anyway).", top)
                return False
            return True
        log.info("Plan for %s: %s strategy, simulated %.0f%% wins, median %s turns.",
                 "/".join(self.plan_pool), self.strategy, 100 * p["win"], p["median_turns"])
        if p["win"] < planner.MIN_WIN and not self.any_tier:
            log.warning("Skipping this recipe: simulated, the bot wins only %.0f%% of the time "
                        "(--any-tier plays it anyway).", 100 * p["win"])
            return False
        return True

    def note_dealt(self, pair):
        """Track the colours being dealt. After POOL_CHECK_PAIRS pairs they're
        known; remember them for this recipe and, if the plan assumed other
        colours, re-plan for the real ones in the background."""
        self.dealt.update(c for c, _ in pair if c != UNKNOWN[0])
        self.dealt_pairs += 1
        if self.recipe is None or self.pool_checked or self.dealt_pairs < POOL_CHECK_PAIRS:
            return
        self.pool_checked = True
        import planner
        seen = tuple(sorted(self.dealt))
        planner.remember_pool(self.recipe, seen)
        if set(seen) == set(self.plan_pool):
            return
        log.info("This island deals %s, not the %s planned for; re-planning in the "
                 "background.", "/".join(seen), "/".join(self.plan_pool))
        job = {"pool": seen}

        def run():
            try:
                job["result"] = planner.plan(self.recipe, seen)
            except Exception as e:          # reported when applied
                job["error"] = e
        self.replan = job
        threading.Thread(target=run, name="replan", daemon=True).start()

    def apply_replan(self):
        """Switch to the background re-plan once it's ready."""
        job = self.replan
        if job is None or not ("result" in job or "error" in job):
            return
        self.replan = None
        if "error" in job:
            log.warning("Re-planning failed (%s); keeping the current plan.", job["error"])
            return
        from brewing import scorer
        p = job["result"]
        self.plan_pool = job["pool"]
        self.strategy = p["strategy"]
        self.evaluate = scorer(self.strategy, self.recipe, self.plan_pool)
        log.info("Plan for %s: %s strategy, simulated %.0f%% wins, median %s turns.",
                 "/".join(self.plan_pool), self.strategy, 100 * p["win"], p["median_turns"])

    def play(self):
        """Play until the potion is done or there's no pair to place."""
        t = self.table
        self.win.refresh()
        needs = t.read_recipe()
        log.info("Recipe needs: %s", ", ".join("%d x %s %d" % (n, c, k)
                                               for (c, k), n in sorted(needs.items())) or "?")
        if not needs and not t.read_hand():
            # Live: started mid sea battle, the bot waited, then parked the
            # cursor on the player. With no recipe and no pair, touch nothing.
            log.warning("No potion board on screen; nothing to do.")
            self.result = "no table"
            return
        if not self.choose_plan(needs):
            self.result = "too hard"
            return
        cols, hand = t.wait_settled()
        turns = lost = 0
        while hand:
            check_stop()
            snapshots.take_requested(self.win, "potions")
            t0 = time.monotonic()
            nxt = t.read_next()
            board = Board(cols, needs)
            pair = (hand[0], hand[1])
            # Every recipe deals red, green and blue tier-1 pieces; bigger
            # ones add purple (live, Nov 2026). Weigh the unseen pair after
            # next against every colour seen so far: in simulation this cut
            # tier-4 recipes from ~80 to ~45 turns (median).
            for col in cols:
                self.pool.update(c for c, _ in col if c != UNKNOWN[0])
            self.pool.update(c for c, _ in pair + (nxt or ()))
            self.note_dealt(pair)
            self.apply_replan()
            move = best_move(board, pair, nxt, pool=self.pool, evaluate=self.evaluate)
            t1 = time.monotonic()
            if move is None:
                log.warning("No room for %s; the board is full", pair)
                break
            c, swapped = move
            want = oriented(pair, swapped)
            predicted = board.copy()
            predicted.pieces_merged = predicted.drop(want, c)
            held = t.hold_over(c, pair)
            t2 = time.monotonic()
            if held is None:
                lost += 1
                log.warning("The pair didn't follow the cursor to column %d (%d/%d)",
                            c, lost, AIM_RETRIES)
                snapshots.save(self.win, "hand_lost", "potions")
                if lost >= AIM_RETRIES:
                    break
                cols, hand = t.wait_settled()
                continue
            lost = 0
            if (held[0], held[1]) != want:
                held = t.rotate(held)
                if held is None or (held[0], held[1]) != want:
                    log.warning("Rotate didn't take; re-reading")
                    cols, hand = t.wait_settled()
                    continue
            t3 = time.monotonic()
            log.debug("drop %s on columns %d-%d", want, c, c + 1)
            t.drop(c)
            t4 = time.monotonic()
            turns += 1
            self.turns = turns
            done = not any(n > 0 for n in predicted.needs.values())
            cols, hand = t.wait_for(predicted.cols, nxt, c, done)
            t5 = time.monotonic()
            log.debug("timing: think %.2f aim %.2f rotate %.2f press %.2f hand after %.2f "
                      "settled after %.2f (merges predicted: %s)", t1 - t0, t2 - t1, t3 - t2,
                      t4 - t3, (t.hand_seen_at or t5) - t4, t5 - t4,
                      predicted.pieces_merged if hasattr(predicted, "pieces_merged") else "?")
            if cols is None:
                if t.brewed():
                    log.info("Potion brewed in %d turns.", turns)
                    self.result = "brewed"
                elif t.game_over():
                    log.warning("Game over: the board is full. Back to the recipe list.")
                    snapshots.save(self.win, "game_over", "potions", force=True)
                    self.win.press(*CONTINUE_BUTTON)
                    self.result = "lost"
                break
            if cols != predicted.cols:
                self.mismatches += 1
                log.info("Board differs from the prediction after turn %d (%s on %d-%d):\n"
                         "  before    %s\n  seen      %s\n  predicted %s",
                         turns, want, c, c + 1, describe(board.cols), describe(cols),
                         describe(predicted.cols))
                snapshots.save(self.win, "mismatch", "potions", force=True,
                               info={"before": board.cols, "seen": cols,
                                     "predicted": predicted.cols, "dropped": want,
                                     "column": c, "needs": [[k[0], k[1], n] for k, n
                                                            in board.needs.items() if n > 0]})
            # Read the recipe every turn (~45 ms) rather than trusting the
            # simulation's tally: live, the tally drifted and the bot thought
            # a green 3 slot was filled when the game still wanted one.
            needs = t.read_recipe()
            snapshots.record(self.win, "potions", {"columns": cols, "needs": [
                [c, k, n] for (c, k), n in needs.items() if n > 0]})
        if turns:
            self.win.park_mouse()
        log.info("Stopped after %d turns (%d prediction mismatches).", turns, self.mismatches)
        snapshots.save(self.win, "potion_end", "potions", force=True)


def single_instance():
    """Hold a lock so a second brew bot can't run alongside this one (live:
    a leftover run kept playing and the two fought over the mouse).
    Returns the open lock file, or None if another bot holds it."""
    import msvcrt
    import tempfile
    lock = open(Path(tempfile.gettempdir()) / "tlopo_brew_bot.lock", "a+")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None
    return lock


def brew_loop(win, once=False, any_tier=False, max_brews=0):
    """Brew the open potion, then press "Brew Again" and keep going until
    stopped, a game is lost, ``max_brews`` are made (0: no limit), or the
    table isn't in a playable state."""
    made = 0
    started = time.monotonic()
    win.refresh()
    first = PotionTable(win)
    if first.brewed() or first.maxed_out():   # started on a finished potion's dialog
        if not start_next(win):
            log.warning("Couldn't start a potion from the finished-potion dialog.")
            return
    while True:
        brewer = Brewer(win, any_tier)
        began = time.monotonic()
        brewer.play()
        if brewer.result != "brewed":
            break
        made += 1
        log.info("Brewed %d so far (this one %.0fs, average %.0fs).", made,
                 time.monotonic() - began, (time.monotonic() - started) / made)
        if once:
            break
        if max_brews and made >= max_brews:
            log.info("Brewed %d; stopping (limit).", made)
            break
        if not start_next(win):
            log.warning("The next potion didn't start; stopping.")
            snapshots.save(win, "next_potion_failed", "potions", force=True)
            break
    log.info("Done: %d potion(s) brewed.", made)


def start_next(win):
    """Press "Brew Again" and wait for the next board. When the potion
    inventory is full the game asks "Continue anyway?"; like the old
    potions.py, answer OK so brewing (and rep) carries on."""
    t = PotionTable(win)
    win.press(*BREW_AGAIN_BUTTON)
    win.park_mouse()
    deadline = time.monotonic() + NEXT_POTION_TIMEOUT
    pressed_again = False
    while time.monotonic() < deadline:
        check_stop()
        wait(0.3)
        win.refresh()
        if t.maxed_out():
            log.info("Potion inventory is full; brewing anyway (OK).")
            win.press(*MAXED_OK_BUTTON)
            win.park_mouse()
            continue
        if t.read_hand():
            return True
        if t.brewed() and not pressed_again and time.monotonic() > deadline - NEXT_POTION_TIMEOUT / 2:
            win.press(*BREW_AGAIN_BUTTON)      # the first press may have been lost
            win.park_mouse()
            pressed_again = True
    return False


def recipe_row(name_or_row):
    """List row for a recipe name (case-insensitive) or a row number."""
    text = str(name_or_row).strip()
    if text.isdigit():
        return int(text)
    for i, name in enumerate(RECIPES):
        if name.lower() == text.lower():
            return i
    raise ValueError("unknown recipe %r (known: %s)" % (text, ", ".join(RECIPES)))


def to_recipe_list(win, timeout=10.0):
    """Get from wherever the table is (a board, Congratulations, Game Over,
    the switch question) to the recipe list. Returns True once it shows."""
    t = PotionTable(win)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_stop()
        win.refresh()
        if t.recipe_list():
            return True
        if t.brewed():
            win.press(*CONGRATS_CONTINUE_BUTTON)
        elif t.game_over():
            win.press(*CONTINUE_BUTTON)
        elif t.switch_confirm():
            win.press(*SWITCH_OK_BUTTON)
        elif t.maxed_out():
            win.press(*MAXED_OK_BUTTON)
        elif t.read_hand():
            win.press(*SWITCH_RECIPE_BUTTON)
        win.park_mouse()
        wait(0.6)
    return False


def open_recipe(win, row, timeout=10.0):
    """Open recipe ``row`` from the list; True once its board is ready."""
    if not to_recipe_list(win):
        log.warning("Couldn't get to the recipe list")
        return False
    t = PotionTable(win)
    win.press(LIST_X, round(LIST_Y0 + row * LIST_PITCH))
    win.park_mouse()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_stop()
        wait(0.3)
        win.refresh()
        if t.maxed_out():
            log.info("Potion inventory is full; brewing anyway (OK).")
            win.press(*MAXED_OK_BUTTON)
            win.park_mouse()
            continue
        if t.read_hand():
            return True
    log.warning("Recipe row %d didn't open (locked, or the list moved?)", row)
    snapshots.save(win, "recipe_not_opened", "potions", force=True, info={"row": row})
    return False


def brew_recipes(win, recipes, count=1, any_tier=False):
    """Brew each of ``recipes`` (names or list rows) ``count`` times."""
    summary = []
    for recipe in recipes:
        row = recipe_row(recipe)
        name = RECIPES[row] if row < len(RECIPES) else "row %d" % row
        for i in range(count):
            if i == 0:
                opened = open_recipe(win, row)
            else:
                opened = start_next(win)
            if not opened:
                summary.append((name, "didn't open", 0, 0))
                break
            brewer = Brewer(win, any_tier)
            began = time.monotonic()
            brewer.play()
            took = time.monotonic() - began
            summary.append((name, brewer.result or "stopped", round(took), brewer.turns))
            log.info("%s: %s in %.0fs (%d turns, %d mismatches)", name,
                     brewer.result or "stopped", took, brewer.turns, brewer.mismatches)
            if brewer.result != "brewed":
                break
    lines = ["  %-26s %-12s %4ss %3s turns" % row for row in summary]
    log.info("Summary:%s", "".join("\n" + line for line in lines))
    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Play the potion-brewing table.")
    parser.add_argument("--once", action="store_true", help="stop after one potion")
    parser.add_argument("--recipes", help="comma-separated recipe names or list rows to "
                        "brew in turn, e.g. \"Deadeye I, Boom Brew I\" or 7,8")
    parser.add_argument("--count", type=int, default=1, help="potions of each recipe (with --recipes)")
    parser.add_argument("--any-tier", action="store_true",
                        help="also play recipes the plan expects to lose often")
    parser.add_argument("--record", action="store_true", help="save frames to debug_frames/")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--max-brews", type=int, default=0,
                        help="stop after this many potions (0: no limit; not with --recipes)")
    parser.add_argument("--max-minutes", type=float, default=0,
                        help="stop after this many minutes (0: no limit)")
    args = parser.parse_args()
    snapshots.recording = args.record
    recipes = [r for r in (args.recipes or "").split(",") if r.strip()]
    for r in recipes:
        recipe_row(r)                    # fail fast on a typo
    lock = single_instance()
    if lock is None:
        print("Another brew bot is already running; stop it first.")
        sys.exit(1)
    with live_session(hotkeys=session_hotkeys(), debug=args.debug,
                      max_minutes=args.max_minutes):
        if recipes:
            brew_recipes(GameWindow(), recipes, args.count, args.any_tier)
        else:
            brew_loop(GameWindow(), once=args.once, any_tier=args.any_tier,
                      max_brews=args.max_brews)


if __name__ == "__main__":
    main()
