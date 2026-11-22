"""Hull bracing: a 4x4 sliding-block puzzle.

Tiles are read as "R" (red brace), "B" (blue brace), "O" (other block) or
" " (empty slot). The goal is a full row of B and a full row of R.
"""
import heapq
import itertools
import logging

import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

log = logging.getLogger("repair")

SIZE = 4
GRID_LEFT, GRID_TOP = 1086, 514      # ref top-left of the first tile
TILE_W, TILE_H = 86, 84
PAD_X, PAD_Y = 15, 16
SAMPLE = 9                           # ref px box sampled at each tile centre
MAX_EXPANSIONS = 200_000             # solver gives up after this many states


def tile_center(row, col):
    return Point(GRID_LEFT + col * (TILE_W + PAD_X) + TILE_W // 2,
                 GRID_TOP + row * (TILE_H + PAD_Y) + TILE_H // 2)


def classify(rgb):
    r, g, b = rgb
    if 50 < r < 150:
        return "R"
    if 40 < b < 60 and g < 50 and r < 50:
        return "B"
    if r < 15 and g < 15 and b < 15:
        return " "
    return "O"


# -- solver ----------------------------------------------------------------
def is_goal(state):
    rows = [state[i:i + SIZE] for i in range(0, SIZE * SIZE, SIZE)]
    return "B" * SIZE in rows and "R" * SIZE in rows


def heuristic(state):
    """Rough distance to the goal (not admissible; good at steering)."""
    rows = [state[i:i + SIZE] for i in range(0, SIZE * SIZE, SIZE)]
    best = {"B": (0, -1), "R": (0, -1)}
    for i, row in enumerate(rows):
        if "O" in row:
            continue
        for colour in "BR":
            n = row.count(colour)
            if n > best[colour][0] and n + row.count(" ") >= SIZE:
                best[colour] = (n, i)
    cost = 0
    for colour in "BR":
        n, target = best[colour]
        cost += SIZE - n if n else 16
        if n == SIZE - 1:
            cost -= 5
        target = max(target, 0)
        for i, row in enumerate(rows):
            if i != best[colour][1]:
                cost += 0.5 * row.count(colour) * abs(i - target)
    return cost


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
    tie = itertools.count()
    frontier = [(heuristic(start), next(tie), 0, start, ())]
    best_cost = {start: 0}
    expansions = 0
    while frontier and expansions < max_expansions:
        _, _, cost, state, moves = heapq.heappop(frontier)
        if is_goal(state):
            return list(moves)
        if cost > best_cost.get(state, cost):
            continue
        expansions += 1
        if expansions % 1000 == 0:
            check_stop()  # a full search takes seconds; stay stoppable
        for nxt, move in neighbours(state):
            if cost + 1 < best_cost.get(nxt, float("inf")):
                best_cost[nxt] = cost + 1
                heapq.heappush(frontier, (cost + 1 + heuristic(nxt), next(tie),
                                          cost + 1, nxt, moves + (move,)))
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
                patch = self.win.grab(c.x - SAMPLE // 2, c.y - SAMPLE // 2, SAMPLE, SAMPLE)
                cells.append(classify(np.median(patch.reshape(-1, 3), axis=0)))
            grid.append(cells)
        return grid

    def slide(self, src, dst):
        start = tile_center(*divmod(src, SIZE))
        end = tile_center(*divmod(dst, SIZE))
        self.win.move(*start)
        self.win.mouse_down(*start)
        try:
            wait(0.01)
            self.win.move(*end)
        finally:
            self.win.mouse_up(*end)  # never leave the button held, even on stop

    def step(self):
        self.win.park_mouse()
        wait(1.0)  # let the previous slide animation settle before reading
        grid = self.read_grid()
        log.info("Grid:\n%s", "\n".join("|%s|" % "".join(row) for row in grid))
        moves = solve(grid)
        if moves is None:
            log.warning("No solution found for this grid; re-reading")
            return
        log.info("Solution: %d moves", len(moves))
        for src, dst in moves:
            check_stop()
            self.slide(src, dst)


if __name__ == "__main__":
    run_standalone(HullBracing)
