"""Compare live potion games (potions/game_logs/, written by the bot) with
the simulator.

    python potions/livecheck.py [--sim 48] [--since 2026-11-26] [--workers N]

From what the live bot logged, it checks:
  * Deals: are colours uniform over the island's colours, and are pairs
    independent (no more same-colour pairs, or repeats of the last pair's
    colour, than chance)?
  * Reads: was each pair in hand the "next" pair shown the turn before?
  * Rules: replaying each logged move with brewing.Board, is the result the
    board read the next turn?
  * Results: live wins and turns per recipe against the simulator playing
    the same recipe, colours and strategy (``--sim`` games, 0 to skip).

A gap in deals or rules means the simulator, and everything tuned in it,
plays a slightly different game from the live one.
"""
import argparse
import random
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import gamelog
import simulate
from bench import wilson, workers
from brewing import Board, oriented

CHI2_5PC = {1: 3.84, 2: 5.99, 3: 7.81, 4: 9.49, 5: 11.07}   # 5% critical values by df
FINISHED = ("brewed", "lost", "no room")    # games that ran to an end (not stopped)
SIM_SEED = 50_000


def pieces(raw):
    return [tuple(p) for p in raw]


def board_of(raw):
    return [pieces(col) for col in raw]


def needs_of(raw):
    return Counter({(c, k): n for c, k, n in raw})


def pool_of(game):
    return tuple(sorted(game.get("final_pool") or game.get("plan_pool") or ()))


def recipe_of(game):
    return tuple(sorted((c, k, n) for c, k, n in game["needs"]))


def chi_square(counts, pool):
    """(statistic, degrees of freedom, looks uniform at 5%) for colour counts."""
    n = sum(counts[c] for c in pool)
    if n == 0 or len(pool) < 2:
        return 0.0, 0, True
    e = n / len(pool)
    stat = sum((counts[c] - e) ** 2 / e for c in pool)
    df = len(pool) - 1
    return stat, df, stat <= CHI2_5PC.get(df, float("inf"))


def deals(games):
    """Per island pool: colour counts, [same-colour pairs, pairs],
    [first piece repeats last pair's first piece, chances], off-pool pieces."""
    out = defaultdict(lambda: {"colours": Counter(), "same": [0, 0], "repeat": [0, 0],
                               "off_pool": Counter()})
    for g in games:
        pool = pool_of(g)
        d = out[pool]
        prev = None
        for t in g["turns"]:
            pair = pieces(t["pair"])
            off = [c for c, _ in pair if c not in pool]
            if off:
                d["off_pool"].update(off)
                prev = None
                continue
            d["colours"].update(c for c, _ in pair)
            d["same"][0] += pair[0][0] == pair[1][0]
            d["same"][1] += 1
            if prev is not None:
                d["repeat"][0] += pair[0][0] == prev[0][0]
                d["repeat"][1] += 1
            prev = pair
    return dict(out)


def reads(games):
    """[pairs in hand that were the previous turn's "next", turns checked]."""
    agree = checked = 0
    for g in games:
        turns = g["turns"]
        for a, b in zip(turns, turns[1:]):
            if a.get("next"):
                checked += 1
                agree += pieces(a["next"]) == pieces(b["pair"])
    return agree, checked


def replay(games):
    """[moves whose replay matches the next board read, moves checked] and
    up to five examples of the ones that don't."""
    agree = checked = 0
    examples = []
    for g in games:
        turns = g["turns"]
        for i, (a, b) in enumerate(zip(turns, turns[1:])):
            board = Board(board_of(a["board"]), needs_of(a["needs"]))
            pair = oriented(pieces(a["pair"]), a["swapped"])
            try:
                board.drop(pair, a["column"])
            except ValueError:
                continue
            checked += 1
            if board.cols == board_of(b["board"]):
                agree += 1
            elif len(examples) < 5:
                examples.append((g["started"], i + 1, pair, a["column"]))
    return agree, checked, examples


def clicks(games, window=300):
    """Per kind (aim/rotate/drop) and "all": {"busy": [lost, clicks],
    "quiet": [lost, clicks]}, busy meaning the player pressed or released
    a mouse button within ``window`` ms of the bot's click."""
    out = defaultdict(lambda: {"busy": [0, 0], "quiet": [0, 0]})
    for g in games:
        for c in g.get("clicks", []):
            busy = any(abs(ms) <= window for _, _, ms, _ in c["user"])
            for key in (c["kind"], "all"):
                cell = out[key]["busy" if busy else "quiet"]
                cell[0] += not c["ok"]
                cell[1] += 1
    return dict(out)


def sim_one(job):
    needs, pool, strategy, seed = job
    won, turns = simulate.play(Counter({(c, k): n for c, k, n in needs}), pool,
                               random.Random(seed), deep=True, strategy=strategy)
    return won, turns


