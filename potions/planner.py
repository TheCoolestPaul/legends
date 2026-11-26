"""Pick how to play a recipe by simulating it first.

The first time the bot meets a recipe it plays GAMES simulated potions with
each strategy in brewing.STRATEGIES on exactly that recipe, keeps the one
that wins most (fewer turns breaks ties) and estimates the win rate. Results
are cached in plans.json next to this file, so later brews start at once.

    python potions/planner.py            plan the recipes in simulate.RECIPES
"""
import json
import logging
import os
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import simulate
from brewing import COLOURS, STRATEGIES

log = logging.getLogger("repair")

PLANS = Path(__file__).resolve().parent / "plans.json"     # git-ignored
GAMES = 16              # simulated potions per strategy, first look
MORE_GAMES = 32         # more of each when the first look isn't conclusive
EASY = 15               # first-look wins (of GAMES) that settle it at once
# Live (Nov 2026): Pantano Silver Spirit III's 16-game look had standard
# ahead 10-8, while 32 fresh games had lanes ahead 24-13; at ~60% wins, 16
# games swing by 2-3 either way.
MIN_WIN = 0.7           # below this the bot skips the recipe (unless --any-tier)
# Colours dealt: always red, green and blue, plus at most one of orange,
# gray or purple depending on the island (never two of those). A recipe
# needing one of the three tells which is dealt. Otherwise, until a
# recipe's colours have been seen, plan for a 4th colour, the harder case,
# so a recipe that passes is at least as likely to be won live; which one
# doesn't matter, since it's only clutter. The bot then re-plans for the
# colours actually dealt (and remembers them).
BASE = frozenset(simulate.RGB)
EXTRAS = frozenset({"orange", "gray", "purple"})
POOL = simulate.RGBP
SEED = 2026
POOLS = "_pools"        # plans.json: {recipe slots: colours last dealt}


def slots_key(needs):
    """A stable text key for a recipe's open slots."""
    return ",".join("%s%dx%d" % (c, t, n) for (c, t), n in sorted(needs.items()) if n > 0)


def recipe_key(needs, pool=POOL):
    """Key for a plan: the recipe's open slots and the colours dealt. Full
    colour names (gray and green share an initial)."""
    return "%s|%s" % (slots_key(needs), "+".join(sorted(pool)))


def known_pool(needs):
    """The colours this recipe was last dealt, or None."""
    pool = load().get(POOLS, {}).get(slots_key(needs))
    return tuple(sorted(pool)) if pool else None


def planning_pool(needs, pool=None):
    """The colours to plan ``needs`` with: red, green and blue, plus the
    one extra colour (orange, gray or purple) the recipe needs, else the
    extra seen dealt in ``pool``, else POOL's (the harder case).

    A recipe can only be made from colours that are dealt. Live (Nov 2026):
    5 x gray 4 was planned with POOL alone, never dealt a gray piece in
    simulation, and lost every game."""
    wanted = {colour for (colour, _), n in needs.items() if n > 0}
    unknown = wanted - set(COLOURS)
    if unknown:
        raise ValueError("unknown colour(s) in recipe: %s" % ", ".join(sorted(unknown)))
    extras = wanted & EXTRAS
    if len(extras) > 1:
        raise ValueError("recipe needs %s, but an island deals only one of orange, gray "
                         "and purple" % " and ".join(sorted(extras)))
    if not extras and pool is not None:
        extras = set(pool) & EXTRAS
        if len(extras) > 1:                 # a misread when learning: don't trust it
            log.warning("Colours seen dealt (%s) can't all be right; planning for a "
                        "4th colour", "/".join(sorted(pool)))
            extras = set()
            pool = None
    if not extras and pool is None:
        extras = set(POOL) & EXTRAS
    return tuple(sorted(BASE | extras))


def remember_pool(needs, pool):
    plans = load()
    plans.setdefault(POOLS, {})[slots_key(needs)] = sorted(pool)
    save(plans)


def _one(job):
    strategy, needs, pool, seed = job
    won, turns = simulate.play(dict(needs), pool, random.Random(seed), deep=True,
                               strategy=strategy)
    return strategy, won, turns


def play_games(needs, pool, games, first=0, workers=None):
    """{strategy: [(won, turns), ...]} for deals first..first+games-1; every
    strategy plays the same deals."""
    needs = [(k, n) for k, n in needs.items() if n > 0]
    jobs = [(s, needs, pool, SEED + first + i) for s in STRATEGIES for i in range(games)]
    results = {s: [] for s in STRATEGIES}
    # Leave the game a couple of cores: live, the first move after planning
    # on every core went unnoticed by the game.
    workers = workers or max(1, (os.cpu_count() or 4) - 2)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for strategy, won, turns in ex.map(_one, jobs, chunksize=1):
            results[strategy].append((won, turns))
    return results


def summary(results):
    out = {}
    for strategy, games_played in results.items():
        wins = [t for won, t in games_played if won]
        out[strategy] = {"won": len(wins), "games": len(games_played),
                         "median_turns": statistics.median(wins) if wins else None}
    return out


def assess(needs, pool=POOL, games=GAMES, workers=None, more=MORE_GAMES):
    """{strategy: {"won", "games", "median_turns"}}: ``games`` per strategy,
    then ``more`` each unless every strategy won at least EASY of them."""
    results = play_games(needs, pool, games, 0, workers)
    if more and not all(sum(won for won, _ in r) >= EASY * games / GAMES
                        for r in results.values()):
        extra = play_games(needs, pool, more, games, workers)
        for s in results:
            results[s] += extra[s]
    return summary(results)


CLEAR_WIN = 0.08        # extra share of wins another strategy needs over "standard"


def best(results):
    """"standard" unless another strategy's win rate beats it by CLEAR_WIN.
    Equal win rates and a few turns' difference are noise, and on fresh
    deals lanes did slightly worse on the simpler recipes."""
    def rate(s):
        return results[s]["won"] / results[s]["games"]
    challengers = [s for s in results if s != "standard" and rate(s) >= rate("standard") + CLEAR_WIN]
    if not challengers:
        return "standard"
    return max(challengers, key=lambda s: (rate(s), -(results[s]["median_turns"] or 1e9)))


def load():
    try:
        return json.loads(PLANS.read_text())
    except (OSError, ValueError):
        return {}


def save(plans):
    try:
        PLANS.write_text(json.dumps(plans, indent=1, sort_keys=True))
    except OSError:
        log.exception("Could not save %s", PLANS)


def plan(needs, pool=POOL, games=GAMES, refresh=False):
    """{"strategy", "win", "median_turns", "results"} for this recipe, from
    the cache or, the first time, by simulating it (about half a minute)."""
    key = recipe_key(needs, pool)
    plans = load()
    if key in plans and not refresh:
        return plans[key]
    log.info("Planning this recipe by simulating it (once; up to ~2 min for hard recipes)...")
    began = time.monotonic()
    results = assess(needs, pool, games)
    choice = best(results)
    entry = {"strategy": choice,
             "win": results[choice]["won"] / results[choice]["games"],
             "median_turns": results[choice]["median_turns"],
             "results": results}
    log.info("Planned in %.0fs: %s", time.monotonic() - began,
             ", ".join("%s won %d/%d" % (s, r["won"], r["games"]) for s, r in results.items()))
    plans[key] = entry
    save(plans)
    return entry


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name, (needs, colours) in simulate.RECIPES.items():
        p = plan(needs)
        print("%-22s -> %-8s win %3.0f%%  median %s turns" % (
            name, p["strategy"], 100 * p["win"], p["median_turns"]))
