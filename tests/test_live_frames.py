"""Tests against frames captured from the live game (2560x1369, Nov 2026).

Everything outside the repair panel is blacked out in these fixtures.
"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import screen
from hull_bracing import HullBracing
from hull_scrubbing import HullScrubbing
from plank_sawing import PlankSawing
from repair_bind import RepairBot
from hammering import Hammering
from repair_mini_game import (BUTTONS, Point, button_completed,
                              ready_banner_visible, repair_bar_visible)
from screen import ImageWindow, Rect

LIVE = Path(__file__).resolve().parent / "fixtures" / "live"


def live(name):
    img = np.array(Image.open(LIVE / (name + ".png")).convert("RGB"))
    return ImageWindow(img, Rect(0, 0, img.shape[1], img.shape[0]))


@pytest.fixture
def bot_on(monkeypatch):
    """A RepairBot on a still frame; clicks are recorded, nothing waits."""
    screen.reset_stop()
    monkeypatch.setattr(RepairBot, "wait_for_game", lambda self, *a, **k: None)

    def make(name):
        return RepairBot(live(name))
    return make


def clicks(win):
    return [pos for kind, pos in win.actions if kind == "click"]


def test_fresh_repair_window(bot_on):
    bot = bot_on("repair_start")
    win = bot.win
    assert win.scale == 1.0
    assert bot.active_game() is None
    assert repair_bar_visible(win)
    assert [button_completed(win, b) for b in BUTTONS] == [False] * 5
    bot.tick()
    assert clicks(win) == [win.to_screen(*BUTTONS[4])]     # hull patching first


def test_scrubbing_in_progress(bot_on):
    bot = bot_on("scrubbing_play")
    game = bot.active_game()
    assert isinstance(game, HullScrubbing)
    assert not game.is_completed()
    assert bot.playable_game() is game
    assert len(game.find_dirt()) > 0


def test_finished_game_moves_on_to_the_next_button(bot_on):
    """Regression: a finished game keeps its title up, which looped forever."""
    bot = bot_on("scrubbing_done")
    win = bot.win
    game = bot.active_game()
    assert isinstance(game, HullScrubbing) and game.is_completed()
    assert bot.playable_game() is None
    assert [button_completed(win, b) for b in BUTTONS] == [True, False, False, False, False]
    bot.tick()
    assert clicks(win) == [win.to_screen(*BUTTONS[4])]     # hull patching next


def test_selected_button_frame_hides_the_bar_marker(bot_on):
    """Regression: with the first button selected, its frame covers the bar
    marker; a visible title must still count as the repair window being open."""
    bot = bot_on("scrubbing_done")
    assert not repair_bar_visible(bot.win)
    bot.tick()
    assert clicks(bot.win)                                 # it did not just wait


# -- hull bracing on the live board -------------------------------------------
def grid_rows(win):
    return ["".join(r) for r in HullBracing(win).read_grid()]


def test_bracing_mid_game_board():
    assert grid_rows(live("bracing_play")) == ["BRRR", " OOO", " OO ", "BRBB"]


def test_bracing_red_row_and_green_column_board():
    """Regression: boards can need a red row and a green *column*; the old
    reader saw green tiles as plain blocks and the solver only knew rows."""
    from hull_bracing import is_goal, required_lines, solve
    rows = grid_rows(live("bracing_red_green"))
    assert rows == ["O R ", "GOO ", "RG  ", "ORGX"]
    state = "".join(rows)
    assert [axis for _, axis in required_lines(state)] == ["row", "col"]
    moves = solve([list(r) for r in rows])
    assert moves is not None and len(moves) <= 25
    cells = list(state)
    for src, dst in moves:
        cells[dst], cells[src] = cells[src], " "
    assert is_goal("".join(cells))


def test_bracing_one_line_board():
    """Regression (live): "Move the pieces to form a straight line!" with four
    red pieces; the solver demanded two lines and refused it forever."""
    from hull_bracing import is_goal, solve
    rows = grid_rows(live("bracing_one_line"))
    assert rows == ["OOR ", "R  O", " R O", "O  R"]
    moves = solve([list(r) for r in rows])
    assert moves is not None
    cells = list("".join(rows))
    for src, dst in moves:
        cells[dst], cells[src] = cells[src], " "
    assert is_goal("".join(cells))


def test_bracing_board_under_the_ready_banner():
    # Row 3 sits under the "Ready!" text; everything else reads correctly.
    rows = grid_rows(live("bracing_ready"))
    assert rows[0] == "ROOB" and rows[1] == "O RO" and rows[3] == "BRBO"
    assert "?" in rows[2]


def test_bracing_waits_out_the_ready_banner(monkeypatch, snapshot_dir):
    """Unreadable tiles on an unsolved board: read again, don't move yet."""
    import hull_bracing
    monkeypatch.setattr(hull_bracing, "wait", lambda s: None)
    win = live("bracing_ready")
    game = HullBracing(win)
    for _ in range(hull_bracing.UNREADABLE_RETRIES - 1):
        game.step()
    assert win.actions == []                           # no slides planned on a guess
    assert not list(snapshot_dir.glob("*.png"))
    game.step()                                        # still unreadable: fall back
    assert len(list(snapshot_dir.glob("*_no_readable_grid_*.png"))) == 1
    # Treating the hidden red tile as a block leaves 3 reds: no solution, so
    # it re-reads instead of sliding tiles on a wrong board.
    assert win.actions == []


