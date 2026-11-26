"""Potion brewing: the rules simulation and the screen reader."""
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "potions"))

from brewing import Board, best_move, neighbours, oriented, parse  # noqa: E402
from screen import ImageWindow, Rect  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "potions"


# -- rules -----------------------------------------------------------------------
def test_hex_neighbours_follow_the_half_cell_offset():
    # Even column 2, row 1 touches odd columns 1 and 3 at rows 1 and 2.
    assert set(neighbours(2, 1)) == {(2, 0), (2, 2), (1, 1), (1, 2), (3, 1), (3, 2)}
    # Odd column 3, row 1 touches even columns 2 and 4 at rows 0 and 1.
    assert set(neighbours(3, 1)) == {(3, 0), (3, 2), (2, 0), (2, 1), (4, 0), (4, 1)}


def test_three_touching_merge_into_the_next_tier_at_the_lowest():
    board = Board([[("green", 1), ("green", 1)]] + [[] for _ in range(7)])
    board.drop((("green", 1), ("blue", 1)), 0)
    assert board.cols[0] == [("green", 2)]
    assert board.cols[1] == [("blue", 1)]


def test_merge_lands_on_the_piece_touching_the_others():
    """Live (turn 10): blues at (5,1) and (6,0), a blue dropped onto (5,2):
    the game put the blue 2 at (5,1), not on the lower (6,0)."""
    board = parse("""
        .. .. .. .. .. b1 b1 ..
        .. .. .. .. .. b2 r1 ..
    """)
    board.cols[6] = [("blue", 1), ("red", 1)]
    board.cols[5] = [("blue", 2), ("blue", 1)]
    board.drop((("blue", 1), ("red", 1)), 5)
    assert board.cols[5] == [("blue", 2), ("blue", 2)]
    assert board.cols[6] == [("red", 1), ("red", 1)]


def test_four_of_a_kind_make_two_of_the_next_tier():
    """Live (potion 2, turn 10): greens at (3,1) and (4,1), two greens
    dropped on top: two green 2s, at (3,1) and (4,1)."""
    board = Board([[], [], [], [("blue", 1), ("green", 1)], [("blue", 1), ("green", 1)],
                   [], [], []])
    board.drop((("green", 1), ("green", 1)), 3)
    assert board.cols[3] == [("blue", 1), ("green", 2)]
    assert board.cols[4] == [("blue", 1), ("green", 2)]


def test_merge_lands_on_the_middle_of_a_chain():
    """Live (turn 18): reds at (6,0) (6,1), a red dropped onto (5,0): the
    red 2 landed at (6,0)."""
    board = Board([[], [], [], [], [], [], [("red", 1), ("red", 1)], []])
    board.drop((("green", 1), ("red", 1)), 4)
    assert board.cols[5] == [] and board.cols[6] == [("red", 2)]
    assert board.cols[4] == [("green", 1)]


def test_merges_chain():
    # Making a third g2 from three g1 merges again into g3.
    board = parse("""
        g1 g1 .. .. .. .. .. ..
        g2 g2 .. .. .. .. .. ..
    """)
    board.drop((("green", 1), ("red", 1)), 2)        # g1 at (2,0) touches (1,1)
    assert sorted(p for col in board.cols for p in col) == [("green", 3), ("red", 1)]


def test_a_piece_the_recipe_needs_is_taken_off_the_board():
    board = Board([[("purple", 2), ("purple", 2)]] + [[] for _ in range(7)],
                  needs={("purple", 3): 2})
    board.drop((("purple", 2), ("red", 1)), 0)
    assert board.collected == 1 and board.needs[("purple", 3)] == 1
    assert board.cols[0] == []


def test_full_columns_cannot_take_a_pair():
    """Odd columns hold 9 pieces, even ones (half a cell higher) only 8."""
    board = Board([[("red", 1)] * 8, [], [], [("red", 1)] * 8, [("red", 1)] * 8, [], [], []])
    assert not board.can_drop(0)          # even column 0 is full at 8
    assert board.can_drop(2)              # odd column 3 still has room at 8
    assert not board.can_drop(3)          # but even column 4 doesn't


