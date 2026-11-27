"""Play Texas Hold'em at a TLOPO poker table until stopped.

Sit down at a Hold'em table, then:
    python poker/poker_bot.py            (--max-hands N, --record, --debug)
Press ` (backtick) to stop; F9 saves a debug frame.

The bot only acts on its own turn, which is when the action buttons show at
the bottom. It reads its two cards and the board, estimates its chance of
winning against the players still holding cards (by dealing the unseen
cards at random many times), and then:

- bets or raises with a strong hand,
- checks or calls with a fair one,
- checks when that's free, and folds otherwise.

It never presses "Swap 1st/2nd Card": swapping is cheating. Every decision
goes to logs/poker-YYYYMMDD.jsonl.

Measured from a recorded session (Nov 2026, 2560x1369 game area;
debug_frames/poker_research/20261127-111236): Tortuga Hold'em, NPC players.
"""
import functools
import json
import logging
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "repair"))  # shared screen code

import snapshots  # noqa: E402
from repair_mini_game import session_hotkeys  # noqa: E402
from screen import (MARKERS, REPO_ROOT, GameWindow, WindowNotFound,  # noqa: E402
                    live_session, match_marker, wait)

log = logging.getLogger("repair")

# -- where things are (reference coordinates) ----------------------------------------
# The board's five cards fill fixed slots from the left; your two cards sit
# below them, the first tucked behind the second with only its left edge
# (rank and suit) showing. (x, y) is a card's top-left corner.
BOARD_SLOTS = [(round(1026 + 102.75 * k), 615) for k in range(5)]
HOLE_SLOTS = [(1210, 847), (1233, 847)]
CORNER = (-4, -4, 26, 50)        # around a card's corner, from its top-left: x, y, w, h
RANK_ROWS, SUIT_ROWS = (0, 30), (22, 50)   # of the corner grab, where each glyph is
CARD_MATCH = 0.6                 # below this a glyph isn't one we know
CARD_WHITE = 0.12                # share of a corner that's card-white (cards 0.26+, felt 0)
RED_PRINT = 13                   # mean R-G of the print above which it's red
# Face-down cards in front of the other seats (pink backs, 95x106).
SEATS = [(964, 429), (1484, 429), (916, 833), (1549, 833)]
SEAT_SIZE = (95, 106)
SEAT_BACK_SHARE = 0.25
# The action buttons (only up on your turn). Swap 1st / Swap 2nd Card sit
# left of these and are never pressed.
FOLD_REGION = (1450, 1258, 150, 45)
CALL_REGION = (1285, 1193, 150, 40)       # "Check" or "Call N"
RAISE_REGION = (1450, 1193, 160, 40)      # "Bet N" or "Raise to N"
CALL_BUTTON = (1361, 1214)
RAISE_BUTTON = (1525, 1214)
FOLD_BUTTON = (1525, 1289)
BUTTON_MATCH = 0.8

# -- play ----------------------------------------------------------------------------
SIMULATIONS = 800
# Bet or raise once the chance of winning is this far from a fair share
# (1 in the number of players) towards certainty: fair + EDGE * (1 - fair).
# Heads-up after the flop: bet at 61%, raise a bet at 68%; against three:
# 44% / 51%; before the flop against three, 40% (A-K, A-Q suited, J-J up).
# Live (Nov 27, ~100 hands at 80/160): with a flat 80% heads-up bar it
# checked three fives and trip sixes, and only called pre-flop with A-K.
BET_EDGE = 0.22         # nobody has bet yet (Check is up): bet
RAISE_EDGE = 0.35       # facing a bet: raise
PREFLOP_EDGE = 0.2      # before the flop, either
CALL_SHARE = 1.0        # call when it's at least a fair share
TURN_EVERY = 0.25       # s between looks while waiting for our turn
ACT_WAIT = 4.0          # s for the buttons to go away after a click
IDLE_LIMIT = 600        # s without a turn before giving up (the table's gone?)
LOG_DIR = REPO_ROOT / "logs"

