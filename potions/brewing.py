"""Potion brewing rules and move choice, without any screen access.

The board is 8 hex columns; odd columns sit half a cell lower than even
ones. Pieces are (colour, tier) pairs, tier 1-6. A pair is dropped onto two
neighbouring columns (c, c+1) and each piece falls to the top of its column.
Three or more touching pieces of the same colour and tier merge: a group of
n becomes n - 2 pieces of the next tier (3 -> 1, 4 -> 2, ...; see
merge_cells for where they land); everything above falls down, and merges
can chain. A piece that matches an
open recipe slot (same colour and tier) is pulled off the board into it.
The game is lost when the columns fill up.
"""
from collections import Counter

COLUMNS = 8
HEIGHT = 9            # pieces per column (measured live, Nov 2026)
MAX_TIER = 6
COLOURS = ("red", "blue", "green", "orange", "gray", "purple")


def neighbours(c, k):
    """Cells touching (column, row-from-bottom); may be off the board."""
    yield c, k - 1
    yield c, k + 1
    other = (k, k + 1) if c % 2 == 0 else (k - 1, k)
    for dc in (-1, 1):
        if 0 <= c + dc < COLUMNS:
            for r in other:
                yield c + dc, r


def height_key(cell):
    """Smaller is physically lower (odd columns sit half a cell lower)."""
    c, k = cell
    return (k - 0.5 * (c % 2), c)


class Board:
    """Columns of pieces, bottom first, plus what the recipe still needs."""

    def __init__(self, columns=None, needs=None):
        self.cols = [list(col) for col in (columns or [[] for _ in range(COLUMNS)])]
        self.needs = Counter(needs or {})
        self.collected = 0

    def copy(self):
        b = Board(self.cols, self.needs)
        b.collected = self.collected
        return b

    def at(self, c, k):
        if 0 <= c < COLUMNS and 0 <= k < len(self.cols[c]):
            return self.cols[c][k]
        return None

    def heights(self):
        return [len(col) for col in self.cols]

    def can_drop(self, c):
        return 0 <= c < COLUMNS - 1 and all(len(self.cols[i]) < HEIGHT for i in (c, c + 1))

    def drop(self, pair, c):
        """Drop ``pair`` (left piece, right piece) onto columns c, c+1 and
        resolve every merge and pickup. Returns the number of merges."""
        if not self.can_drop(c):
            raise ValueError("column full")
        self.cols[c].append(pair[0])
        self.cols[c + 1].append(pair[1])
        new = {(c, len(self.cols[c]) - 1), (c + 1, len(self.cols[c + 1]) - 1)}
        self._collect()
        return self.resolve(new)

    def _collect(self):
        """Pull pieces the recipe still needs off the board."""
        for c in range(COLUMNS):
            kept = []
            for piece in self.cols[c]:
                if self.needs.get(piece, 0) > 0:
                    self.needs[piece] -= 1
                    self.collected += 1
                else:
                    kept.append(piece)
            self.cols[c] = kept

    def groups(self):
        """Groups of 3+ touching pieces of the same colour and tier."""
        seen, found = set(), []
        for c in range(COLUMNS):
            for k, piece in enumerate(self.cols[c]):
                if (c, k) in seen or piece[0] == "?":
                    continue            # unreadable pieces never merge
                group, todo = [], [(c, k)]
                seen.add((c, k))
                while todo:
                    cell = todo.pop()
                    group.append(cell)
                    for n in neighbours(*cell):
                        if n not in seen and self.at(*n) == piece:
                            seen.add(n)
                            todo.append(n)
                if len(group) >= 3:
                    found.append((piece, group))
        return found

    def resolve(self, new=()):
        """Merge every group (and the chains that follow). ``new`` holds the
        cells of the pieces just dropped."""
        merges = 0
        while True:
            groups = self.groups()
            if not groups:
                return merges
            grid = [list(col) for col in self.cols]
            for (colour, tier), group in groups:
                kept = merge_cells(group, new)
                for c, k in group:
                    grid[c][k] = None
                for c, k in kept:
                    grid[c][k] = (colour, min(tier + 1, MAX_TIER))
                merges += 1
            self.cols = [[p for p in col if p is not None] for col in grid]
            new = ()
            self._collect()