def test_best_move_makes_a_recipe_piece_when_it_can():
    board = parse("""
        .. .. .. .. .. p2 .. ..
        .. .. .. .. .. p2 .. ..
    """, needs={("purple", 3): 6})
    pair = (("purple", 2), ("red", 1))
    c, swapped = best_move(board, pair)
    board.drop(oriented(pair, swapped), c)
    assert board.collected == 1


def test_best_move_is_none_when_nothing_fits():
    board = Board([[("red", 1)] * 9 for _ in range(8)])
    assert best_move(board, (("red", 2), ("blue", 2))) is None


# -- reading the table --------------------------------------------------------------
def table(name, scale=1.0):
    from brew_bot import PotionTable
    img = Image.open(FIXTURES / (name + ".png")).convert("RGB")
    if scale != 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    img = np.array(img)
    return PotionTable(ImageWindow(img, Rect(0, 0, img.shape[1], img.shape[0])))


MIDGAME = ("g2,g1,p2,g1,b2,g1 | b2,r1,b1,g1,p2,g1,b2,g1,r1 | b3,p1,b1 | r1,r1 | "
           "g2 | p2,p1 | r2,r1,r1 | -")


def test_reads_the_mid_game_board():
    from brew_bot import describe
    t = table("board_midgame")
    assert describe(t.read_columns()) == MIDGAME
    assert t.read_hand() == (("green", 1), ("red", 1), 0)
    assert t.read_next() == (("green", 1), ("red", 1))


def test_reads_the_recipe():
    assert table("board_start").read_recipe() == Counter({("purple", 3): 6})


def test_empty_board_reads_empty():
    t = table("board_start")
    assert t.read_columns() == [[] for _ in range(8)]
    assert t.read_next() == (("blue", 1), ("blue", 1))


def test_reader_works_on_a_smaller_window():
    """Resolution independence: the same frame at 75% reads the same."""
    from brew_bot import describe
    t = table("board_midgame", scale=0.75)
    assert describe(t.read_columns()) == MIDGAME
    assert t.read_hand() == (("green", 1), ("red", 1), 0)


def test_checked_recipe_slots_are_not_needed_any_more():
    """Live: the red tier 4 slot was already filled (check mark), but the
    bot still counted it and planned around pulling a red 4 off the board."""
    assert table("game_over").read_recipe() == Counter({("green", 4): 2})


def test_unmade_recipe_icons_still_read():
    """Unmade slots show a grey icon in a coloured border."""
    assert table("swift_foot_start").read_recipe() == Counter(
        {("red", 3): 2, ("blue", 3): 1, ("green", 3): 1})


def test_game_over_dialog_is_recognised():
    assert table("game_over").game_over()
    assert not table("board_midgame").game_over()
    assert not table("recipe_list").game_over()


def test_unreadable_pieces_never_merge():
    unknown = ("?", 0)
    board = Board([[unknown, unknown]] + [[] for _ in range(7)])
    board.drop((unknown, ("red", 1)), 0)
    assert board.cols[0] == [unknown] * 3


def test_brewed_dialog_is_recognised():
    assert table("brewed").brewed()
    assert not table("game_over").brewed()
    assert not table("board_midgame").brewed()


def test_maxed_out_question_is_recognised():
    assert table("maxed_out").maxed_out()
    assert not table("brewed").maxed_out()
    assert not table("board_midgame").maxed_out()


def test_recipe_list_and_switch_question_are_recognised():
    assert table("recipe_list").recipe_list()
    assert table("recipe_list_popup").recipe_list()       # a guild invite over it
    assert not table("board_midgame").recipe_list()
    assert table("switch_confirm").switch_confirm()
    assert not table("maxed_out").switch_confirm()
    assert not table("switch_confirm").maxed_out()


