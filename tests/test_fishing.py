"""Fishing: reading the screen (from a recorded session) and the fight logic."""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fishing"))
sys.path.insert(0, str(ROOT / "repair"))

import fish_bot  # noqa: E402
from fish_bot import Fisher, Reel, Table  # noqa: E402
from screen import ImageWindow, Rect  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "fishing"


def table(name):
    img = np.array(Image.open(FIXTURES / (name + ".jpg")).convert("RGB"))
    return Table(ImageWindow(img, Rect(0, 0, img.shape[1], img.shape[0])))


# -- the gauge -------------------------------------------------------------------------
@pytest.mark.parametrize("name, kind, level", [
    ("idle", None, 0.0),              # on the dock: empty
    ("cast_full", "cast", 1.0),       # the power bar at the top
    ("line_full", "green", 1.0),      # lure in the water, line unhurt
    ("line_partial", "green", 0.80),  # a fish pulled while held
    ("line_yellow", "yellow", 0.64),  # the shark: damaged line turns yellow
])
def test_reads_the_gauge(name, kind, level):
    g = table(name).gauge()
    assert g.kind == kind
    assert g.level == pytest.approx(level, abs=0.02)


# -- skills ------------------------------------------------------------------------------
def test_all_skills_ready_on_the_dock():
    assert all(table("idle").skills().values())


def test_cooling_skills_are_not_ready():
    """Live: right after Ocean Eye (6) and Sink Lure (5)."""
    s = table("skills_cooling").skills()
    assert not s["eye"] and not s["sink"]
    assert s["stall"] and s["pull"] and s["heal"] and s["tug"]
    s = table("tug_pull_cooling").skills()        # mid-fight, after Tug Line and Pull Fish
    assert not s["tug"] and not s["pull"] and s["heal"]


# -- the "!" marks -----------------------------------------------------------------------
def test_a_bite_shows_a_green_mark():
    x, y = table("bite").mark("green")
    assert abs(x - 1261) < 15 and abs(y - 590) < 25


def test_a_curious_fish_is_not_a_bite():
    """A green "?" before the bite: not an "!"."""
    assert table("curious").mark("green") is None


def test_a_pulling_fish_shows_a_red_mark():
    assert table("pulling_red").mark("red") is not None
    assert table("pulling_red").mark("green") is None


@pytest.mark.parametrize("name", ["idle", "line_full", "shark_fight", "caught"])
def test_no_marks_elsewhere(name):
    t = table(name)
    assert t.mark("green") is None and t.mark("red") is None


# -- after a catch ----------------------------------------------------------------------
def test_the_catch_panel_and_loot_window():
    assert table("caught").caught() and not table("caught").loot()
    assert table("loot").loot() and not table("loot").caught()
    for name in ("idle", "line_full", "bite"):
        assert not table(name).caught() and not table(name).loot()


# -- reeling -----------------------------------------------------------------------------
def test_reel_holds_and_lets_go_when_the_line_is_hurt():
    reel = Reel(0.0)
    assert reel.update(0.0, 1.0) == "hold"
    assert reel.update(0.1, 1.0) is None
    assert reel.update(0.2, 0.998) is None                  # under DROP (1.5 px): a wobble
    assert reel.update(0.3, 0.98) == "release"              # the fish pulls
    assert reel.update(0.5, 0.98) is None                   # still pausing
    assert reel.update(0.3 + fish_bot.PAUSE, 0.98) == "hold"


def test_reel_waits_longer_while_the_fish_keeps_pulling():
    reel = Reel(0.0)
    reel.update(0.0, 1.0)
    reel.update(0.1, 0.9)                                   # pull 1: pause PAUSE
    t = 0.1 + fish_bot.PAUSE
    assert reel.update(t, 0.9) == "hold"
    assert reel.update(t + 0.1, 0.8) == "release"           # pull 2 right away: longer pause
    assert reel.update(t + 0.1 + fish_bot.PAUSE, 0.8) is None
    assert reel.update(t + 0.1 + 1.5 * fish_bot.PAUSE, 0.8) == "hold"


def test_reel_lets_go_on_a_red_mark_and_waits_for_it_to_go():
    reel = Reel(0.0)
    reel.update(0.0, 1.0)
    assert reel.update(0.1, 1.0, red_mark=True) == "release"
    assert reel.update(5.0, 1.0, red_mark=True) is None      # still showing
    assert reel.update(5.1, 1.0) == "hold"


def test_healing_raises_the_level_without_a_false_alarm():
    reel = Reel(0.0)
    reel.update(0.0, 0.4)
    assert reel.update(0.1, 0.9) is None                    # Heal Line refilled it
    assert reel.update(0.2, 0.897) is None                  # a pixel's wobble, not a pull
    assert reel.update(0.3, 0.88) == "release"              # ... but a real loss from 0.9 is


