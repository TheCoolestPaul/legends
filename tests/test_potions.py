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


@pytest.fixture(autouse=True)
def _game_logs_in_tmp(monkeypatch, tmp_path):
    """Games played in tests never land in the real potions/game_logs/."""
    import gamelog
    monkeypatch.setattr(gamelog, "LOG_DIR", tmp_path / "game_logs")


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


# -- a lost drop click ------------------------------------------------------------------
DROP_NOT_TAKEN = ("r3,r1 | b4,r2,r1 | b4,b1,g1,g1,r1 | g5,g2,g2,k1,k1,b1,b1 | k1,k1 | "
                  "b2,b2,r2,k1 | k1,r2,b2 | k1")


def test_gray_and_green_are_told_apart_in_logs():
    from brew_bot import describe
    t = table("drop_not_taken")
    assert describe(t.read_columns()) == DROP_NOT_TAKEN


def test_a_lost_drop_click_is_noticed():
    """Live (Nov 2026): the click was lost and the pair stayed in hand over
    columns 2-3, so the bot logged a prediction mismatch."""
    t = table("drop_not_taken")
    before = t.read_columns()
    pair = (("blue", 1), ("green", 1))
    assert not t.drop_taken(pair, 2, before, timeout=0)
    assert t.drop_taken(pair, 3, before, timeout=0)               # held elsewhere
    assert t.drop_taken(pair[::-1], 2, before, timeout=0)         # a different hand
    moved = [list(col) for col in before]
    moved[2].append(("blue", 1))
    assert t.drop_taken(pair, 2, moved, timeout=0)                # board changed



# -- benchmark and tuning ----------------------------------------------------------------
def test_tuned_weights_use_the_merge_features():
    from brewing import WEIGHTS
    assert WEIGHTS["buried"] > 0 and WEIGHTS["top_needed"] > 0
    assert WEIGHTS["material_base"] < 3.0


def test_buried_and_top_needed_features():
    from brewing import WEIGHTS, evaluate
    needs = Counter({("gray", 3): 1})
    # Column 0: gray under red (buried); column 1: red under gray (on top).
    b = parse("r1 k1 .. .. .. .. .. ..\nk1 r1 .. .. .. .. .. ..", needs)
    off = dict(WEIGHTS, buried=0.0, top_needed=0.0)
    base = evaluate(b, off)
    assert evaluate(b, dict(off, buried=5.0)) == pytest.approx(base - 5.0)
    assert evaluate(b, dict(off, top_needed=5.0)) == pytest.approx(base + 5.0)


def test_cheaper_merges_value_high_tiers_less():
    from brewing import WEIGHTS, evaluate
    b = parse("k2 .. .. .. .. .. .. ..", Counter({("gray", 4): 1}))
    assert evaluate(b, dict(WEIGHTS, material_base=2.0)) < evaluate(b)


def test_bench_plays_fixed_deals():
    import bench
    w = bench.weights_from()
    jobs = bench.jobs_for(w, ["swiftfoot"], 2)
    assert jobs == bench.jobs_for(w, ["swiftfoot"], 2)               # same deals every run
    results = [bench.play_one(j) for j in jobs]
    assert all(r[0] == "swiftfoot" and 0 <= r[4] <= 1 for r in results)
    assert results == [bench.play_one(j) for j in jobs]               # deterministic
    s = bench.summarise(results)["swiftfoot"]
    assert s["games"] == 2 and s["low"] <= s["won"] / 2 <= s["high"]


def test_wilson_interval():
    import bench
    low, high = bench.wilson(24, 48)
    assert 0.35 < low < 0.5 < high < 0.65
    assert bench.wilson(0, 10)[0] == 0.0 and bench.wilson(10, 10)[1] == 1.0