def test_recipe_rows_by_name_or_number():
    from brew_bot import recipe_row
    assert recipe_row("swift foot i") == 4
    assert recipe_row(" Deadeye I ") == 7
    assert recipe_row("13") == 13
    import pytest
    with pytest.raises(ValueError):
        recipe_row("Swift Foot IX")


# -- speed: checking against the prediction ---------------------------------------
def test_quick_checks_agree_with_a_full_read():
    t = table("board_midgame")
    cols = t.read_columns()
    t.look()
    assert t.board_is(cols)
    changed = [list(col) for col in cols]
    changed[2][1] = ("red", 1)
    assert not t.board_is(changed)
    assert not t.board_is(cols[:7] + [[("red", 1)]])         # a piece that isn't there
    assert t.hand_is((("green", 1), ("red", 1)), 0)
    assert not t.hand_is((("red", 1), ("green", 1)), 0)


def test_expected_board_and_pair_return_at_once():
    import time
    t = table("board_midgame")
    cols = t.read_columns()
    began = time.monotonic()
    got, hand = t.wait_for(cols, (("green", 1), ("red", 1)), 0)
    assert got == cols and hand == (("green", 1), ("red", 1), 0)
    assert time.monotonic() - began < 0.4                    # no half-second settle


def test_unexpected_board_falls_back_to_a_full_read(monkeypatch):
    import brew_bot
    monkeypatch.setattr(brew_bot, "MATCH_WAIT", 0.05)
    monkeypatch.setattr(brew_bot, "STEADY", 0.05)
    t = table("board_midgame")
    real = t.read_columns()
    wrong = [list(col) for col in real]
    wrong[2][1] = ("red", 1)
    got, hand = t.wait_for(wrong, (("green", 1), ("red", 1)), 0)
    assert got == real                                       # what's really there


# -- deeper search ------------------------------------------------------------------
def test_unseen_pair_odds_add_up():
    from brewing import pair_odds
    odds = pair_odds({"red", "green", "blue", "purple"})
    assert len(odds) == 10
    assert abs(sum(p for _, p in odds) - 1) < 1e-9


def test_deeper_search_still_takes_a_recipe_piece_when_it_can():
    board = parse("""
        .. .. .. .. .. p2 .. ..
        .. .. .. .. .. p2 .. ..
    """, needs={("purple", 3): 6})
    pair = (("purple", 2), ("red", 1))
    c, swapped = best_move(board, pair, (("red", 1), ("green", 1)),
                           pool={"red", "green", "blue", "purple"})
    board.drop(oriented(pair, swapped), c)
    assert board.collected == 1


def test_unmade_recipe_colour_comes_from_the_border():
    """Live: Pincer Potion's grey Crab Soul (blue 6) was read as purple 6;
    unmade icons are grey, the border says blue."""
    assert table("pincer_start").read_recipe() == Counter({("blue", 6): 1})


def test_grey_recipe_icons_with_weak_matches_still_count():
    """Live: Pantano Silver Spirit III's grey Venom Gem (red 5) matched at
    0.76, under the board threshold, and the recipe lost a slot."""
    assert table("pantano_start").read_recipe() == Counter(
        {("red", 5): 1, ("blue", 5): 1, ("green", 5): 1, ("green", 4): 1})


def test_searching_only_changed_cells_matches_a_full_search():
    """The fast core looks for merges only around pieces that moved; on a
    settled board that must find exactly what a whole-board search finds."""
    import random
    from brewing import moves
    rng = random.Random(7)
    colours = ("red", "green", "blue", "purple")
    fast = Board(needs={("blue", 3): 2, ("red", 4): 1})
    for _ in range(300):
        pair = ((rng.choice(colours), 1), (rng.choice(colours), 1))
        options = [m for m in moves(pair) if fast.can_drop(m[0])]
        if not options:
            break
        c, swapped = rng.choice(options)
        full = fast.copy()
        fast.drop(oriented(pair, swapped), c)
        full.cols[c].append(oriented(pair, swapped)[0])
        full.cols[c + 1].append(oriented(pair, swapped)[1])
        full._collect()
        full.resolve(new={(c, len(full.cols[c]) - 1), (c + 1, len(full.cols[c + 1]) - 1)})
        assert fast.cols == full.cols and fast.collected == full.collected