def test_bracing_retries_an_ignored_first_slide(monkeypatch):
    """Regression: the first drag landed while "Brace!" was still up and was
    ignored; replaying the plan without it reproduced the live board
    exactly. Now the first slide is checked and repeated until it takes."""
    import hull_bracing
    monkeypatch.setattr(hull_bracing, "wait", lambda s: None)
    win = live("bracing_play")
    game = HullBracing(win)
    reads = iter(["R", " "])                     # one read per try: empties after the 2nd drag
    monkeypatch.setattr(game, "tile_code", lambda i: next(reads))
    monkeypatch.setattr(hull_bracing, "MOVE_CHECK_TIME", 0.0)
    assert game.slide_checked(4, 0)
    assert sum(kind == "down" for kind, _ in win.actions) == 2


def test_bracing_gives_up_on_a_first_slide_that_never_takes(monkeypatch, snapshot_dir):
    import hull_bracing
    monkeypatch.setattr(hull_bracing, "wait", lambda s: None)
    monkeypatch.setattr(hull_bracing, "MOVE_CHECK_TIME", 0.0)
    win = live("bracing_play")
    game = HullBracing(win)                     # a still frame: never moves
    game.step()
    downs = sum(kind == "down" for kind, _ in win.actions)
    assert downs == 1 + hull_bracing.FIRST_MOVE_RETRIES   # no later moves
    assert len(list(snapshot_dir.glob("*first_move_failed*.png"))) == 1


@pytest.mark.parametrize("name, act", [
    ("bracing_play", lambda g: g.step()),
    ("patching_two_new_leaks", lambda g: g.step()),
    ("hammering_ready", lambda g: g.wait_for_glow(Point(895, 639))),
])
def test_no_input_once_the_game_has_vanished(monkeypatch, name, act):
    """On a crewed ship someone else can finish the repair and the panel
    vanishes mid-step; the bot must stop clicking straight away."""
    import hull_bracing
    from hull_patching import HullPatching
    monkeypatch.setattr(hull_bracing, "wait", lambda s: None)
    win = live(name)
    game = {"bracing_play": HullBracing, "patching_two_new_leaks": HullPatching,
            "hammering_ready": Hammering}[name](win)
    monkeypatch.setattr(game, "still_open", lambda: False)
    act(game)
    assert [k for k, _ in win.actions if k in ("click", "down")] == []


def test_still_open_is_cached_briefly():
    win = live("bracing_play")
    game = HullBracing(win)
    calls = []
    game.is_active = lambda: calls.append(1) or True
    assert game.still_open() and game.still_open()
    assert len(calls) == 1


def test_bracing_won_board_needs_no_moves(monkeypatch):
    import hull_bracing
    monkeypatch.setattr(hull_bracing, "wait", lambda s: None)
    win = live("bracing_won")
    assert grid_rows(win) == ["RRRR", "  OO", "O OO", "BBBB"]
    HullBracing(win).step()
    assert win.actions == []


# -- plank sawing on live frames ----------------------------------------------
def test_plank_is_identified():
    assert PlankSawing(live("plank_letter_n")).identify_plank() == "letter_n"


def test_plank_still_animating_in_is_not_sawn():
    game = PlankSawing(live("plank_animating"))
    assert game.identify_plank() is None
    assert max(game.info["plank_scores"].values()) > 0.9   # the old 0.9 cutoff would saw