def results(games, sim_games=48, n_workers=None):
    """Per (recipe, pool, strategy): live and simulated wins and turns."""
    groups = defaultdict(list)
    for g in games:
        groups[(recipe_of(g), pool_of(g), g.get("final_strategy") or g.get("strategy")
                or "standard")].append(g)
    jobs = [(key, (list(key[0]), key[1], key[2], SIM_SEED + i))
            for key in groups if sim_games and key[1] for i in range(sim_games)]
    sims = defaultdict(list)
    if jobs:
        with ProcessPoolExecutor(max_workers=n_workers or workers()) as ex:
            for (key, _), r in zip(jobs, ex.map(sim_one, [j for _, j in jobs])):
                sims[key].append(r)
    rows = []
    for key, gs in sorted(groups.items()):
        done = [g for g in gs if g["result"] in FINISHED]
        wins = [g["turns_played"] for g in done if g["result"] == "brewed"]
        sim = sims.get(key, [])
        sim_wins = [t for won, t in sim if won]
        rows.append({"recipe": key[0], "pool": key[1], "strategy": key[2],
                     "games": len(done), "stopped": len(gs) - len(done), "won": len(wins),
                     "median_turns": statistics.median(wins) if wins else None,
                     "mismatches": sum(g.get("mismatches", 0) for g in gs),
                     "turns": sum(len(g["turns"]) for g in gs),
                     "sim_games": len(sim), "sim_won": len(sim_wins),
                     "sim_median_turns": statistics.median(sim_wins) if sim_wins else None})
    return rows


def pct(a, b):
    return "%3.0f%%" % (100 * a / b) if b else "  -"


def report(games, sim_games=48, n_workers=None):
    lines = ["%d games, %d turns logged" % (len(games), sum(len(g["turns"]) for g in games))]
    lines.append("\nDeals (pieces in the pairs dealt):")
    for pool, d in sorted(deals(games).items()):
        stat, df, uniform = chi_square(d["colours"], pool)
        n = sum(d["colours"].values())
        lines.append("  %s: %s  (chi-square %.1f, df %d: %s)" % (
            "/".join(pool) or "?", ", ".join("%s %s" % (c, pct(d["colours"][c], n)) for c in pool),
            stat, df, "looks uniform" if uniform else "NOT uniform"))
        k = len(pool) or 1
        lines.append("    same-colour pairs %s (chance %s); first piece repeats the last "
                     "pair's %s (chance %s)" % (pct(*d["same"]), pct(1, k), pct(*d["repeat"]),
                                                 pct(1, k)))
        if d["off_pool"]:
            lines.append("    pieces outside the planned colours: %s" % dict(d["off_pool"]))
    agree, checked = reads(games)
    lines.append("\nReads: hand was the previous 'next' pair %d/%d (%s)"
                 % (agree, checked, pct(agree, checked)))
    agree, checked, examples = replay(games)
    lines.append("Rules: replayed move reached the next board read %d/%d (%s)"
                 % (agree, checked, pct(agree, checked)))
    for started, turn, pair, col in examples:
        lines.append("  e.g. %s turn %d: %s on %d-%d" % (started, turn, pair, col, col + 1))
    counts = clicks(games)
    if counts:
        lines.append("\nClicks lost, with the player's mouse busy within 300 ms vs quiet:")
        for kind in ("aim", "rotate", "drop", "all"):
            if kind in counts:
                busy, quiet = counts[kind]["busy"], counts[kind]["quiet"]
                lines.append("  %-6s busy %d/%d %s   quiet %d/%d %s"
                             % (kind, busy[0], busy[1], pct(*busy), quiet[0], quiet[1],
                                pct(*quiet)))
    lines.append("\nResults (live vs simulated; stopped games left out):")
    for r in results(games, sim_games, n_workers):
        low, high = wilson(r["won"], r["games"])
        recipe = ", ".join("%dx %s %d" % (n, c, k) for c, k, n in r["recipe"])
        lines.append("  %s [%s, %s]" % (recipe, "/".join(r["pool"]), r["strategy"]))
        lines.append("    live: won %d/%d %s (95%%: %.0f-%.0f%%), median %s turns, "
                     "%d stopped, %d mismatches in %d turns"
                     % (r["won"], r["games"], pct(r["won"], r["games"]), 100 * low, 100 * high,
                        r["median_turns"] or "-", r["stopped"], r["mismatches"], r["turns"]))
        if r["sim_games"]:
            lines.append("    sim:  won %d/%d %s, median %s turns"
                         % (r["sim_won"], r["sim_games"], pct(r["sim_won"], r["sim_games"]),
                            r["sim_median_turns"] or "-"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim", type=int, default=48, help="simulated games per recipe (0: none)")
    ap.add_argument("--since", help="only games started on or after this date (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--logs", help="folder of game logs (default potions/game_logs)")
    args = ap.parse_args(argv)
    games = gamelog.load(args.logs)
    if args.since:
        games = [g for g in games if g["started"] >= args.since]
    if not games:
        print("No live games logged yet (the brew bot writes them to %s)."
              % (args.logs or gamelog.LOG_DIR))
        return 0
    print(report(games, args.sim, args.workers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