# -- strategies and the per-recipe planner ----------------------------------------
def test_lanes_put_needed_colours_in_the_middle():
    from brewing import lane_centres
    centres = lane_centres({("blue", 6): 1}, ("red", "green", "blue", "purple"))
    assert 2.5 < centres["blue"] < 4.5                     # the middle of 8 columns
    assert min(centres.values()) < 1 and max(centres.values()) > 6


def test_lanes_cost_pieces_away_from_their_colour():
    from brewing import evaluate, scorer
    needs = {("blue", 6): 1}
    score = scorer("lanes", needs, ("red", "green", "blue", "purple"))
    home = Board([[], [], [], [("blue", 1)], [], [], [], []], needs)
    away = Board([[("blue", 1)], [], [], [], [], [], [], []], needs)
    assert evaluate(home) - score(home) < evaluate(away) - score(away)
    assert scorer("standard", needs, ()) is evaluate


def test_planner_keeps_the_strategy_that_wins_most(monkeypatch, tmp_path):
    import planner
    monkeypatch.setattr(planner, "PLANS", tmp_path / "plans.json")
    calls = []

    def fake_assess(needs, pool, games):
        calls.append(needs)
        return {"standard": {"won": 5, "games": 16, "median_turns": 90},
                "lanes": {"won": 12, "games": 16, "median_turns": 100}}
    monkeypatch.setattr(planner, "assess", fake_assess)
    needs = Counter({("red", 5): 1, ("blue", 5): 1})
    first = planner.plan(needs)
    assert first["strategy"] == "lanes" and first["win"] == 12 / 16
    assert planner.plan(needs) == first and len(calls) == 1     # cached on disk
    assert planner.recipe_key(needs) == planner.recipe_key(Counter(dict(reversed(needs.items()))))


def test_planner_sticks_with_standard_unless_beaten_clearly():
    import planner

    def results(standard, lanes, lanes_turns=30):
        return {"standard": {"won": standard, "games": 16, "median_turns": 40},
                "lanes": {"won": lanes, "games": 16, "median_turns": lanes_turns}}
    assert planner.best(results(16, 16)) == "standard"        # fewer turns is noise
    assert planner.best(results(10, 11)) == "standard"        # one game is noise
    assert planner.best(results(10, 12)) == "lanes"


def test_planning_really_simulates(monkeypatch, tmp_path):
    """One tiny real plan, end to end, in worker processes."""
    import planner
    monkeypatch.setattr(planner, "PLANS", tmp_path / "plans.json")
    results = planner.assess(Counter({("red", 2): 1}), games=2, workers=2)
    assert set(results) == {"standard", "lanes"}
    assert all(r["games"] == 2 and r["won"] == 2 for r in results.values())


def _brewer_with_plan(monkeypatch, win, any_tier=False, fail=False):
    import planner
    from brew_bot import Brewer

    def fake_plan(needs, pool=None):
        if fail:
            raise RuntimeError("no simulation")
        return {"strategy": "lanes", "win": win, "median_turns": 100, "results": {}}
    monkeypatch.setattr(planner, "plan", fake_plan)
    return Brewer(table("pantano_start").win, any_tier=any_tier)


def test_recipes_the_plan_expects_to_lose_are_skipped(monkeypatch):
    brewer = _brewer_with_plan(monkeypatch, win=0.4)
    brewer.play()
    assert brewer.result == "too hard" and brewer.turns == 0
    assert brewer.win.actions == []                       # no clicks at all