# -- casting ------------------------------------------------------------------------------
class FakeWin:
    def __init__(self):
        self.actions = []
        self.scale = 1.0

    def click(self, x, y):
        self.actions.append("click")

    press = click

    def key(self, char):
        self.actions.append("key " + char)


def test_a_cast_clicks_twice_and_sets_the_bar_near_the_top(monkeypatch):
    fisher = Fisher(FakeWin())
    levels = iter([None, ("cast", 0.2), ("cast", 0.5), ("cast", 0.8), ("cast", 0.95),
                   ("cast", 1.0), ("green", 1.0)])

    def gauge():
        g = next(levels, ("green", 1.0))
        return fish_bot.Gauge(*g) if g else fish_bot.Gauge()
    monkeypatch.setattr(fisher.table, "gauge", gauge)
    monkeypatch.setattr(fish_bot, "wait", lambda s: None)
    assert fisher.cast()
    assert fisher.win.actions == ["click", "click"]


def test_a_falling_bar_is_not_set(monkeypatch):
    """The bar bounces: only click while it rises past the mark."""
    fisher = Fisher(FakeWin())
    levels = iter([("cast", 0.97), ("cast", 0.9), ("cast", 0.5), ("cast", 0.8), ("cast", 0.97),
                   ("green", 1.0)])

    def gauge():
        return fish_bot.Gauge(*next(levels, ("green", 1.0)))
    monkeypatch.setattr(fisher.table, "gauge", gauge)
    monkeypatch.setattr(fish_bot, "wait", lambda s: None)
    assert fisher.cast()
    assert fisher.win.actions == ["click", "click"]


def test_the_fishing_game_is_told_from_the_open_world():
    """Live: the session had ended and the character was swimming off the
    dock; the bot clicked twice before giving up. It now checks first."""
    for name in ("idle", "line_full", "caught", "loot", "shark_fight"):
        assert table(name).fishing(), name
    assert not table("not_fishing").fishing()


def test_nothing_is_clicked_outside_the_fishing_game():
    t = table("not_fishing")
    fisher = Fisher(t.win)
    assert fisher.fish_once() == "not fishing"
    assert t.win.actions == []


def test_an_orange_line_is_still_a_line():
    """Live: green, yellow, then orange (255,127,0) as the line weakens;
    reading orange as "no line" made the bot think the lure was back."""
    g = table("caught_orange_line").gauge()
    assert g.kind == "orange" and g.line and 0.1 < g.level < 0.6


def test_loot_windows_are_found_whatever_the_fish():
    """The header text is centred, so it moves with the fish's name; the
    trash can doesn't (live: "a Grouper!" missed a whole-header template)."""
    assert table("loot").loot() and table("loot_grouper").loot()
    assert table("caught_orange_line").caught()


def test_a_fish_that_bites_while_reeling_is_fought(monkeypatch):
    """Live: a fish took the lure while it was being reeled up."""
    fisher = Fisher(FakeWin())
    levels = iter([("green", 1.0), ("green", 1.0), ("green", 0.97)])
    monkeypatch.setattr(fisher.table, "gauge", lambda: fish_bot.Gauge(*next(levels, ("green", 0.95))))
    monkeypatch.setattr(fisher.table, "mark", lambda colour: None)
    monkeypatch.setattr(fish_bot, "wait", lambda s: None)
    fisher.win.mouse_down = fisher.win.mouse_up = lambda x, y: None
    assert fisher.reel_step(5.0) == "fish"


def test_the_trash_confirm_prompt_is_recognised():
    """Live: binning loot with a specially dyed item asks "Are you sure ...?"."""
    assert table("trash_confirm").confirm()
    for name in ("loot", "loot_grouper", "caught", "idle", "line_full"):
        assert not table(name).confirm(), name


def test_a_green_gauge_after_a_catch_is_not_a_landed_cast(monkeypatch):
    """Live: the gauge stays green on the dock after a catch, until the next
    cast; the bot took it for a lure in the water and reeled at nothing."""
    fisher = Fisher(FakeWin())
    levels = iter([("green", 0.97), ("green", 0.97), ("cast", 0.3), ("cast", 0.7),
                   ("cast", 0.96), ("cast", 0.9), ("green", 1.0)])
    monkeypatch.setattr(fisher.table, "gauge", lambda: fish_bot.Gauge(*next(levels, ("green", 1.0))))
    monkeypatch.setattr(fish_bot, "wait", lambda s: None)
    assert fisher.cast() is True
    assert fisher.win.actions == ["click", "click"]


def test_a_cast_with_the_lure_still_out_says_so(monkeypatch):
    fisher = Fisher(FakeWin())
    monkeypatch.setattr(fisher.table, "gauge", lambda: fish_bot.Gauge("green", 0.8))
    monkeypatch.setattr(fish_bot, "BAR_WAIT", 0.0)
    assert fisher.cast() == "out"
    assert fisher.win.actions == ["click"]