RANKS = "23456789TJQKA"
SUITS = "cdhs"


# -- hand strength -------------------------------------------------------------------
def _straight_high(ranks):
    """Highest card of a straight among a set of rank values, or 0."""
    if 14 in ranks:
        ranks = ranks | {1}
    for high in range(14, 4, -1):
        if all(r in ranks for r in range(high - 4, high + 1)):
            return high
    return 0


def hand_value(cards):
    """Comparable value of the best five-card hand among ``cards`` (each a
    (rank 2-14, suit) pair); higher tuples win."""
    ranks = sorted((r for r, _ in cards), reverse=True)
    by_suit = {}
    for r, s in cards:
        by_suit.setdefault(s, []).append(r)
    flush = next((sorted(rs, reverse=True) for rs in by_suit.values() if len(rs) >= 5), None)
    if flush:
        high = _straight_high(set(flush))
        if high:
            return (8, high)
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda rc: (rc[1], rc[0]), reverse=True)
    if groups[0][1] == 4:
        quad = groups[0][0]
        return (7, quad, max(r for r in ranks if r != quad))
    if groups[0][1] == 3 and len(groups) > 1 and groups[1][1] >= 2:
        return (6, groups[0][0], groups[1][0])
    if flush:
        return (5,) + tuple(flush[:5])
    high = _straight_high(set(ranks))
    if high:
        return (4, high)
    if groups[0][1] == 3:
        trip = groups[0][0]
        return (3, trip) + tuple([r for r in ranks if r != trip][:2])
    if groups[0][1] == 2 and len(groups) > 1 and groups[1][1] == 2:
        hi, lo = groups[0][0], groups[1][0]
        return (2, hi, lo, max(r for r in ranks if r not in (hi, lo)))
    if groups[0][1] == 2:
        pair = groups[0][0]
        return (1, pair) + tuple([r for r in ranks if r != pair][:3])
    return (0,) + tuple(ranks[:5])


def parse(card):
    """'Ah' -> (14, 'h')."""
    return RANKS.index(card[0]) + 2, card[1]


def win_chance(hole, board, opponents, simulations=SIMULATIONS, rng=random):
    """Share of random deals of the unseen cards that ``hole`` wins (a tie
    counts as a share of the pot) against ``opponents`` random hands."""
    opponents = max(1, opponents)
    known = [parse(c) for c in hole + board]
    deck = [(r, s) for r in range(2, 15) for s in SUITS if (r, s) not in known]
    mine = [parse(c) for c in hole]
    shown = [parse(c) for c in board]
    need = 5 - len(shown)
    total = 0.0
    for _ in range(simulations):
        draw = rng.sample(deck, need + 2 * opponents)
        full = shown + draw[:need]
        me = hand_value(mine + full)
        best, ties = True, 1
        for k in range(opponents):
            them = hand_value(draw[need + 2 * k:need + 2 * k + 2] + full)
            if them > me:
                best = False
                break
            if them == me:
                ties += 1
        if best:
            total += 1.0 / ties
    return total / simulations


def raise_bar(opponents, can_check, preflop):
    """The chance of winning at which to bet or raise."""
    fair = 1.0 / (max(1, opponents) + 1)
    edge = PREFLOP_EDGE if preflop else BET_EDGE if can_check else RAISE_EDGE
    return fair + edge * (1 - fair)


def decide(chance, opponents, can_check, can_raise, preflop=False):
    """'raise' (bet or raise), 'call' (check or call), 'check' or 'fold'."""
    fair = 1.0 / (max(1, opponents) + 1)
    if can_raise and chance >= raise_bar(opponents, can_check, preflop):
        return "raise"
    if chance >= CALL_SHARE * fair:
        return "call"
    return "check" if can_check else "fold"


# -- reading the table ---------------------------------------------------------------
def ink(img):
    """Dark or red print on a card, as a bright image (card paper is dark)."""
    return (255 - img.min(axis=2)).astype(np.uint8)