def test_a_good_plan_is_played_with_its_strategy(monkeypatch):
    from brewing import evaluate
    brewer = _brewer_with_plan(monkeypatch, win=0.9)
    assert brewer.choose_plan(Counter({("red", 5): 1}))
    assert brewer.strategy == "lanes" and brewer.evaluate is not evaluate


def test_any_tier_plays_a_poor_plan_anyway(monkeypatch):
    assert _brewer_with_plan(monkeypatch, win=0.2, any_tier=True).choose_plan(
        Counter({("blue", 6): 1}))


def test_without_a_plan_the_tier_limit_applies(monkeypatch):
    brewer = _brewer_with_plan(monkeypatch, win=0, fail=True)
    assert not brewer.choose_plan(Counter({("blue", 6): 1}))
    assert brewer.choose_plan(Counter({("red", 4): 1}))


def test_nothing_happens_away_from_the_potion_table():
    """Live: started during a sea battle, the bot parked the cursor on the
    player. Without a recipe or a pair it must not touch the mouse."""
    from brew_bot import Brewer
    win = table("recipe_list").win                       # no board, no recipe
    brewer = Brewer(win)
    brewer.play()
    assert brewer.result == "no table" and win.actions == []


# -- learning each island's colours -------------------------------------------------
def test_plan_keys_tell_gray_from_green():
    import planner
    needs = Counter({("red", 3): 1})
    assert planner.recipe_key(needs, ("red", "green", "blue")) !=         planner.recipe_key(needs, ("red", "gray", "blue"))


def test_a_recipes_colours_are_remembered(monkeypatch, tmp_path):
    import planner
    monkeypatch.setattr(planner, "PLANS", tmp_path / "plans.json")
    needs = Counter({("blue", 4): 5})
    assert planner.known_pool(needs) is None
    planner.remember_pool(needs, ("red", "blue", "green"))
    assert planner.known_pool(needs) == ("blue", "green", "red")


def _brewer_learning(monkeypatch, tmp_path, planned_for):
    import planner
    from brew_bot import Brewer
    monkeypatch.setattr(planner, "PLANS", tmp_path / "plans.json")
    calls = []

    def fake_plan(needs, pool=None):
        calls.append(tuple(sorted(pool)))
        return {"strategy": "standard", "win": 1.0, "median_turns": 50 if len(pool) == 3 else 100,
                "results": {}}
    monkeypatch.setattr(planner, "plan", fake_plan)
    brewer = Brewer(table("board_start").win)
    if planned_for:
        planner.remember_pool(Counter({("blue", 4): 5}), planned_for)
    assert brewer.choose_plan(Counter({("blue", 4): 5}))
    return brewer, calls


def _deal(brewer, colours, pairs=12):
    for i in range(pairs):
        brewer.note_dealt(((colours[i % len(colours)], 1), (colours[(i + 1) % len(colours)], 1)))


def test_dealt_colours_trigger_a_background_replan(monkeypatch, tmp_path):
    """Live: a red/green/blue island, planned for four colours, expected 106
    turns per potion and took 35-65."""
    import time
    import planner
    brewer, calls = _brewer_learning(monkeypatch, tmp_path, None)
    assert calls == [tuple(sorted(planner.POOL))]           # four colours to start
    _deal(brewer, ("red", "green", "blue"))
    deadline = time.monotonic() + 2
    while brewer.replan and "result" not in brewer.replan and time.monotonic() < deadline:
        time.sleep(0.01)
    brewer.apply_replan()
    assert brewer.plan_pool == ("blue", "green", "red")
    assert calls[-1] == ("blue", "green", "red")
    assert planner.known_pool(Counter({("blue", 4): 5})) == ("blue", "green", "red")


def test_known_colours_are_planned_for_at_once(monkeypatch, tmp_path):
    brewer, calls = _brewer_learning(monkeypatch, tmp_path, ("red", "green", "blue"))
    assert calls == [("blue", "green", "red")]
    _deal(brewer, ("red", "green", "blue"))
    assert brewer.replan is None and len(calls) == 1         # nothing to re-plan