def test_saw_drag_follows_the_line_in_small_steps():
    """Regression: jumping straight between corners failed most cuts live."""
    from plank_sawing import PLANKS, SAW_STEP
    win = live("plank_letter_n")
    PlankSawing(win).saw(PLANKS["letter_n"])
    kinds = [k for k, _ in win.actions]
    assert kinds[1] == "down" and kinds[-1] == "up" and "down" not in kinds[2:]
    moves = [p for k, p in win.actions if k == "move"]
    assert 12 <= len(moves) <= 30                           # not 5 jumps, not 80
    steps = [((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
             for (x0, y0), (x1, y1) in zip(moves, moves[1:])]
    assert max(steps) <= SAW_STEP * 1.6


@pytest.mark.parametrize("plank", ["forward_slash", "in_top_out_right", "letter_n",
                                   "straight_horizontal", "straight_vertical"])
def test_saw_carries_on_past_the_end_of_the_cut(plank):
    """The stroke goes SAW_OVERSHOOT past the last point, in the same
    direction, so the game sees the saw leave the plank."""
    import math
    from plank_sawing import PLANKS, SAW_OVERSHOOT
    win = live("plank_letter_n")
    PlankSawing(win).saw(PLANKS[plank])
    (x0, y0), (x1, y1) = (win.to_screen(*p) for p in PLANKS[plank][-2:])
    ex, ey = win.actions[-1][1]                          # where the button is released
    assert abs(math.hypot(ex - x1, ey - y1) - SAW_OVERSHOOT) <= 2
    # same direction as the last segment
    assert (ex - x1) * (x1 - x0) + (ey - y1) * (y1 - y0) > 0
    assert win.rect.contains(ex, ey)                     # still inside the game


# -- the "Ready!" banner -------------------------------------------------------
@pytest.mark.parametrize("name, shown", [
    ("hammering_ready", True), ("bracing_ready", True),
    ("bracing_play", False), ("bracing_won", False), ("bracing_not_needed", False),
    ("plank_letter_n", False), ("plank_animating", False), ("repair_start", False),
    ("scrubbing_play", False), ("scrubbing_done", False),
])
def test_ready_banner_detection(name, shown):
    assert ready_banner_visible(live(name)) is shown


@pytest.mark.parametrize("name, shown", [
    # "Ready!" and each game's own word, which follows it
    ("hammering_ready", True), ("bracing_ready", True), ("banner_scrub", True),
    ("banner_patch", True), ("banner_brace", True), ("plank_animating", True),  # "Saw!"
    # gameplay
    ("bracing_play", False), ("bracing_red_green", False), ("bracing_not_needed", False),
    ("plank_letter_n", False), ("scrubbing_play", False), ("hammering_one_left", False),
    ("patching_two_new_leaks", False), ("patching_hole_and_old_patches", False),
])
def test_any_start_banner_is_detected(name, shown):
    """Regression: after "Ready!" each game shows "Brace!", "Saw!"... which
    the "Ready!"-only check missed, so bots acted on it."""
    from repair_mini_game import banner_visible
    assert banner_visible(live(name)) is shown


def test_no_input_while_the_ready_banner_is_up(monkeypatch):
    """Regression: hammering clicked through "Ready!" (its letters even
    passed as nails), so the first hits were wasted."""
    import repair_mini_game
    screen.reset_stop()
    waits = []

    def fake_wait(seconds):
        waits.append(seconds)
        if len(waits) >= 5:
            screen.request_stop()
    monkeypatch.setattr(repair_mini_game, "wait", fake_wait)
    win = live("hammering_ready")
    with pytest.raises(screen.StopRequested):
        Hammering(win).play()
    screen.reset_stop()
    assert len(waits) >= 5 and win.actions == []


# -- hammering ----------------------------------------------------------------
def test_live_nails_are_found():
    nails = Hammering(live("hammering_ready")).find_nails()
    assert [n.x for n in nails] == [895, 1004, 1114, 1224, 1333, 1442, 1552, 1662]
    assert {n.y for n in nails} == {639}


def test_half_sunk_nail_is_still_hit_and_flush_ones_are_not():
    """Regression: after a weak hit a nail sat just above the board; it was
    outside the scan band, so the bot saw "no nails" and hammering stalled."""
    nails = Hammering(live("hammering_one_left")).find_nails()
    assert nails == [Point(1484, 740)]                # 5 flush "Perfect" ones skipped


def test_grey_blob_without_a_shaft_is_not_a_nail():
    """Regression: the hammer sprite (grey head, wooden handle) next to the
    cursor was taken for nails, so hits landed below and beside nails."""
    win = live("hammering_ready")
    x, y = win.to_screen(1186, 690)                 # between two nails
    win.image[y:y + 15, x:x + 42] = (170, 170, 170)  # grey "head"
    win.image[y + 15:y + 60, x + 18:x + 24] = (120, 80, 40)  # wooden handle
    nails = Hammering(win).find_nails()
    assert len(nails) == 8 and all(n.y == 639 for n in nails)


def test_drifting_banner_fragments_are_not_nails(monkeypatch):
    """Regression: fading "Ready!" letters drifting past the board were read
    as half-sunk nails. Only nails in the same place in both scans count."""
    win = live("hammering_ready")
    game = Hammering(win)
    real = game.find_nails()
    fake_x, fake_y = win.to_screen(1306, 738)
    scans = iter([0, 6])                     # the fragment rises 6 px between scans

    def scan():
        img = win.image.copy()
        y = fake_y - next(scans)
        img[y:y + 15, fake_x:fake_x + 40] = (175, 175, 175)
        win.image = img
        return Hammering.find_nails(game)
    monkeypatch.setattr(game, "find_nails", scan)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert game.steady_nails() == real


def burst_on(monkeypatch, glow_age):
    """Run one burst on the live nails; the glow showed ``glow_age`` s ago."""
    import time as _time
    win = live("hammering_ready")
    game = Hammering(win)
    monkeypatch.setattr(game, "wait_for_glow", lambda nail: _time.monotonic() - glow_age)
    nails = game.find_nails()
    return game.burst(nails), clicks(win), [win.to_screen(*n) for n in nails]


def test_burst_hits_several_nails_in_one_glow(monkeypatch):
    from hammering import BURST_MAX
    hits, clicked, nails = burst_on(monkeypatch, glow_age=0.0)
    assert hits == BURST_MAX and clicked == nails[:BURST_MAX]


def test_burst_clicks_all_land_inside_the_window(monkeypatch):
    """Regression: the window was checked before each click, so the last
    click landed ~10 ms late (and missed). Clicks here take ~10 ms, like
    live; every click after the first must finish inside BURST_WINDOW."""
    import types
    import hammering
    from hammering import BURST_WINDOW, FIRST_HIT_DELAY
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(hammering, "time", types.SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda s: setattr(clock, "now", clock.now + s)))
    win = live("hammering_ready")
    game = Hammering(win)
    nails = game.find_nails()
    monkeypatch.setattr(game, "wait_for_glow", lambda nail: 0.0)   # glow at t=0
    landed = []

    def click_taking_10ms(x, y):
        clock.now += 0.010
        landed.append(clock.now)
    monkeypatch.setattr(win, "click", click_taking_10ms)
    hits = game.burst(nails)
    assert hits == 2                                  # lands at 18, 28 ms
    assert landed[0] >= FIRST_HIT_DELAY + 0.010 - 1e-9  # live: earlier hits didn't count
    assert all(t <= BURST_WINDOW + 1e-9 for t in landed), landed


def test_hammer_resends_the_move_while_waiting_for_glow(monkeypatch, snapshot_dir):
    """Live: with input lagging, the move to a nail was lost and the bot
    hovered there without ever clicking. While waiting it re-sends the
    move, and if no glow comes it says so and saves a frame."""
    import types
    import hammering
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(hammering, "time", types.SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda s: setattr(clock, "now", clock.now + s)))
    win = live("hammering_one_left")                # no glowing circle on screen
    game = Hammering(win)
    monkeypatch.setattr(game, "still_open", lambda: True)
    assert game.wait_for_glow(Point(1484, 740)) is None
    moves = [p for k, p in win.actions if k == "move"]
    expected = int(hammering.HAMMER_WINDOW / hammering.REMOVE_EVERY)
    assert len(moves) >= expected                   # the first move plus re-sends
    assert len({p for p in moves}) > 1              # nudged, not identical
    assert len(list(snapshot_dir.glob("*no_glow*.png"))) == 1


