"""Poker: reading the table (from recorded sessions), hand values and decisions."""
import random
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "poker"))
sys.path.insert(0, str(ROOT / "repair"))

import poker_bot  # noqa: E402
from poker_bot import Player, Table, decide, hand_value, parse, win_chance  # noqa: E402
from screen import ImageWindow, Rect  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "poker"


def window(name):
    img = np.array(Image.open(FIXTURES / (name + ".jpg")).convert("RGB"))
    return ImageWindow(img, Rect(0, 0, img.shape[1], img.shape[0]))


def table(name):
    return Table(window(name))


# -- reading the table -----------------------------------------------------------------
@pytest.mark.parametrize("name, buttons", [
    ("turn_check", {"check": True, "raise": True}),    # Check / Bet 4
    ("turn_call", {"check": False, "raise": True}),    # Call 16 / Raise to 32
    ("river_call", {"check": False, "raise": True}),   # Call 8 / Raise to 24
    ("ten_six", {"check": True, "raise": True}),
    ("waiting", None),                                 # "Waiting for other players ..."
    ("between_hands", None),
])
def test_reads_the_buttons(name, buttons):
    assert table(name).buttons() == buttons


@pytest.mark.parametrize("name, hole, board", [
    ("turn_check", ["5s", "9h"], ["9s", "Qh", "9d"]),
    ("turn_call", ["5s", "9h"], ["9s", "Qh", "9d", "9c"]),
    ("river_call", ["4s", "4h"], ["7s", "Jc", "Jd", "Kd", "Ah"]),
    ("ten_six", ["7h", "Jc"], ["5d", "Ah", "Ks", "6s", "Ts"]),
    ("between_hands", [None, None], []),
])
def test_reads_the_cards(name, hole, board):
    t = table(name)
    assert t.hole() == hole
    assert t.board() == board


@pytest.mark.parametrize("name, opponents", [
    ("turn_check", 3),
    ("river_call", 2),       # the top-right seat had folded
    ("between_hands", 0),
])
def test_counts_players_still_in(name, opponents):
    assert table(name).opponents() == opponents


# -- hand values -----------------------------------------------------------------------
def value(cards):
    return hand_value([parse(c) for c in cards.split()])


@pytest.mark.parametrize("better, worse", [
    ("As Ks Qs Js Ts 2c 3d", "9h 9c 9d 9s Ah 2c 3d"),   # straight flush > quads
    ("9h 9c 9d 9s 2h 2c 3d", "Qh Qc Qd 9s 9h 2c 3d"),   # quads > full house
    ("Qh Qc Qd 9s 9h 2c 3d", "Ah Kh 2h 7h 9h Qc 3d"),   # full house > flush
    ("Ah Kh 2h 7h 9h Qc 3d", "5c 6d 7h 8s 9c Ac Ad"),   # flush > straight
    ("5c 6d 7h 8s 9c Ac Ad", "Ac Ad As 8s 9c 2h 3d"),   # straight > trips
    ("2c 3d 4h 5s Ac Kd 9h", "Ac Ad As 8s 9c 2h 4d"),   # the wheel is a straight
    ("6c 2d 3h 4s 5c Ad 9h", "2c 3d 4h 5s Ac Kd 9h"),   # 6-high straight > wheel
    ("Ac Ad 9s 9c 2h 3d 5c", "Ac Ad 8s 8c Kh 3d 5c"),   # two pair by the lower pair
    ("Ac Ad 9s 9c Kh 3d 5c", "Ac Ad 9s 9c Qh 3d 5c"),   # two pair by the kicker
    ("Ac Ad Ks 2c 7h 3d 5c", "Ac Ad Qs 2c 7h 3d 5c"),   # pair by the kicker
    ("Ac Kd 9s 7c 5h 3d 2c", "Ac Kd 9s 7c 4h 3d 2c"),   # high card down to the fifth
])
def test_hand_order(better, worse):
    assert value(better) > value(worse)


def test_split_board_is_a_tie():
    board = "Ac Kd Qh Js Tc"
    assert value("2h 3d " + board) == value("4h 5s " + board)


# -- chance of winning and decisions ----------------------------------------------------
def test_win_chance():
    rng = random.Random(1)
    assert win_chance(["As", "Ah"], [], 1, 2000, rng) == pytest.approx(0.85, abs=0.03)
    assert win_chance(["7c", "2d"], [], 1, 2000, rng) == pytest.approx(0.35, abs=0.04)
    # the nuts on the river
    assert win_chance(["As", "Ks"], ["Qs", "Js", "Ts", "2c", "3d"], 3, 300, rng) == 1.0
    # the board plays for everyone: always a split
    assert win_chance(["2c", "3d"], ["Ac", "Kd", "Qh", "Js", "Tc"], 1, 300, rng) == \
        pytest.approx(0.5)


@pytest.mark.parametrize("chance, opponents, can_check, can_raise, preflop, action", [
    (0.9, 1, False, True, False, "raise"),
    (0.9, 1, False, False, False, "call"),    # raises capped: call
    (0.77, 1, True, True, False, "raise"),    # live: three fives heads-up only checked
    (0.51, 3, True, True, False, "raise"),    # live: trip sixes against three checked
    (0.63, 1, False, True, False, "call"),    # raising a bet heads-up needs 68%
    (0.70, 1, False, True, False, "raise"),
    (0.41, 3, False, True, True, "raise"),    # A-K before the flop
    (0.38, 3, False, True, True, "call"),     # A-Q offsuit: call
    (0.30, 3, False, True, False, "call"),    # better than a fair share (25%)
    (0.15, 3, False, True, False, "fold"),
    (0.15, 3, True, True, False, "check"),    # never fold when checking is free
])
def test_decide(chance, opponents, can_check, can_raise, preflop, action):
    assert decide(chance, opponents, can_check, can_raise, preflop) == action


def test_plays_a_turn(tmp_path, monkeypatch):
    """Four nines facing Call / Raise: raises (the right-hand button)."""
    monkeypatch.setattr(poker_bot, "LOG_DIR", tmp_path)
    win = window("turn_call")         # 9s Qh 9d 9c on the board with 5s 9h
    player = Player(win)
    action = player.act(player.table.buttons())
    assert action == "raise"
    assert win.actions == [("click", win.to_screen(*poker_bot.RAISE_BUTTON))]
    assert player.hands == 1
    assert list(tmp_path.glob("poker-*.jsonl"))


def test_only_calls_once_raised_back(tmp_path, monkeypatch):
    """A second turn on the same street (someone raised us back): call, even
    with four nines."""
    monkeypatch.setattr(poker_bot, "LOG_DIR", tmp_path)
    win = window("turn_call")
    player = Player(win)
    assert player.act(player.table.buttons()) == "raise"
    assert player.act(player.table.buttons()) == "call"
    assert win.actions[-1] == ("click", win.to_screen(*poker_bot.CALL_BUTTON))


def test_never_presses_swap():
    """The swap buttons (cheating) sit left of x=1280; nothing clicks there."""
    for point in (poker_bot.CALL_BUTTON, poker_bot.RAISE_BUTTON, poker_bot.FOLD_BUTTON):
        assert point[0] > 1280