def merge_cells(group, new=()):
    """Where a merged group's new pieces land: n - 2 of the group's cells.

    Live (Nov 2026), eight clean mismatches between the game and simpler
    models: three blues at (5,1) (5,2) (6,0) merged at (5,1); three reds at
    (5,0) (6,0) (6,1) at (6,0) (not the physically lowest piece either
    time); four greens at (3,1) (3,2) (4,1) (4,2) became two green 2s at
    (3,1) and (4,1). All fit: the members touching the most others in the
    group get the new pieces. Ties: physically lowest, then leftmost
    (unconfirmed).
    """
    cells = set(group)

    def key(cell):
        degree = sum(1 for n in neighbours(*cell) if n in cells)
        return (-degree, height_key(cell))
    return sorted(group, key=key)[:len(group) - 2]


# -- choosing a move ----------------------------------------------------------
def moves(pair):
    """Every (column, swapped) option for a pair (identical pieces: no swap)."""
    swaps = (False,) if pair[0] == pair[1] else (False, True)
    return [(c, s) for c in range(COLUMNS - 1) for s in swaps]


def oriented(pair, swapped):
    return (pair[1], pair[0]) if swapped else pair


def evaluate(board):
    """Higher is better. Recipe progress first, then pieces building towards
    what the recipe needs, tidy single-colour clusters and low columns."""
    score = 1000.0 * board.collected
    wanted = {}
    for (colour, tier), n in board.needs.items():
        if n > 0:
            wanted[colour] = max(wanted.get(colour, 0), tier)
    for c in range(COLUMNS):
        for k, (colour, tier) in enumerate(board.cols[c]):
            target = wanted.get(colour)
            if target and tier < target:
                score += 3 ** (tier - 1)            # material for the recipe
            elif not target:
                score -= 2                         # clutter
            for n in neighbours(c, k):
                other = board.at(*n)
                if other is None:
                    continue
                if other == (colour, tier):
                    score += 6                     # one away from a merge
                elif other[0] == colour:
                    score += 1.5                   # same-colour column/cluster
                else:
                    score -= 1
    heights = board.heights()
    score -= sum(h * h for h in heights) * 0.6
    score -= 40 * sum(1 for h in heights if h >= HEIGHT - 1)
    return score


def _dropped(board, pair, move):
    b = board.copy()
    b.drop(oriented(pair, move[1]), move[0])
    return b


def best_move(board, pair, next_pair=None):
    """(column, swapped) for ``pair``, looking one pair ahead when known.
    None if nothing fits."""
    best, best_score = None, None
    for move in moves(pair):
        if not board.can_drop(move[0]):
            continue
        after = _dropped(board, pair, move)
        score = evaluate(after)
        if next_pair is not None:
            follow = [evaluate(_dropped(after, next_pair, m)) for m in moves(next_pair)
                      if after.can_drop(m[0])]
            score = max(follow) if follow else score - 10_000
        if best_score is None or score > best_score:
            best, best_score = move, score
    return best


CODES = {"r": "red", "b": "blue", "g": "green", "o": "orange", "k": "gray", "p": "purple"}


def parse(text, needs=None):
    """Board from rows of codes like 'g1' (colour letter from CODES + tier),
    top row first, left to right; '..' is empty. Rows count from the bottom
    of each column, ignoring the half-cell offset. Handy for tests."""
    codes = CODES
    rows = [line.split() for line in text.strip().splitlines()]
    cols = [[] for _ in range(COLUMNS)]
    for row in reversed(rows):
        for c, code in enumerate(row):
            if code != "..":
                cols[c].append((codes[code[0]], int(code[1])))
    return Board(cols, needs)