@functools.lru_cache(maxsize=None)
def glyphs(kind):
    """The rank or suit templates we have: {'A': image, ...}."""
    found = {}
    for path in sorted(MARKERS.glob("poker/%s_*.png" % kind)):
        found[path.stem.split("_", 1)[1]] = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return found


def _best(img, templates, scale):
    best, score = None, -1.0
    for name, tpl in templates.items():
        if abs(scale - 1.0) > 1e-3:
            tpl = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if img.shape[0] < tpl.shape[0] or img.shape[1] < tpl.shape[1]:
            continue
        s = float(cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED).max())
        if s > score:
            best, score = name, s
    return best, score


def is_red(img):
    """Is a card corner's print red (hearts, diamonds) rather than black?
    Recorded: black print R-G 5-8, red 18-33."""
    px = img.astype(np.int16)
    printed = ink(img) > 110
    if not printed.any():
        return False
    return float((px[..., 0] - px[..., 1])[printed].mean()) > RED_PRINT


def read_corner(img, scale=1.0):
    """A card from the RGB grab of its corner: 'Ah', None (no card there)
    or '?' (a card whose rank or suit we have no template for).

    The suit's colour comes first, then its shape decides between the two
    suits of that colour (at this size a club and a spade, or a heart and
    a diamond, look much alike otherwise)."""
    if (img.min(axis=2) > 170).mean() < CARD_WHITE:
        return None
    pic = ink(img)
    top = pic[int(RANK_ROWS[0] * scale):int(RANK_ROWS[1] * scale)]
    bottom = pic[int(SUIT_ROWS[0] * scale):int(SUIT_ROWS[1] * scale)]
    rank, rs = _best(top, glyphs("rank"), scale)
    colour = "hd" if is_red(img) else "cs"
    suit, ss = _best(bottom, {k: v for k, v in glyphs("suit").items() if k[0] in colour}, scale)
    if rank is None or suit is None or rs < CARD_MATCH or ss < CARD_MATCH:
        return "?"
    return rank[0] + suit[0]          # templates may have variants: rank_8b, suit_c2


def card_back_share(img):
    """Share of a seat's area covered by the pink card-back pattern."""
    px = img.astype(np.int16)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    return float(((r > 150) & (r - g > 25) & (r - b > 15) & (g > 90)).mean())


class Table:
    """Reads the poker table from a GameWindow (live or a screenshot)."""

    def __init__(self, win):
        self.win = win

    def _button(self, region, name):
        score, _ = match_marker(self.win.grab(*region), "poker/%s.png" % name, self.win.scale)
        return score >= BUTTON_MATCH

    def buttons(self):
        """None when it isn't our turn, else what the two middle buttons
        offer: {'check': bool, 'raise': bool} (check: "Check", not "Call
        N"; raise: a "Bet N" / "Raise to N" button is there)."""
        if not self._button(FOLD_REGION, "fold"):
            return None
        return {"check": self._button(CALL_REGION, "check"),
                "raise": self._button(RAISE_REGION, "bet") or self._button(RAISE_REGION, "raise")}

    def card(self, slot):
        x, y = slot
        dx, dy, w, h = CORNER
        return read_corner(self.win.grab(x + dx, y + dy, w, h), self.win.scale)

    def hole(self):
        return [self.card(s) for s in HOLE_SLOTS]

    def board(self):
        cards = []
        for slot in BOARD_SLOTS:
            c = self.card(slot)
            if c is None:
                break
            cards.append(c)
        return cards

    def opponents(self):
        """Players still holding cards."""
        w, h = SEAT_SIZE
        return sum(card_back_share(self.win.grab(x, y, w, h)) >= SEAT_BACK_SHARE
                   for x, y in SEATS)


