"""Record live potion games, one JSON line per game, to check simulate.py.

Each game holds when it started, the recipe, the colours and strategy the
plan assumed, every turn (the board, recipe still open, pair in hand, the
next pair, the move made, and the board seen when it didn't match the
prediction) and how it ended. Files go to potions/game_logs/ (git-ignored),
one per day; livecheck.py compares them with the simulator.

Writing a log never stops the bot: any error is logged and the game goes on.
"""
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("repair")
HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "game_logs"


def _needs(needs):
    return [[c, k, n] for (c, k), n in sorted(needs.items()) if n > 0]


def _pieces(pieces):
    return [[c, t] for c, t in pieces]


class GameLog:
    def __init__(self, needs, plan_pool=None, strategy=None, folder=None):
        self.folder = Path(folder) if folder else LOG_DIR
        self.began = time.monotonic()
        self.closed = False
        self.game = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "needs": _needs(needs),
                     "plan_pool": sorted(plan_pool or []), "strategy": strategy, "turns": []}

    def turn(self, cols, needs, pair, nxt, column, swapped):
        """One move: the board and recipe it was chosen on, the pair in hand
        (as dealt, before any rotation), the next pair and the move."""
        self.game["turns"].append({
            "board": [_pieces(col) for col in cols], "needs": _needs(needs),
            "pair": _pieces(pair), "next": _pieces(nxt) if nxt else None,
            "column": column, "swapped": bool(swapped)})

    def click(self, turn, kind, ok, user):
        """One aim/rotate/drop on ``turn``: did the game take it, and the
        player's own button presses around it ([button, down/up, ms, over game])."""
        self.game.setdefault("clicks", []).append(
            {"turn": turn, "kind": kind, "ok": bool(ok), "user": user})

    def mismatch(self, seen):
        """The board after the last move wasn't the one predicted."""
        if self.game["turns"]:
            self.game["turns"][-1]["seen"] = [_pieces(col) for col in seen]

    def finish(self, result, **extra):
        """Append the game to today's file; returns its path (None if not written)."""
        if self.closed:
            return None
        self.closed = True
        self.game.update(result=result, seconds=round(time.monotonic() - self.began, 1),
                         **extra)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / (time.strftime("%Y%m%d") + ".jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.game) + "\n")
            return path
        except (OSError, TypeError, ValueError):
            log.exception("Couldn't write the game log")
            return None


def load(folder=None):
    """Every logged game, oldest first."""
    games = []
    for path in sorted(Path(folder or LOG_DIR).glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    games.append(json.loads(line))
                except ValueError:
                    log.warning("Skipping unreadable line %d of %s", n, path)
    return games
