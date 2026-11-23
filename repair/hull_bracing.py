"""Hull bracing: a 4x4 sliding-block puzzle ("form two straight lines").

Tiles are wooden blocks carrying pieces of coloured lines: a horizontal red
or blue stripe, a vertical green stripe, or both (a cross). Tile codes:

    R  red horizontal      B  blue horizontal      G  green vertical
    X  red + green cross   Y  blue + green cross   O  plain block
    " " empty slot         ?  unreadable

Every line colour with four pieces on the board must form a full line:
red and blue a full row, green a full column. Boards seen live: blue + red
(two rows) and red + green (a row and a column crossing at the X tile).

A tile is read from two patches on its horizontal stripe line, left and
right of the middle (missing the vertical stripe and the dark middle of a
cross), plus two on its vertical centre line, above and below the
horizontal stripe. Calibrated on tile samples from the game, kept in
tests/fixtures/bracing/.
"""
import heapq
import itertools
import logging
import time

import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

log = logging.getLogger("repair")

SIZE = 4
# Grid geometry measured from tile edges on the live game (Nov 2026).
GRID_LEFT, GRID_TOP = 1086, 515      # ref top-left of the first tile
TILE_W, TILE_H = 87, 84
PITCH_X, PITCH_Y = 102.67, 102.5     # tile-to-tile spacing
TILE_SAMPLE_W, TILE_SAMPLE_H = 65, 63   # ref px read around each tile centre
BAND_X = (0.22, 0.78)                   # where along the stripe to sample
BAND_Y = 0.43                           # stripes run ~5 px above the tile centre
VERTICAL_Y = (0.15, 0.85)               # vertical stripe samples, clear of the band
SOLVER_WEIGHT = 2.0                     # >1 trades shortest moves for speed
STOP_CHECK_EVERY = 1000                 # solver checks the stop key this often

# Which tile codes carry which line, and which way that line must run.
LINES = (("red", "RX", "row"), ("blue", "BY", "row"), ("green", "GXY", "col"))
MAX_EXPANSIONS = 200_000                # solver gives up after this many states
UNREADABLE_RETRIES = 5                  # re-reads before solving around "?" tiles
# Live: instant drags, 35 px steps with no pauses and 20 px steps 5 ms
# apart all dropped slides (a second plan was needed); 15 px steps 8 ms
# apart with a 50 ms settle landed every slide. Bracing took ~6 s either
# way, so keep the reliable setting.
SLIDE_STEP = 15                         # ref px between cursor positions
SLIDE_DELAY = 0.008                     # seconds between those positions
SLIDE_SETTLE = 0.05                     # seconds after each slide
READ_SETTLE = 0.4                       # seconds before reading the board
# The first slide of a plan is checked (see slide_checked).
MOVE_CHECK_TIME = 0.3                   # seconds to wait for the slot to empty
FIRST_MOVE_RETRIES = 3                  # extra tries if the first slide was ignored
FIRST_MOVE_RETRY_WAIT = 0.15            # seconds between tries


def tile_center(row, col):
    return Point(round(GRID_LEFT + col * PITCH_X + TILE_W / 2),
                 round(GRID_TOP + row * PITCH_Y + TILE_H / 2))


def classify_colour(rgb):
    """Class of one sampled colour; "?" if it matches nothing known.

    Measured on the game's tiles: blue stripe ~(1, 2, 50), red stripe
    ~(95, 1, 0), wood ~(182, 136, 66); empty slots are near black.
    """
    r, g, b = (int(v) for v in rgb)
    if b > 30 and r < 30 and g < 30:
        return "B"
    if r > 60 and g < 35 and b < 35:
        return "R"
    if max(r, g, b) < 30:
        return " "
    if r > 120 and g > 90:
        return "O"
    return "?"


def is_green(rgb):
    """Vertical green stripe, measured ~(2, 76-96, 1)."""
    r, g, b = (int(v) for v in rgb)
    return g > 45 and r < 30 and b < 30


def _sample(tile, fx, fy):
    h, w = tile.shape[:2]
    half = max(1, round(w * 0.05))
    x, y = int(w * fx), int(h * fy)
    patch = tile[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1]
    return np.median(patch.reshape(-1, 3), axis=0)


def classify_tile(tile):
    """Tile code of one tile image (RGB array of roughly one tile)."""
    left, right = (classify_colour(_sample(tile, fx, BAND_Y)) for fx in BAND_X)
    if left != right:
        return "?"
    top, bottom = (is_green(_sample(tile, 0.5, fy)) for fy in VERTICAL_Y)
    if top != bottom:
        return "?"
    green = top
    if left == " ":
        return "?" if green else " "
    if left == "R":
        return "X" if green else "R"
    if left == "B":
        return "Y" if green else "B"
    if left == "O":
        return "G" if green else "O"
    return "?"


# -- solver ----------------------------------------------------------------
def required_lines(state):
    """(carrier codes, "row"/"col") for every line colour with 4 pieces."""
    return [(carriers, axis) for _, carriers, axis in LINES
            if sum(state.count(c) for c in carriers) >= SIZE]


def _lines(state, axis):
    if axis == "row":
        return [state[i * SIZE:(i + 1) * SIZE] for i in range(SIZE)]
    return [state[c::SIZE] for c in range(SIZE)]


def is_goal(state, required=None):
    required = required_lines(state) if required is None else required
    return bool(required) and all(
        any(all(ch in carriers for ch in line) for line in _lines(state, axis))
        for carriers, axis in required)


