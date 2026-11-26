"""Play whole potions offline with the real rules, to compare strategies.

    python potions/simulate.py [games]

Pieces are dealt as live (Nov 2026): tier 1 only, colours uniform from red,
green and blue, plus purple for the bigger recipes.
"""
import random
import sys
import time
from collections import Counter

from brewing import Board, best_move, capacity, oriented, scorer

RGB = ("red", "green", "blue")
RGBP = RGB + ("purple",)
RECIPES = {
    "Swift Foot I": ({("red", 3): 2, ("blue", 3): 1, ("green", 3): 1}, RGB),
    "Deadeye I": ({("purple", 2): 1, ("purple", 3): 2}, RGBP),
    "Swashbuckler Stew II": ({("green", 4): 2, ("red", 4): 1}, RGBP),
    "Clap O'Thunder": ({("blue", 4): 1, ("green", 4): 1, ("red", 4): 1}, RGBP),
}
MAX_TURNS = 400


def play(needs, colours, rng, evaluate=None, deep=False, strategy=None, detail=False):
    """(won, turns) for one game; ``deep`` also weighs the unseen pair;
    ``strategy`` picks a brewing.STRATEGIES scoring for this recipe.
    ``detail`` adds the share of the recipe delivered: (won, turns, progress)."""
    if strategy is not None:
        evaluate = scorer(strategy, needs, colours)
    board = Board(needs=Counter(needs))
    total = sum(n for n in board.needs.values() if n > 0) or 1

    def result(won, turn):
        return (won, turn, progress(board, total)) if detail else (won, turn)

    def deal():
        return (rng.choice(colours), 1), (rng.choice(colours), 1)
    pair, nxt = deal(), deal()
    for turn in range(1, MAX_TURNS + 1):
        move = best_move(board, pair, nxt, evaluate=evaluate, pool=colours if deep else None)
        if move is None:
            return result(False, turn)
        board.drop(oriented(pair, move[1]), move[0])
        if not any(n > 0 for n in board.needs.values()):
            return result(True, turn)
        if any(h > capacity(c) for c, h in enumerate(board.heights())):
            return result(False, turn)
        pair, nxt = nxt, deal()
    return result(False, MAX_TURNS)


def progress(board, total):
    """Share of the recipe made, 0-1: each delivered slot counts 1, each
    open one half the share of its tier reached by its colour's best piece
    on the board (a lost Pincer Potion, one blue 6 slot, that got to blue 5
    scores ~0.4, not 0)."""
    partial = 0.0
    for (colour, tier), n in board.needs.items():
        if n > 0:
            best = max((t for col in board.cols for c, t in col if c == colour), default=0)
            partial += n * 0.5 * min(best, tier) / tier
    return min(1.0, (board.collected + partial) / total)


def report(games=10, seed=1, evaluate=None, recipes=RECIPES, deep=False):
    rows = []
    for name, (needs, colours) in recipes.items():
        rng = random.Random(seed)
        results = [play(needs, colours, rng, evaluate, deep) for _ in range(games)]
        wins = [t for won, t in results if won]
        rows.append((name, len(wins), games, sum(wins) / len(wins) if wins else 0))
    return rows


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    began = time.time()
    for name, won, games, turns in report(n):
        print("%-22s won %2d/%d  avg %.0f turns" % (name, won, games, turns))
    print("%.0fs" % (time.time() - began))