# -- playing -------------------------------------------------------------------------
def log_decision(entry, folder=None):
    folder = Path(folder or LOG_DIR)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / time.strftime("poker-%Y%m%d.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.debug("couldn't log the decision: %s", e)


class Player:
    def __init__(self, win):
        self.win = win
        self.table = Table(win)
        self.hands = 0
        self.last_hole = None
        self.last_street = None        # (hole, board size) of our last turn

    def act(self, buttons):
        """Play one turn; returns the action taken."""
        hole, board = self.table.hole(), self.table.board()
        opponents = self.table.opponents()
        if hole != self.last_hole:
            self.last_hole = hole
            self.hands += 1
            log.info("Hand %d: %s. Played %d hands so far.", self.hands,
                     " ".join(map(str, hole)), self.hands)
        if None in hole or "?" in hole or "?" in board:
            # A card we can't read (no template yet): don't gamble on it.
            chance = None
            action = "check" if buttons["check"] else "fold"
            snapshots.save(self.win, "unknown_card", "poker", force=True)
        else:
            chance = win_chance(hole, board, opponents)
            action = decide(chance, opponents, buttons["check"], buttons["raise"],
                            preflop=not board)
        # Our turn again on the same street means someone raised us back.
        # That player is usually strong, which the chance against random
        # hands can't see (live: three nines re-raised the river into a
        # full house), so from here on only call.
        street = (tuple(hole), len(board))
        raised_back = street == self.last_street and not buttons["check"]
        self.last_street = street
        if raised_back and action == "raise":
            action = "call"
        log.info("%s | board %s | %d against | %s -> %s%s", " ".join(map(str, hole)),
                 " ".join(board) or "-", opponents,
                 "?" if chance is None else "%.0f%%" % (100 * chance), action,
                 " (raised back)" if raised_back else "")
        log_decision({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "hole": hole, "board": board,
                      "opponents": opponents, "chance": chance, "check": buttons["check"],
                      "can_raise": buttons["raise"], "raised_back": raised_back,
                      "action": action})
        point = {"raise": RAISE_BUTTON, "call": CALL_BUTTON, "check": CALL_BUTTON,
                 "fold": FOLD_BUTTON}[action]
        self.win.click(*point)
        return action

    def wait_turn_over(self):
        deadline = time.monotonic() + ACT_WAIT
        while time.monotonic() < deadline:
            wait(0.2)
            if self.table.buttons() is None:
                return True
        return False


def play_loop(win, max_hands=0):
    player = Player(win)
    idle_since = time.monotonic()
    hidden = False
    while True:
        try:
            win.refresh()                  # where the game is (and whether it's there)
            buttons = player.table.buttons()
            if buttons is None:
                if time.monotonic() - idle_since > IDLE_LIMIT:
                    log.warning("No turn for %d minutes; is the table still up? Stopping.",
                                IDLE_LIMIT // 60)
                    snapshots.save(win, "no_turn", "poker", force=True)
                    break
                wait(TURN_EVERY)
                continue
            if (max_hands and player.hands >= max_hands
                    and player.table.hole() != player.last_hole):
                log.info("Played %d hands; stopping (limit).", player.hands)
                break
            player.act(buttons)
            if not player.wait_turn_over():
                log.debug("the buttons are still up after the click")
            idle_since = time.monotonic()
        except WindowNotFound as e:
            if not hidden:
                log.warning("%s; waiting for it to come back.", e)
                hidden = True
            wait(2.0)
            continue
        if hidden:
            log.info("The game is back; carrying on.")
            hidden = False
    log.info("Done: %d hands played.", player.hands)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Play Texas Hold'em at a poker table.")
    parser.add_argument("--max-hands", type=int, default=0,
                        help="stop after this many (0: no limit)")
    parser.add_argument("--max-minutes", type=float, default=0,
                        help="stop after this many minutes (0: no limit)")
    parser.add_argument("--record", action="store_true", help="save frames to debug_frames/")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()
    snapshots.recording = args.record
    with live_session(hotkeys=session_hotkeys(), debug=args.debug, max_minutes=args.max_minutes):
        play_loop(GameWindow(), max_hands=args.max_hands)


if __name__ == "__main__":
    main()
