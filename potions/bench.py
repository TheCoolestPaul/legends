"""Benchmark potion play on fixed deals, to compare scorings fairly.

    python potions/bench.py [games] [--weights file.json] [--recipes a,b]

Plays every recipe in RECIPES ``games`` times (default 48) with the live
bot's search (best_move with the unseen pair weighed), on the same deals
(seeds) every run, and prints the win rate with a 95% interval and the
median turns of the wins. ``--weights`` scores boards with brewing.WEIGHTS
updated from a JSON file (e.g. what potions/tune.py found).

At 48 games a win rate is only good to about +-14 points; compare
scorings on the same deals and look at the intervals before trusting a gap.
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial

import simulate
from brewing import WEIGHTS, evaluate

RGB, RGBP = simulate.RGB, simulate.RGBP
RGBK = RGB + ("gray",)
# (needs, colours dealt). The hard ones are what scoring changes are for;
# the easy ones check nothing got worse.
RECIPES = {
    # Live Nov 2026 as Plunder Potion III (Tortuga): Essence of Gold x5.
    "plunder3": ({("gray", 4): 5}, RGBK),
    # Pantano Silver Spirit III: red, blue, green 5 + green 4 (planner.py note).
    "pantano3": ({("red", 5): 1, ("blue", 5): 1, ("green", 5): 1, ("green", 4): 1}, RGBP),
    "pincer": ({("blue", 6): 1}, RGBP),
    "clap": ({("blue", 4): 1, ("green", 4): 1, ("red", 4): 1}, RGBP),
    "swiftfoot": ({("red", 3): 2, ("blue", 3): 1, ("green", 3): 1}, RGB),
}
SEED = 7000


def weights_from(overrides=None):
    w = dict(WEIGHTS)
    w.update(overrides or {})
    return w


def play_one(job):
    """(recipe, game, won, turns, progress) for one deal."""
    name, game, weights, seed = job
    needs, colours = RECIPES[name]
    won, turns, progress = simulate.play(dict(needs), colours, random.Random(seed),
                                         evaluate=partial(evaluate, w=weights), deep=True,
                                         detail=True)
    return name, game, won, turns, progress


def jobs_for(weights, recipes, games, seed=SEED):
    """Every (recipe, game) job; game i of a recipe is the same deal for
    every scoring (common random numbers make comparisons much sharper)."""
    return [(name, i, weights, seed + 1000 * k + i)
            for k, name in enumerate(sorted(RECIPES)) if name in recipes
            for i in range(games)]


def workers():
    return max(1, (os.cpu_count() or 4) - 2)


def run_jobs(jobs, n_workers=None):
    with ProcessPoolExecutor(max_workers=n_workers or workers()) as ex:
        return list(ex.map(play_one, jobs, chunksize=1))


def wilson(wins, n, z=1.96):
    """95% interval for a win rate (Wilson score)."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def summarise(results):
    """{recipe: {"won", "games", "low", "high", "median_turns", "progress"}}."""
    out = {}
    for name in sorted({r[0] for r in results}):
        rows = [r for r in results if r[0] == name]
        wins = [r[3] for r in rows if r[2]]
        low, high = wilson(len(wins), len(rows))
        out[name] = {"won": len(wins), "games": len(rows), "low": low, "high": high,
                     "median_turns": statistics.median(wins) if wins else None,
                     "progress": statistics.mean(r[4] for r in rows)}
    return out


def report(summary):
    lines = []
    for name, s in summary.items():
        lines.append("%-10s won %3d/%-3d %4.0f%%  (95%%: %3.0f-%3.0f%%)  median %4s turns  "
                     "progress %3.0f%%" % (name, s["won"], s["games"], 100 * s["won"] / s["games"],
                                           100 * s["low"], 100 * s["high"],
                                           s["median_turns"] if s["median_turns"] else "-",
                                           100 * s["progress"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("games", nargs="?", type=int, default=48)
    ap.add_argument("--weights", help="JSON file of brewing.WEIGHTS overrides")
    ap.add_argument("--recipes", default=",".join(RECIPES))
    ap.add_argument("--workers", type=int, default=None,
                    help="processes (default: all cores but two; fewer while the game runs)")
    args = ap.parse_args(argv)
    overrides = json.loads(open(args.weights).read()) if args.weights else {}
    overrides = overrides.get("weights", overrides)
    recipes = args.recipes.split(",")
    began = time.time()
    results = run_jobs(jobs_for(weights_from(overrides), recipes, args.games), args.workers)
    print(report(summarise(results)))
    print("%.0fs on %d workers" % (time.time() - began, args.workers or workers()))


if __name__ == "__main__":
    sys.exit(main())
