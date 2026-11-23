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

    # -- reading --------------------------------------------------------------
    def piece_at(self, x, y):
        """(piece, score) for the piece centred near ref (x, y); piece is
        None when nothing there matches well."""
        tmpl = templates(round(self.win.scale, 3))
        th, tw = next(iter(tmpl.values())).shape[:2]
        s = self.win.scale
        half_w, half_h = tw / s / 2 + SEARCH, th / s / 2 + SEARCH
        patch = np.ascontiguousarray(self.win.grab(round(x - half_w), round(y - half_h),
                                                   round(2 * half_w), round(2 * half_h)))
        if patch.shape[0] < th or patch.shape[1] < tw:
            return None, 0.0
        best, best_score = None, -1.0
        for piece, t in tmpl.items():
            score = float(cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED).max())
            if score > best_score:
                best, best_score = piece, score
        return (best if best_score >= MIN_SCORE else None), best_score

    def read_columns(self):
        """Pieces in each column, bottom first.

        Pieces never float, so an unreadable cell below a readable one is a
        piece we failed to recognise: it's kept as UNKNOWN (it blocks, and
        merges with nothing) rather than cutting the column short. Live, a
        column cut short at one bad cell hid two pieces and the bot filled
        it to the top. A column ends after two empty cells in a row.
        """
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
        held = []
        for c in range(COLUMNS):
            piece, _ = self.piece_at(*cell_centre(c, HAND_ROW))
            if piece is not None:
                held.append((c, piece))
        if len(held) == 2 and held[1][0] == held[0][0] + 1:
            return held[0][1], held[1][1], held[0][0]
        return None

    def read_next(self):
        pair = tuple(self.piece_at(*p)[0] for p in NEXT_PAIR)
        return pair if None not in pair else None

    def read_recipe(self):
        """Counter of (colour, tier) the recipe still needs: slots shown in
        the list without a check mark beside them."""
        needs = Counter()
        for i in range(RECIPE_SLOTS):
            piece, _ = self.piece_at(*recipe_centre(i))
            if piece is not None and not self.slot_filled(i):
                needs[piece] += 1
        return needs

    def slot_filled(self, i):
        """A filled recipe slot has a dark check mark beside it (live: 24%
        of that patch is dark when checked, ~6% when not)."""
        _, y = recipe_centre(i)
        patch = self.win.grab(*CHECK_REGION_X, y - CHECK_HALF_H, CHECK_REGION_W, 2 * CHECK_HALF_H)
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
    def hold_over(self, c):
        """Move the pair in hand over columns c, c+1; returns the hand once
        it is there, or None."""
        self.win.move(aim_x(c), AIM_Y)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            check_stop()
            hand = self.read_hand()
            if hand and hand[2] == c:
                return hand
            wait(0.05)
        return None

    def rotate(self, hand):
        """Swap the two pieces in hand; returns the new hand or None."""
        left, right, c = hand
        self.win.right_click(aim_x(c), AIM_Y)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            new = self.read_hand()
            if new and (new[0], new[1]) == (right, left):
                return new
            wait(0.05)
        return None

    def drop(self, c):
        self.win.press(aim_x(c), AIM_Y)

    def wait_settled(self):
        """Wait for a pair in hand and a board that has stopped changing.
        Returns (columns, hand), or (None, None) on timeout or game over."""
        deadline = time.monotonic() + TURN_TIMEOUT
        last, since = None, None
        while time.monotonic() < deadline:
            check_stop()
            # Live: after "Game Over" the last pair stays drawn in hand, so
            # check the dialogs every time, not only when the hand is gone.
            if self.game_over() or self.brewed():
                return None, None
            hand = self.read_hand()
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
    def __init__(self, win):
        self.win = win
        self.table = PotionTable(win)
        self.mismatches = 0
        self.result = None
        self.turns = 0

    def play(self):
        """Play until the potion is done or there's no pair to place."""
        t = self.table
        self.win.refresh()
        needs = t.read_recipe()
        log.info("Recipe needs: %s", ", ".join("%d x %s %d" % (n, c, k)
                                               for (c, k), n in sorted(needs.items())) or "?")
        cols, hand = t.wait_settled()
        turns = 0
        while hand:
            check_stop()
            snapshots.take_requested(self.win, "potions")
            nxt = t.read_next()
            board = Board(cols, needs)
            pair = (hand[0], hand[1])
            move = best_move(board, pair, nxt)
            if move is None:
                log.warning("No room for %s; the board is full", pair)
                break
            c, swapped = move
            want = oriented(pair, swapped)
            predicted = board.copy()
            predicted.drop(want, c)
            held = t.hold_over(c)
            if held is None:
                log.warning("The pair didn't follow the cursor to column %d", c)
                snapshots.save(self.win, "hand_lost", "potions")
                break
            if (held[0], held[1]) != want:
                held = t.rotate(held)
                if held is None or (held[0], held[1]) != want:
                    log.warning("Rotate didn't take; re-reading")
                    cols, hand = t.wait_settled()
                    continue
            log.debug("drop %s on columns %d-%d", want, c, c + 1)
            t.drop(c)
            turns += 1
            self.turns = turns
            cols, hand = t.wait_settled()
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


def brew_loop(win, once=False):
    """Brew the open potion, then press "Brew Again" and keep going until
    stopped, a game is lost, or the table isn't in a playable state."""
    made = 0
    started = time.monotonic()
    win.refresh()
    first = PotionTable(win)
    if first.brewed() or first.maxed_out():   # started on a finished potion's dialog
        if not start_next(win):
            log.warning("Couldn't start a potion from the finished-potion dialog.")
            return
    while True:
        brewer = Brewer(win)
        began = time.monotonic()
        brewer.play()
        if brewer.result != "brewed":
            break
        made += 1
        log.info("Brewed %d so far (this one %.0fs, average %.0fs).", made,
                 time.monotonic() - began, (time.monotonic() - started) / made)
        if once:
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


def brew_recipes(win, recipes, count=1):
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
            brewer = Brewer(win)
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
    parser.add_argument("--record", action="store_true", help="save frames to debug_frames/")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()
    snapshots.recording = args.record
    recipes = [r for r in (args.recipes or "").split(",") if r.strip()]
    for r in recipes:
        recipe_row(r)                    # fail fast on a typo
    lock = single_instance()
    if lock is None:
        print("Another brew bot is already running; stop it first.")
        sys.exit(1)
    with live_session(hotkeys=session_hotkeys(), debug=args.debug):
        if recipes:
            brew_recipes(GameWindow(), recipes, args.count)
        else:
            brew_loop(GameWindow(), once=args.once)


if __name__ == "__main__":
    main()