def test_tuning_moves_towards_the_best_candidates():
    import tune
    state = tune.initial_state()
    before = tune.mean_weights(state)["clutter"]
    candidates = [dict(tune.mean_weights(state), clutter=c) for c in (1.0, 2.0, 40.0, 50.0)]
    scored = [(0.1, {}), (0.2, {}), (0.9, {}), (0.8, {})]
    order = tune.step(state, candidates, scored)
    assert order[0] == 2
    assert tune.mean_weights(state)["clutter"] > before


def test_a_win_always_beats_a_loss():
    import tune
    assert tune.game_score(True, 400, 1.0) > tune.game_score(False, 10, 0.99)
    assert tune.game_score(True, 50, 1.0) > tune.game_score(True, 100, 1.0)


def test_a_tiny_tuning_run(monkeypatch, tmp_path):
    import json
    import tune
    monkeypatch.setattr(tune, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(tune, "BEST", tmp_path / "best.json")
    tune.main(["--generations", "1", "--population", "2", "--games", "1",
               "--recipes", "swiftfoot", "--workers", "2"])
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["generation"] == 1 and len(state["history"]) == 1
    assert set(json.loads((tmp_path / "best.json").read_text())["weights"]) == \
        {name for name, *_ in tune.PARAMS}


def test_progress_credits_tiers_reached_towards_open_slots():
    import simulate
    b = parse("b5 .. .. .. .. .. .. ..", Counter({("blue", 6): 1}))
    assert abs(simulate.progress(b, 1) - 0.5 * 5 / 6) < 1e-9
    b = parse("r1 .. .. .. .. .. .. ..", Counter({("blue", 6): 1}))
    assert simulate.progress(b, 1) == 0.0
    b.needs[("blue", 6)] = 0
    b.collected = 1
    assert simulate.progress(b, 1) == 1.0


# -- live game logs ---------------------------------------------------------------------
def _logged_game(folder, turns=12, seed=3):
    """Play a short simulated game, logging it as the live bot would."""
    import random

    import gamelog
    needs = Counter({("blue", 4): 1})
    pool = ("blue", "green", "red")
    rng = random.Random(seed)
    game = gamelog.GameLog(needs, pool, "standard", folder=folder)
    board = Board(needs=needs)

    def deal():
        return (rng.choice(pool), 1), (rng.choice(pool), 1)
    pair, nxt = deal(), deal()
    for _ in range(turns):
        c, swapped = best_move(board, pair, nxt)
        game.turn(board.cols, board.needs, pair, nxt, c, swapped)
        board.drop(oriented(pair, swapped), c)
        pair, nxt = nxt, deal()
    return game


def test_a_logged_game_reads_back(tmp_path):
    import gamelog
    game = _logged_game(tmp_path)
    game.mismatch([[("red", 1)]] + [[] for _ in range(7)])
    path = game.finish("lost", turns_played=12)
    assert game.finish("lost") is None                     # written once only
    (g,) = gamelog.load(tmp_path)
    assert path.parent == tmp_path and g["result"] == "lost"
    assert g["needs"] == [["blue", 4, 1]] and g["plan_pool"] == ["blue", "green", "red"]
    assert len(g["turns"]) == 12 and "seen" in g["turns"][-1]


def test_livecheck_replays_a_faithful_log_exactly(tmp_path):
    import gamelog
    import livecheck
    _logged_game(tmp_path).finish("lost", turns_played=12, final_pool=["blue", "green", "red"])
    games = gamelog.load(tmp_path)
    assert livecheck.reads(games) == (11, 11)
    agree, checked, examples = livecheck.replay(games)
    assert agree == checked == 11 and examples == []
    # A misread board breaks the move into it and the move out of it.
    games[0]["turns"][5]["board"][0].append(["purple", 3])
    agree, checked, examples = livecheck.replay(games)
    assert agree == 9 and [turn for _, turn, _, _ in examples] == [5, 6]


def test_livecheck_counts_deals():
    import livecheck
    pool = ["blue", "red"]
    turns = [{"pair": p} for p in ([["red", 1], ["red", 1]], [["red", 1], ["blue", 1]],
                                   [["blue", 1], ["blue", 1]], [["gray", 1], ["red", 1]])]
    d = livecheck.deals([{"final_pool": pool, "turns": turns}])[("blue", "red")]
    assert d["colours"] == Counter({"red": 3, "blue": 3})
    assert d["same"] == [2, 3] and d["off_pool"] == Counter({"gray": 1})
    assert livecheck.chi_square(Counter({"red": 50, "blue": 50}), ("blue", "red"))[2]
    assert not livecheck.chi_square(Counter({"red": 90, "blue": 10}), ("blue", "red"))[2]


def test_livecheck_report_runs(tmp_path):
    import gamelog
    import livecheck
    _logged_game(tmp_path).finish("brewed", turns_played=12, final_pool=["blue", "green", "red"],
                                  final_strategy="standard")
    text = livecheck.report(gamelog.load(tmp_path), sim_games=2, n_workers=1)
    assert "live: won 1/1" in text and "sim:  won" in text


def test_a_stopped_game_is_still_logged(monkeypatch):
    import gamelog
    from brew_bot import Brewer
    from screen import StopRequested
    brewer = Brewer(table("board_start").win)
    monkeypatch.setattr(Brewer, "choose_plan", lambda self, needs: True)

    def stopped(self, t, needs):
        self.turns = 4
        raise StopRequested()
    monkeypatch.setattr(Brewer, "_play_turns", stopped)
    with pytest.raises(StopRequested):
        brewer.play()
    (g,) = gamelog.load(gamelog.LOG_DIR)
    assert g["result"] == "stopped" and g["turns_played"] == 4


# -- the player's clicks vs the bot's -----------------------------------------------------
def test_the_input_log_finds_the_players_presses_near_a_click():
    from screen import InputLog
    log_ = InputLog()
    log_.add(10.00, "L", True, False)
    log_.add(10.08, "L", False, False)
    log_.add(11.00, "R", True, True)
    assert log_.near(10.1) == [["L", "down", -100, False], ["L", "up", -20, False]]
    assert log_.near(10.9, before=0.05) == [["R", "down", 100, True]]
    log_.add(10.0 + InputLog.KEEP + 1, "L", True, False)    # old history is dropped
    assert [e[1] for e in log_._events] == ["R", "L"]


def test_lost_clicks_are_logged_with_the_players_presses(monkeypatch, caplog):
    import brew_bot
    import gamelog
    from brew_bot import Brewer
    brewer = Brewer(table("board_start").win)
    brewer.game = gamelog.GameLog(Counter({("blue", 4): 1}))
    brewer.turns = 6
    monkeypatch.setattr(brew_bot.INPUT_LOG, "near",
                        lambda t0: [["L", "down", -40, False]] if t0 == 2.0 else [])
    brewer.note_click("drop", 1.0, True)
    with caplog.at_level("WARNING", logger="repair"):
        brewer.note_click("drop", 2.0, False)
    assert brewer.game.game["clicks"] == [
        {"turn": 7, "kind": "drop", "ok": True, "user": []},
        {"turn": 7, "kind": "drop", "ok": False, "user": [["L", "down", -40, False]]}]
    assert "L down at -40 ms" in caplog.text


def test_livecheck_splits_lost_clicks_by_the_players_activity():
    import livecheck
    game = {"clicks": [
        {"turn": 1, "kind": "drop", "ok": False, "user": [["L", "down", -40, False]]},
        {"turn": 1, "kind": "drop", "ok": True, "user": []},
        {"turn": 2, "kind": "aim", "ok": True, "user": [["R", "up", 900, False]]},
        {"turn": 2, "kind": "drop", "ok": True, "user": [["L", "up", 120, True]]}]}
    c = livecheck.clicks([game])
    assert c["drop"] == {"busy": [1, 2], "quiet": [0, 1]}
    assert c["aim"] == {"busy": [0, 0], "quiet": [0, 1]}      # 900 ms away: quiet
    assert c["all"] == {"busy": [1, 2], "quiet": [0, 2]}
