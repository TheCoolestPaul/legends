"""Tune brewing.WEIGHTS against the simulator (cross-entropy method).

    python potions/tune.py [--generations 12] [--population 12] [--games 12]
                           [--recipes plunder3,pantano3,pincer,clap]
                           [--workers N] [--resume]

Each generation samples ``population`` weight sets around the current mean,
plays every one on the same ``games`` fresh deals of each recipe, keeps the
best quarter and moves the mean (and spread) towards them. Weights that
are positive by nature are searched on a log scale.

A game scores 1 plus a little for winning fast, or, when lost, a share of
the recipe delivered, so near-misses still tell good weights from bad
(win/lose alone is too noisy at a dozen games). Deals change every
generation so nothing is tuned to particular deals.

Progress is saved after every generation to tune_state.json (resume with
--resume) and the best weights so far to tune_best.json, both next to this
file and git-ignored. Check a result with bench.py on its own deals:

    python potions/bench.py 96 --weights potions/tune_best.json
"""
import argparse
import json
import logging
import math
import random
import statistics
import time
from pathlib import Path

import bench
import simulate

log = logging.getLogger("tune")
HERE = Path(__file__).resolve().parent
STATE = HERE / "tune_state.json"
BEST = HERE / "tune_best.json"
TUNE_SEED = 900_000           # well away from bench.SEED's deals

# (weight, low, high, log scale)
PARAMS = [
    ("material", 0.05, 20.0, True),
    ("clutter", 0.5, 60.0, True),
    ("pair", 0.2, 40.0, True),
    ("same_colour", 0.0, 10.0, False),
    ("mixed", 0.0, 10.0, False),
    ("height_sq", 0.02, 5.0, True),
    ("danger", 1.0, 300.0, True),
    ("material_base", 1.5, 3.5, False),
    ("buried", 0.0, 30.0, False),
    ("top_needed", 0.0, 30.0, False),
]
LOG_SPREAD = 0.6              # starting sd on the log scale
MIN_SPREAD = 0.05             # of each parameter's (transformed) range
ELITE = 0.25
SMOOTH = 0.7                  # share of the new mean/spread taken each step


def _param(name):
    return next(p for p in PARAMS if p[0] == name)


def to_space(name, value):
    _, low, high, logscale = _param(name)
    value = min(max(value, low), high)
    return math.log(value) if logscale else value


def from_space(name, x):
    _, low, high, logscale = _param(name)
    value = math.exp(x) if logscale else x
    return min(max(value, low), high)


def span(name):
    _, low, high, logscale = _param(name)
    return (math.log(high) - math.log(low)) if logscale else (high - low)


def initial_state():
    mean = {name: to_space(name, bench.WEIGHTS[name]) for name, *_ in PARAMS}
    spread = {name: (LOG_SPREAD if logscale else span(name) / 5)
              for name, _, _, logscale in PARAMS}
    return {"generation": 0, "mean": mean, "spread": spread, "history": [], "best": None}


def sample(state, rng):
    return {name: from_space(name, rng.gauss(state["mean"][name], state["spread"][name]))
            for name, *_ in PARAMS}


def game_score(won, turns, progress):
    if won:
        return 1.0 + 0.2 * (1 - turns / simulate.MAX_TURNS)
    return 0.8 * progress


def evaluate_population(candidates, recipes, games, seed, n_workers):
    """[(fitness, {recipe: wins})] for each candidate, all on the same deals."""
    jobs = []
    for i, overrides in enumerate(candidates):
        weights = bench.weights_from(overrides)
        for job in bench.jobs_for(weights, recipes, games, seed):
            jobs.append((i, job))
    results = bench.run_jobs([job for _, job in jobs], n_workers)
    per = [{} for _ in candidates]
    for (i, _), (name, _, won, turns, progress) in zip(jobs, results):
        per[i].setdefault(name, []).append((won, turns, progress))
    out = []
    for rows in per:
        fitness = statistics.mean(statistics.mean(game_score(*r) for r in rs)
                                  for rs in rows.values())
        out.append((fitness, {name: sum(1 for r in rs if r[0]) for name, rs in rows.items()}))
    return out


def step(state, candidates, scored):
    """Move the mean and spread towards the elite candidates; returns the
    candidates' indices, best first."""
    order = sorted(range(len(candidates)), key=lambda i: -scored[i][0])
    elite = [candidates[i] for i in order[:max(2, int(len(candidates) * ELITE))]]
    for name, *_ in PARAMS:
        xs = [to_space(name, c[name]) for c in elite]
        mean, sd = statistics.mean(xs), statistics.pstdev(xs)
        state["mean"][name] = SMOOTH * mean + (1 - SMOOTH) * state["mean"][name]
        state["spread"][name] = max(SMOOTH * sd + (1 - SMOOTH) * state["spread"][name],
                                    MIN_SPREAD * span(name))
    return order


def mean_weights(state):
    return {name: from_space(name, state["mean"][name]) for name, *_ in PARAMS}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generations", type=int, default=12)
    ap.add_argument("--population", type=int, default=12)
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--recipes", default="plunder3,pantano3,pincer,clap")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    recipes = args.recipes.split(",")
    state = json.loads(STATE.read_text()) if args.resume and STATE.exists() else initial_state()
    rng = random.Random(TUNE_SEED + state["generation"])
    while state["generation"] < args.generations:
        gen = state["generation"]
        began = time.time()
        # Candidate 0 is the current mean (the hand-tuned weights at first),
        # as a yardstick on the same deals.
        candidates = [mean_weights(state)] + [sample(state, rng)
                                              for _ in range(args.population - 1)]
        scored = evaluate_population(candidates, recipes, args.games,
                                     TUNE_SEED + 10_000 * (gen + 1), args.workers)
        order = step(state, candidates, scored)
        top = order[0]
        log.info("generation %d (%.0fs): yardstick %.3f %s; best %.3f %s",
                 gen, time.time() - began, scored[0][0], scored[0][1],
                 scored[top][0], scored[top][1])
        state["history"].append({"generation": gen, "yardstick": scored[0][0],
                                 "yardstick_wins": scored[0][1], "best": scored[top][0],
                                 "best_weights": candidates[top], "best_wins": scored[top][1]})
        if state["best"] is None or scored[top][0] > state["best"]["fitness"]:
            state["best"] = {"fitness": scored[top][0], "weights": candidates[top],
                             "generation": gen}
        state["generation"] = gen + 1
        STATE.write_text(json.dumps(state, indent=1))
        # The mean is the steadier pick than one lucky candidate; both are kept.
        BEST.write_text(json.dumps({"weights": mean_weights(state),
                                    "best_single": state["best"]}, indent=1))
    log.info("done; mean weights in %s", BEST)


if __name__ == "__main__":
    main()