def test_colours_arent_judged_too_early(monkeypatch, tmp_path):
    brewer, calls = _brewer_learning(monkeypatch, tmp_path, None)
    _deal(brewer, ("red", "green", "blue"), pairs=5)
    assert brewer.replan is None and not brewer.pool_checked


# -- planning with the recipe's own colours ------------------------------------------
def test_a_gray_recipe_is_planned_with_gray():
    """Live: 5 x gray 4 was planned for red/green/blue/purple, so the
    simulation never dealt gray and couldn't win."""
    import planner
    assert planner.planning_pool(Counter({("gray", 4): 5})) == ("blue", "gray", "green", "red")


def test_red_green_and_blue_plus_one_extra_colour():
    """Islands deal red, green and blue, plus at most one of orange, gray
    and purple."""
    import planner
    for extra in ("orange", "gray", "purple"):
        pool = planner.planning_pool(Counter({(extra, 3): 1, ("red", 3): 1}))
        assert pool == tuple(sorted(("red", "green", "blue", extra)))
    for base in ("red", "green", "blue"):
        # No extra needed and none seen yet: plan for a 4th, the harder case.
        assert len(planner.planning_pool(Counter({(base, 3): 1}))) == 4


def test_a_recipe_needing_two_extra_colours_is_an_error():
    import planner
    for pair in (("gray", "purple"), ("gray", "orange"), ("orange", "purple")):
        with pytest.raises(ValueError):
            planner.planning_pool(Counter({(pair[0], 3): 1, (pair[1], 3): 1}))


def test_colours_seen_dealt_are_used_when_the_recipe_needs_no_extra():
    import planner
    needs = Counter({("blue", 4): 5})
    assert planner.planning_pool(needs, ("blue", "green", "red")) == ("blue", "green", "red")
    assert planner.planning_pool(needs, ("blue", "green", "orange", "red")) ==         ("blue", "green", "orange", "red")
    # A red/green/blue colour not seen yet is still dealt.
    assert planner.planning_pool(needs, ("blue", "green")) == ("blue", "green", "red")


def test_the_recipes_extra_colour_wins_over_one_seen():
    import planner
    needs = Counter({("orange", 3): 2, ("red", 3): 0})     # red is already made
    assert planner.planning_pool(needs, ("red", "green", "blue")) ==         ("blue", "green", "orange", "red")
    assert planner.planning_pool(needs, ("blue", "green", "purple", "red")) ==         ("blue", "green", "orange", "red")


def test_two_extra_colours_seen_are_a_misread():
    import planner
    needs = Counter({("blue", 4): 5})
    assert planner.planning_pool(needs, ("blue", "gray", "green", "purple", "red")) ==         tuple(sorted(planner.POOL))


def test_an_unknown_recipe_colour_is_an_error():
    import planner
    with pytest.raises(ValueError):
        planner.planning_pool(Counter({("teal", 3): 1}))


def test_the_brewer_plans_a_gray_recipe_with_gray(monkeypatch, tmp_path):
    import planner
    from brew_bot import Brewer
    monkeypatch.setattr(planner, "PLANS", tmp_path / "plans.json")
    calls = []

    def fake_plan(needs, pool=None):
        calls.append(tuple(sorted(pool)))
        return {"strategy": "standard", "win": 1.0, "median_turns": 80, "results": {}}
    monkeypatch.setattr(planner, "plan", fake_plan)
    brewer = Brewer(table("board_start").win)
    needs = Counter({("gray", 4): 5})
    assert brewer.choose_plan(needs)
    assert calls == [("blue", "gray", "green", "red")]   # gray is the island's 4th
    # Twelve pairs that happen to hold no gray: gray is still planned for,
    # so there's nothing to re-plan.
    _deal(brewer, ("red", "green", "blue"))
    assert brewer.replan is None and len(calls) == 1
    assert brewer.plan_pool == ("blue", "gray", "green", "red")