def heuristic(state, required):
    """Estimated slides left: for each colour's best line, the pieces it is
    missing plus how far the nearest outside pieces must travel to it."""
    total = 0
    for carriers, axis in required:
        pieces = [divmod(i, SIZE) for i, ch in enumerate(state) if ch in carriers]
        best = None
        for k, line in enumerate(_lines(state, axis)):
            missing = sum(ch not in carriers for ch in line)
            away = sorted(abs((r if axis == "row" else c) - k) for r, c in pieces
                          if (r if axis == "row" else c) != k)
            cost = missing + sum(away[:missing])
            best = cost if best is None else min(best, cost)
        total += best
    return total


def neighbours(state):
    """(next_state, (from_index, to_index)) for every legal slide."""
    cells = list(state)
    for empty in (i for i, c in enumerate(cells) if c == " "):
        row, col = divmod(empty, SIZE)
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            r, c = row + dr, col + dc
            if 0 <= r < SIZE and 0 <= c < SIZE:
                src = r * SIZE + c
                if cells[src] != " ":
                    nxt = cells.copy()
                    nxt[empty], nxt[src] = nxt[src], nxt[empty]
                    yield "".join(nxt), (src, empty)


def solve(grid, max_expansions=MAX_EXPANSIONS):
    """Moves (from_index, to_index) that reach the goal, or None.

    ``grid`` is a list of rows of single-character tiles. Best-first search
    with a closed set; [] means the grid is already solved.
    """
    start = "".join("".join(row) for row in grid)
    required = required_lines(start)
    if len(required) != 2:
        return None  # the game always asks for two lines: misread board
    tie = itertools.count()
    frontier = [(SOLVER_WEIGHT * heuristic(start, required), next(tie), 0, start, ())]
    best_cost = {start: 0}
    expansions = 0
    while frontier and expansions < max_expansions:
        _, _, cost, state, moves = heapq.heappop(frontier)
        if is_goal(state, required):
            return list(moves)
        if cost > best_cost.get(state, cost):
            continue
        expansions += 1
        if expansions % STOP_CHECK_EVERY == 0:
            check_stop()  # a long search can take seconds; stay stoppable
        for nxt, move in neighbours(state):
            if cost + 1 < best_cost.get(nxt, float("inf")):
                best_cost[nxt] = cost + 1
                heapq.heappush(frontier, (cost + 1 + SOLVER_WEIGHT * heuristic(nxt, required),
                                          next(tie), cost + 1, nxt, moves + (move,)))
    return None


class HullBracing(RepairMiniGame):
    name = "hull_bracing"
    title_marker = "hull_bracing/hull_bracing.PNG"
    button = BUTTONS[2]

    def read_grid(self):
        grid = []
        for row in range(SIZE):
            cells = []
            for col in range(SIZE):
                c = tile_center(row, col)
                tile = self.win.grab(c.x - TILE_SAMPLE_W // 2, c.y - TILE_SAMPLE_H // 2,
                                     TILE_SAMPLE_W, TILE_SAMPLE_H)
                cells.append(classify_tile(tile))
            grid.append(cells)
        return grid

    def tile_code(self, index):
        c = tile_center(*divmod(index, SIZE))
        return classify_tile(self.win.grab(c.x - TILE_SAMPLE_W // 2, c.y - TILE_SAMPLE_H // 2,
                                           TILE_SAMPLE_W, TILE_SAMPLE_H))

    def slide(self, src, dst):
        start = tile_center(*divmod(src, SIZE))
        end = tile_center(*divmod(dst, SIZE))
        self.win.drag([start, end], step=SLIDE_STEP, delay=SLIDE_DELAY)  # always releases
        wait(SLIDE_SETTLE)           # let the tile finish sliding

    def slide_checked(self, src, dst):
        """Slide, then make sure it happened: the slot left behind must turn
        empty. Live: right after the "Brace!" banner the first drag can be
        ignored, and every later move then works on the wrong board.
        Returns True once the tile has moved."""
        for attempt in range(1 + FIRST_MOVE_RETRIES):
            if not self.still_open():
                return False
            self.slide(src, dst)
            deadline = time.monotonic() + MOVE_CHECK_TIME
            while True:
                if self.tile_code(src) == " ":
                    return True
                if time.monotonic() > deadline:
                    break
                wait(0.02)
            log.info("Slide %d->%d didn't take (attempt %d); trying again",
                     src, dst, attempt + 1)
            self.win.park_mouse()
            wait(FIRST_MOVE_RETRY_WAIT)
        return False

    def step(self):
        self.win.park_mouse()
        wait(READ_SETTLE)  # let the last slide finish before reading
        grid = self.read_grid()
        rows = ["".join(row) for row in grid]
        self.info = {"grid": rows}
        log.info("Grid:\n%s", "\n".join("|%s|" % row for row in rows))
        if any("?" in row for row in rows):
            grid = [["O" if c == "?" else c for c in row] for row in grid]
            if is_goal("".join("".join(row) for row in grid)):
                return  # solved; the "You Win!" banner covers some tiles
            # Usually the "Ready!" banner at the start: read again shortly.
            self.nothing_found("readable_grid", after=UNREADABLE_RETRIES)
            if self._misses["readable_grid"] < UNREADABLE_RETRIES:
                wait(0.3)
                return
            log.warning("Some tiles could not be read (?); treating them as blocks")
        self.found("readable_grid")
        moves = solve(grid)
        if moves is None:
            log.warning("No solution found for this grid; re-reading")
            self.snapshot("no_solution")
            return
        log.info("Solution: %d moves", len(moves))
        if moves and not self.slide_checked(*moves[0]):
            if self.still_open():
                log.warning("First slide never took; re-reading the board")
                self.snapshot("first_move_failed")
            return
        for src, dst in moves[1:]:
            check_stop()
            if not self.still_open():
                return
            self.slide(src, dst)


if __name__ == "__main__":
    run_standalone(HullBracing)