def test_cursor_rests_at_top_centre(monkeypatch):
    """Resting left-centre put the cursor over the crew list, where a lagged
    click opened a crewmate's profile."""
    win = live("repair_start")
    x, y = win.park_point()
    assert abs(x - (win.rect.left + win.rect.width // 2)) <= 1
    assert win.rect.top <= y <= win.rect.top + 20


def test_burst_stops_when_the_glow_is_over(monkeypatch):
    hits, clicked, nails = burst_on(monkeypatch, glow_age=1.0)
    assert hits == 1 and clicked == nails[:1]       # the nail that glowed


# -- hull patching ------------------------------------------------------------
def test_new_small_leaks_are_found_and_old_patches_ignored():
    """Two fresh holes (small black dots with a jet); three old patches with
    water running under them must not be clicked."""
    from hull_patching import HullPatching
    leaks = HullPatching(live("patching_two_new_leaks")).find_leaks()
    assert [(p.x, p.y) for p in leaks] == [(1114, 645), (1641, 736)]


def test_open_hole_found_not_the_streaming_patch():
    """Regression: the old detector keyed on water under old patches, so it
    clicked a patched spot here and missed the open hole."""
    from hull_patching import HullPatching
    leaks = HullPatching(live("patching_hole_and_old_patches")).find_leaks()
    assert [(p.x, p.y) for p in leaks] == [(923, 649)]


def test_hole_half_hidden_by_its_jet_is_found():
    """Live: a big jet covered the lower half of its hole, leaving a black
    cap too short for the size check; the leak ran all game, never clicked."""
    from hull_patching import HullPatching
    leaks = HullPatching(live("patching_hole_over_jet")).find_leaks()
    assert (1400, 640) in [(p.x, p.y) for p in leaks]


# -- bilge pumping --------------------------------------------------------------
def gauge_playback(monkeypatch, fixture="bilge_gauge"):
    """Replay a recorded stretch of the gauge on a fake clock. Returns (game,
    clock, clicks, rod_y_at(t)). Each grab shows the frame at clock time."""
    import types
    import bilge_pumping
    from bilge_pumping import BilgePumping, rod_row
    data = np.load(LIVE / (fixture + ".npz"))
    frames, times = data["frames"], data["times"]
    top, left = int(data["top"]), int(data["left"])
    clock = types.SimpleNamespace(now=0.0)
    fake_time = types.SimpleNamespace(monotonic=lambda: clock.now,
                                      sleep=lambda s: setattr(clock, "now", clock.now + s))
    monkeypatch.setattr(bilge_pumping, "time", fake_time)
    win = ImageWindow(np.zeros((1369, 2560, 3), np.uint8), Rect(0, 0, 2560, 1369))

    def frame_at(t):
        return frames[min(len(frames) - 1, int(np.searchsorted(times, t)))]

    def capture(x, y, w, h):
        clock.now += 0.001                          # a grab takes ~1 ms
        y0, x0 = top - 23, left                     # ref -> this window's pixels
        f = frame_at(clock.now)
        win.image[y0:y0 + f.shape[0], x0:x0 + f.shape[1]] = f
        return win.image[y:y + h, x:x + w]
    monkeypatch.setattr(win, "_capture_client", capture)

    def rod_y_at(t):
        row = rod_row(frame_at(t), 1.0)
        return None if row is None else top + row

    clicks = []

    def click(x, y):
        clock.now += 0.010                          # a click takes ~10 ms
        clicks.append(clock.now)
    monkeypatch.setattr(win, "click", click)
    game = BilgePumping(win)
    monkeypatch.setattr(game, "still_open", lambda: True)   # only the gauge is drawn
    return game, clock, clicks, rod_y_at


@pytest.mark.parametrize("fixture, reaches", [("bilge_gauge", "top"),
                                              ("bilge_gauge_bottom", "bottom")])
def test_rod_is_tracked_into_each_end_of_the_gauge(fixture, reaches):
    from bilge_pumping import rod_row
    data = np.load(LIVE / (fixture + ".npz"))
    ys = [rod_row(f, 1.0) for f in data["frames"]]
    ys = [int(data["top"]) + y for y in ys if y is not None]
    assert len(ys) >= 30
    if reaches == "top":
        assert min(ys) < 530                       # up into the top green zone
    else:
        assert max(ys) > 910                       # down into the bottom one


@pytest.mark.parametrize("fixture, zone", [
    ("bilge_gauge", (519, 572)),            # green zone at the top, rod rising fast
    ("bilge_gauge_bottom", (867, 917)),     # zone moved to the bottom, rod slower
])
def test_bilge_pumps_once_while_the_rod_is_in_the_green(monkeypatch, fixture, zone):
    """The zone jumps between the ends of the gauge after each pump; the bot
    finds it on screen and only clicks if the click will land in it."""
    game, clock, clicks, rod_y_at = gauge_playback(monkeypatch, fixture)
    game.step()
    assert len(clicks) == 1                         # one pump per pass
    y = rod_y_at(clicks[0])                         # where the rod was as it landed
    assert zone[0] <= y <= zone[1], (y, game.info)


def test_bilge_ignores_the_old_zone_lingering_after_a_pump(monkeypatch):
    """Regression (live trace): after a top pump the old zone lingers as a
    green band near the top while the bottom zone shows. Picking the biggest
    green patch chose the top, so the rod passed the bottom zone unpumped
    and was only caught a whole bounce later."""
    game, clock, clicks, rod_y_at = gauge_playback(monkeypatch, "bilge_gauge_bottom")
    win = game.win
    y0 = 530 - 23                                   # a 34 px band, like the trace
    win.image[y0:y0 + 34, 860:1040] = (40, 200, 60)
    game.last_pumped_end = "top"
    data = np.load(LIVE / "bilge_gauge_bottom.npz")
    ys = [(t, rod_y_at(t)) for t in data["times"]]
    bounce_at = max((ty for ty in ys if ty[1] is not None), key=lambda ty: ty[1])[0]
    game.step()
    assert clicks and clicks[0] <= bounce_at, (clicks, bounce_at)
    assert game.last_pumped_end == "bottom"


def test_bilge_clicks_away_from_the_gauge(monkeypatch):
    """Any click on the game pumps. Clicking on the zone left the cursor over
    the gauge being read, so pumps click on the ship picture instead."""
    from bilge_pumping import GAUGE, PUMP_AT
    game, clock, clicks, rod_y_at = gauge_playback(monkeypatch)
    spots = []
    game.win.click = lambda x, y: (spots.append((x, y)), clicks.append(clock.now))
    game.step()
    assert spots == [PUMP_AT]
    gx, gy, gw, gh = GAUGE
    assert not (gx <= PUMP_AT.x <= gx + gw and gy <= PUMP_AT.y <= gy + gh)


@pytest.mark.parametrize("fixture, end", [("bilge_gauge", "top"),
                                          ("bilge_gauge_bottom", "bottom")])
def test_bilge_pumps_on_the_way_in_not_after_the_bounce(monkeypatch, fixture, end):
    """Regression (live): the bot waited for the rod to bounce off the end of
    the gauge before pumping. Heading into the end it can't overshoot, so
    the pump must land before the bounce."""
    game, clock, clicks, rod_y_at = gauge_playback(monkeypatch, fixture)
    data = np.load(LIVE / (fixture + ".npz"))
    ys = [(t, rod_y_at(t)) for t in data["times"]]
    ys = [(t, y) for t, y in ys if y is not None]
    bounce_at = (min if end == "top" else max)(ys, key=lambda ty: ty[1])[0]
    game.step()
    assert clicks and clicks[0] <= bounce_at, (clicks, bounce_at)


# -- the repair window going away ----------------------------------------------
def test_no_clicks_when_the_player_is_at_a_cannon(monkeypatch):
    import repair_bind
    screen.reset_stop()
    monkeypatch.setattr(repair_bind, "wait", lambda s: None)
    bot = RepairBot(live("at_cannon"))
    bot.tick()
    assert clicks(bot.win) == []


def test_window_closing_while_waiting_stops_the_reclick(monkeypatch, snapshot_dir):
    """Regression (live): after clicking a button the player went to a
    cannon; the re-click fired anyway and landed on the cannon view."""
    import repair_bind
    screen.reset_stop()
    bot = RepairBot(live("scrubbing_done"))
    cannon = live("at_cannon").image
    frames = iter([cannon] * 99)                     # gone by the first look after the click
    monkeypatch.setattr(repair_bind, "wait",
                        lambda s: setattr(bot.win, "image", next(frames)))
    monkeypatch.setattr(repair_bind, "RECLICK_EVERY", 0.0)
    bot.tick()
    assert clicks(bot.win) == [bot.win.to_screen(*BUTTONS[4])]   # the first click only
    assert bot.failed_starts == {}                               # not a failed start
    assert not list(snapshot_dir.glob("*button_opened_nothing*"))


def test_no_button_click_if_the_window_closed_just_before(monkeypatch):
    import repair_bind
    screen.reset_stop()
    bot = RepairBot(live("scrubbing_done"))
    monkeypatch.setattr(bot, "repair_window_open", lambda: False)
    monkeypatch.setattr(repair_bind, "wait", lambda s: None)
    bot.tick()
    assert clicks(bot.win) == []


# -- crewed ships: other players' games ----------------------------------------
def test_buttons_other_players_are_using_are_left_alone(bot_on, caplog):
    """Live: on a crewed ship the bot kept clicking a button whose game
    another player was in (yellow progress ring); it can't open for us."""
    import logging
    import repair_bind
    from repair_mini_game import button_in_progress
    bot = bot_on("crew_busy")                       # 4 done, patching in progress
    assert [button_in_progress(bot.win, b) for b in BUTTONS] == [False] * 4 + [True]
    repair_bind.wait = lambda s: None
    with caplog.at_level(logging.INFO, logger="repair"):
        bot.tick()
    assert clicks(bot.win) == []
    assert "Waiting for other players" in caplog.text


def test_a_button_that_failed_goes_to_the_back_of_the_queue(bot_on):
    """After a failed start the bot tries the other buttons first instead of
    retrying the same one straight away, even patching, which normally goes first."""
    bot = bot_on("repair_start")                    # nothing done yet
    bot.failed_starts[BUTTONS[4]] = 1
    bot.tick()
    assert clicks(bot.win) == [bot.win.to_screen(*BUTTONS[1])]


def test_own_game_in_progress_still_plays(bot_on, monkeypatch):
    """Our own game shows the yellow ring too; it's played, not skipped."""
    bot = bot_on("scrubbing_play")
    played = []
    monkeypatch.setattr(HullScrubbing, "play", lambda self: played.append(self.name))
    bot.tick()
    assert played == ["hull_scrubbing"]


def test_a_game_just_won_is_not_started_again(bot_on, monkeypatch):
    """Live: scrubbing was the last game; on winning it the ship was repaired
    and every button reset while its title lingered, so it looked unfinished
    and play() started again. A just-won game is left alone for a moment."""
    import repair_bind
    monkeypatch.setattr(repair_bind, "wait", lambda s: None)
    bot = bot_on("scrubbing_play")              # title up, no green check
    played = []
    monkeypatch.setattr(HullScrubbing, "play",
                        lambda self: played.append(self.name) or True)
    bot.tick()
    bot.tick()                                  # same frame straight after
    assert played == ["hull_scrubbing"] and clicks(bot.win) == []
    name, won_at = bot.finished
    bot.finished = (name, won_at - repair_bind.FINISH_SETTLE)
    bot.tick()                                  # long after: a new ship's game
    assert played == ["hull_scrubbing"] * 2


def test_no_input_until_a_beat_after_the_banner_goes(monkeypatch):
    """Live: games still started early, because input stays ignored for a
    moment after the banner disappears. play() waits BANNER_GRACE first."""
    import types
    import repair_mini_game
    from repair_mini_game import BANNER_GRACE
    screen.reset_stop()
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(repair_mini_game, "time",
                        types.SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(repair_mini_game, "timed_out", lambda d: clock.now > d)
    steps = []
    win = live("hammering_one_left")                # no banner on screen
    game = Hammering(win)
    monkeypatch.setattr(game, "step", lambda: steps.append(clock.now) or screen.request_stop())

    def fake_wait(seconds):
        clock.now += seconds
    monkeypatch.setattr(repair_mini_game, "wait", fake_wait)
    with pytest.raises(screen.StopRequested):
        game.play()
    screen.reset_stop()
    assert steps and steps[0] >= BANNER_GRACE - 1e-9


# -- opening the next game -----------------------------------------------------
@pytest.fixture
def no_waiting(monkeypatch):
    import repair_bind
    screen.reset_stop()
    monkeypatch.setattr(repair_bind, "wait", lambda s: None)


def test_a_game_this_ship_does_not_need_opens_already_done(no_waiting, snapshot_dir):
    """Regression: bracing opened with a check at once; that was logged as
    "opened nothing" with a snapshot. It is a normal outcome."""
    bot = RepairBot(live("bracing_not_needed"))
    game = bot.wait_for_game(BUTTONS[2], seconds=0.5)
    assert isinstance(game, HullBracing) and game.is_completed()


def test_waiting_for_the_wrong_button_times_out(no_waiting):
    bot = RepairBot(live("bracing_not_needed"))
    assert bot.wait_for_game(BUTTONS[3], seconds=0.3) is None   # hammering


def test_slow_switch_counts_as_opened(no_waiting, monkeypatch, snapshot_dir):
    """Regression: plank sawing took ~3.5 s to appear; a 3 s wait gave up."""
    import repair_bind
    frames = iter([live("scrubbing_done").image] * 4
                  + [live("patching_two_new_leaks").image] * 99)
    bot = RepairBot(live("scrubbing_done"))
    monkeypatch.setattr(repair_bind, "wait", lambda s: setattr(bot.win, "image", next(frames)))
    bot.tick()                                    # clicks hull patching, waits
    assert clicks(bot.win) == [bot.win.to_screen(*BUTTONS[4])]
    assert not list(snapshot_dir.glob("*button_opened_nothing*"))
    assert bot.failed_starts == {}


def test_a_game_that_times_out_is_skipped_for_this_ship(bot_on, monkeypatch):
    """Regression: an unsolvable bracing board timed out, then the runner
    started it again, forever. Now it moves on to the other games."""
    def times_out(self):
        self.timed_out = True
        return False
    monkeypatch.setattr(HullBracing, "play", times_out)
    bot = bot_on("bracing_play")                  # bracing open, not finished
    bot.tick()                                    # plays it; it times out
    assert bot.skipped == {"hull_bracing"} and clicks(bot.win) == []
    bot.tick()                                    # next: patching, not bracing
    assert clicks(bot.win) == [bot.win.to_screen(*BUTTONS[4])]


def test_live_button_centres():
    """BUTTONS match the real button centres measured on the live game."""
    measured = [(840, 1224), (1061, 1227), (1275, 1230), (1500, 1229), (1715, 1222)]
    for b, (x, y) in zip(BUTTONS, measured):
        assert abs(b.x - x) <= 3 and abs(b.y - y) <= 6


def test_an_ignored_button_is_clicked_again_every_second(no_waiting, monkeypatch, snapshot_dir):
    """Live: after plank sawing, two clicks on bracing over 6 s were both
    ignored. Keep pressing the button about once a second until it opens."""
    import types
    import repair_bind
    from repair_bind import RECLICK_EVERY
    clock = types.SimpleNamespace(now=0.0)
    monkeypatch.setattr(repair_bind, "time", types.SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(repair_bind, "timed_out", lambda d: clock.now > d)
    monkeypatch.setattr(repair_bind, "wait", lambda s: setattr(clock, "now", clock.now + s))
    bot = RepairBot(live("scrubbing_done"))
    assert bot.wait_for_game(BUTTONS[2], seconds=4.05) is None
    presses = clicks(bot.win)
    assert presses == [bot.win.to_screen(*BUTTONS[2])] * len(presses)
    assert len(presses) == int(4 / RECLICK_EVERY)        # 1 s, 2 s, 3 s, 4 s


def test_press_hovers_before_it_clicks(monkeypatch):
    """The game drops a click it didn't see the cursor over the button for."""
    win = live("scrubbing_done")
    monkeypatch.setattr(screen.time, "sleep", lambda s: win.actions.append(("sleep", s)))
    screen.GameWindow.press(win, *BUTTONS[1])
    at = win.to_screen(*BUTTONS[1])
    assert win.actions == [("move", at), ("sleep", screen.PRESS_HOVER), ("down", at),
                           ("sleep", screen.PRESS_HOLD), ("up", at),
                           ("sleep", screen.PRESS_AFTER)]


def test_the_saw_is_held_still_before_and_after_it_is_picked_up(monkeypatch):
    """Live: a plank was sometimes cut twice, the saw never picked up; the
    jump onto it, the press and the first move fell inside one frame."""
    from plank_sawing import PLANKS, SAW_GRAB, PlankSawing
    win = live("plank_letter_n")
    monkeypatch.setattr(screen.time, "sleep", lambda s: win.actions.append(("sleep", s)))
    PlankSawing(win).saw(PLANKS["letter_n"])
    start = win.to_screen(*PLANKS["letter_n"][0])
    assert win.actions[:4] == [("move", start), ("sleep", SAW_GRAB),
                               ("down", start), ("sleep", SAW_GRAB)]
    assert win.actions[4][0] == "move" and win.actions[-1][0] == "up"


def test_pumping_is_left_until_the_other_games_are_done(bot_on):
    """The bot is faster than people at every game but bilge pumping, which
    takes ~10 s however it's played: it takes the others first."""
    from repair_bind import start_order
    order = sorted(BUTTONS, key=lambda b: start_order(b, {}))
    assert order[-1] == BUTTONS[0]                  # pumping/scrubbing slot


def test_patching_goes_first():
    """Hull patching takes people the longest, so the bot takes it first,
    then the rest left to right, pumping last."""
    from repair_bind import start_order
    order = sorted(BUTTONS, key=lambda b: start_order(b, {}))
    assert order == [BUTTONS[4], BUTTONS[1], BUTTONS[2], BUTTONS[3], BUTTONS[0]]
