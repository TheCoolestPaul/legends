"""Offline tests for the repair bots, using screenshots in the repo.

The screenshots are maximised 1920x1080 captures (game area 1920x1017 at
y=23). Each is also rescaled to 1280x720 and 2560x1440 to check that the
detection does not depend on the window size.
"""
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from hammering import Hammering
from hull_bracing import SIZE, is_goal, neighbours, solve
from hull_patching import HullPatching
from repair_bind import RepairBot
from repair_mini_game import BUTTONS, Point, button_completed, repair_bar_visible
from screen import REF_HEIGHT, REF_TOP, ImageWindow, Rect

ROOT = Path(__file__).resolve().parent.parent
HAMMERING = ["tests/test_pics/0.png", "tests/test_pics/1.png",
             "tests/test_pics/2.png", "tests/test_pics/4.png"]
PATCHING = "_showRegionOnScreen.png"
NOT_REPAIR = ["test.png", "tests/test_pic.png"]   # loot screen, open world

SCALES = [1.0, 2 / 3, 4 / 3]   # 1920x1080, 1280x720, 2560x1440


def window(path, scale=1.0):
    img = Image.open(ROOT / path).convert("RGB")
    client = Rect(0, 23, 1920, 1017)
    if scale != 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)),
                         Image.LANCZOS)
        client = Rect(0, round(23 * scale), round(1920 * scale), round(1017 * scale))
    return ImageWindow(np.array(img), client)


def near(p, q, tol):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


# -- coordinate mapping ------------------------------------------------------
def test_reference_window_maps_points_to_themselves():
    win = ImageWindow(np.zeros((1440, 2560, 3), np.uint8),
                      Rect(0, REF_TOP, 2560, REF_HEIGHT))
    for x, y in [(0, 23), (1280, 711), (838, 1252), (2559, 1399)]:
        assert near(win.to_screen(x, y), (x, y), 1)


def test_buttons_map_onto_the_1080p_buttons():
    win = window(HAMMERING[0])
    # Button centres measured in the 1080p frames. BUTTONS come from the live
    # game, where the bar sits ~19 ref px (14 px at 1080p) higher than in
    # these older screenshots; still well inside a ~42 px button radius.
    measured = [(634, 931), (797, 931), (957, 931)]
    for button, (x, y) in zip(BUTTONS, measured):
        sx, sy = win.to_screen(*button)
        assert abs(sx - x) <= 4 and abs(sy - y) <= 16


# -- screen detection, at every scale -----------------------------------------
@pytest.mark.parametrize("scale", SCALES)
def test_active_game_is_detected_by_title(scale):
    for path in HAMMERING:
        bot = RepairBot(window(path, scale))
        assert type(bot.active_game()) is Hammering, path
    bot = RepairBot(window(PATCHING, scale))
    assert type(bot.active_game()) is HullPatching
    for path in NOT_REPAIR:
        assert RepairBot(window(path, scale)).active_game() is None, path


@pytest.mark.parametrize("scale", SCALES)
def test_completed_buttons(scale):
    win = window(HAMMERING[0], scale)
    assert [button_completed(win, b) for b in BUTTONS] == [True, True, True, False, False]
    win = window(PATCHING, scale)
    assert not any(button_completed(win, b) for b in BUTTONS)


@pytest.mark.parametrize("scale", SCALES)
def test_repair_bar_visibility(scale):
    for path in HAMMERING + [PATCHING]:
        assert repair_bar_visible(window(path, scale)), path
    for path in NOT_REPAIR:
        assert not repair_bar_visible(window(path, scale)), path


@pytest.mark.parametrize("scale", SCALES)
def test_find_nails(scale):
    # The 2020 UI in these frames draws a flattened nail higher than the
    # live game does (y ~741 vs 749), so only check the nails still standing.
    expected = [(1210, 683), (1347, 640), (1483, 640), (1621, 640)]
    for path in HAMMERING:
        nails = [n for n in Hammering(window(path, scale)).find_nails() if n.y < 720]
        assert len(nails) == 4, (path, nails)
        for nail, exp in zip(nails, expected):
            assert near(nail, exp, 6), (path, nails)


@pytest.mark.parametrize("scale", SCALES)
def test_target_circle_glow(scale):
    # Centre of the target circle in each frame; it glows in frames 0 and 1.
    circles = {"tests/test_pics/0.png": ((1031, 561), True),
               "tests/test_pics/1.png": ((1033, 561), True),
               "tests/test_pics/2.png": ((1037, 557), False),
               "tests/test_pics/4.png": ((1024, 541), False)}
    for path, (centre, glows) in circles.items():
        game = Hammering(window(path, scale))
        assert game.glowing(Point(*centre)) is glows, path


@pytest.mark.parametrize("scale", SCALES)
def test_no_leaks_on_the_patching_start_screen(scale):
    assert HullPatching(window(PATCHING, scale)).find_leaks() == []


# -- capture stays inside the game window -------------------------------------
MAGENTA = (255, 0, 255)


def framed_window():
    """A 400x300 game area inside a larger screen painted magenta."""
    screen_img = np.zeros((600, 800, 3), np.uint8)
    screen_img[:] = MAGENTA
    screen_img[100:400, 200:600] = (10, 120, 30)
    return ImageWindow(screen_img, Rect(200, 100, 400, 300))


@pytest.mark.parametrize("region", [
    (-500, -500, 4000, 3000),   # covers far more than the game area
    (-300, 600, 400, 200),      # crosses the left edge
    (2400, 1300, 400, 300),     # crosses the bottom-right corner
    (5000, 5000, 50, 50),       # entirely outside
])
def test_grab_never_reads_outside_the_game_area(region):
    win = framed_window()
    img = win.grab(*region)
    assert img.shape[:2] == (win.region_to_screen(*region).height,
                             win.region_to_screen(*region).width)
    assert not (img == MAGENTA).all(axis=-1).any()


def test_grab_inside_the_game_area_is_unchanged():
    win = framed_window()
    assert (win.grab(1000, 500, 200, 200) == (10, 120, 30)).all()


def test_capture_probe_prefers_window_capture(monkeypatch):
    import screen
    blank = lambda *a: np.zeros((10, 10, 3), np.uint8)
    real = lambda *a: np.random.default_rng(0).integers(0, 255, (10, 10, 3), np.uint8)
    win = screen.GameWindow()
    win.hwnd, win.rect = 1, Rect(0, 0, 10, 10)

    monkeypatch.setattr(screen, "capture_bitblt", real)
    assert win._probe_capture() == "bitblt"
    monkeypatch.setattr(screen, "capture_bitblt", blank)
    monkeypatch.setattr(screen, "capture_printwindow", real)
    assert win._probe_capture() == "printwindow"
    monkeypatch.setattr(screen, "capture_printwindow", blank)
    assert win._probe_capture() == "screen"


def test_input_outside_the_game_area_is_dropped(monkeypatch):
    import screen
    win = screen.GameWindow()
    win.hwnd, win.rect = 1, Rect(0, 23, 1920, 1017)
    monkeypatch.setattr(win, "is_foreground", lambda: True)
    assert win._input_target(1280, 700) is not None
    assert win._input_target(-2000, 700) is None
    assert win._input_target(1280, 5000) is None


def test_input_waits_while_the_game_is_in_the_background(monkeypatch):
    import threading
    import screen
    win = screen.GameWindow()
    win.hwnd, win.rect = 1, Rect(0, 23, 1920, 1017)
    monkeypatch.setattr(win, "is_foreground", lambda: False)
    monkeypatch.setattr(win, "refresh", lambda: win.rect)
    screen.reset_stop()
    timer = threading.Timer(0.3, screen.request_stop)
    timer.start()
    try:
        with pytest.raises(screen.StopRequested):
            win._input_target(1280, 700)   # would wait forever without stop
    finally:
        timer.cancel()
        screen.reset_stop()


# -- kill switch -------------------------------------------------------------
import subprocess
import sys
import time

import screen
from hull_bracing import HullBracing


@pytest.fixture
def clean_stop():
    screen.reset_stop()
    yield
    screen._finished.set()   # lets any kill-switch thread exit
    time.sleep(0.05)
    screen.reset_stop()


def key_timeline(*spans):
    """Fake key state: spans of (seconds, held) played back from now."""
    start = time.monotonic()

    def state():
        t = time.monotonic() - start
        for seconds, held in spans:
            if t < seconds:
                return held
            t -= seconds
        return spans[-1][1]
    return state


def test_stop_key_is_noticed_quickly(clean_stop):
    kills = []
    switch = KillSwitchFor(key_timeline((0.1, False), (1, True)), kills, grace=5)
    pressed_at = time.monotonic() + 0.1
    assert screen._stop.wait(1)
    assert time.monotonic() - pressed_at < 0.15     # polled every 20 ms
    screen._finished.set()
    switch._thread.join(1)
    assert kills == []


def test_key_held_at_start_must_be_released_first(clean_stop):
    kills = []
    KillSwitchFor(key_timeline((0.3, True), (0.2, False), (1, True)), kills, grace=5)
    time.sleep(0.25)
    assert not screen._stop.is_set()   # still the press from before start
    assert screen._stop.wait(1)        # the new press after release counts


def test_hard_kill_when_the_bot_ignores_stop(clean_stop):
    kills = []
    KillSwitchFor(key_timeline((0.05, False), (1, True)), kills, grace=0.3)
    time.sleep(0.8)                    # never call _finished.set()
    assert kills == ["killed"]


def test_no_hard_kill_when_the_bot_stops_in_time(clean_stop):
    kills = []
    KillSwitchFor(key_timeline((0.05, False), (1, True)), kills, grace=0.5)
    assert screen._stop.wait(1)
    screen._finished.set()             # the bot shuts down cleanly
    time.sleep(0.8)
    assert kills == []


def KillSwitchFor(key_state, kills, grace):
    return screen.KillSwitch(key_state=key_state, grace=grace,
                             kill=lambda: kills.append("killed")).start()


HUNG_BOT = r"""
import sys, time
sys.path.insert(0, "repair")
import logging; logging.basicConfig(level=logging.INFO)
import screen
start = time.monotonic()
screen.KillSwitch(key_state=lambda: time.monotonic() - start > 0.3, grace=0.5).start()
while True:          # a stuck bot: never checks for a stop
    time.sleep(0.05)
"""


def test_a_hung_bot_process_is_killed():
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", HUNG_BOT], cwd=ROOT,
                          capture_output=True, text=True, timeout=10)
    elapsed = time.monotonic() - started
    assert proc.returncode == 1
    assert "killing it" in proc.stderr
    assert elapsed < 5


def test_solver_stops_mid_search(clean_stop, monkeypatch):
    import hull_bracing
    monkeypatch.setattr(hull_bracing, "STOP_CHECK_EVERY", 1)
    scrambled = [list("RBRB"), list("OOBR"), list("OBRO"), list("OR  ")]
    screen.request_stop()
    with pytest.raises(screen.StopRequested):
        solve(scrambled)


def test_stopping_mid_drag_releases_the_mouse(clean_stop):
    win = window(PATCHING)
    screen.request_stop()
    with pytest.raises(screen.StopRequested):
        HullBracing(win).slide(0, 1)
    assert [a[0] for a in win.actions] == ["move", "down", "up"]


def test_live_session_ends_cleanly_on_ctrl_c(clean_stop, monkeypatch):
    released = []
    monkeypatch.setattr(screen, "release_mouse", lambda: released.append(True))
    monkeypatch.setattr(screen, "KillSwitch",
                        lambda **kw: type("K", (), {"start": lambda s: s})())
    with screen.live_session():
        raise KeyboardInterrupt
    assert released and screen._finished.is_set()


# -- stopping ----------------------------------------------------------------
def test_stop_interrupts_a_running_game():
    """The bot loop must exit promptly when stop is requested mid-game."""
    import threading
    import time
    from screen import StopRequested, request_stop, reset_stop

    bot = RepairBot(window(PATCHING))   # patching is open, no leaks: would loop
    reset_stop()
    timer = threading.Timer(0.5, request_stop)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(StopRequested):
            bot.tick()
    finally:
        timer.cancel()
        reset_stop()
    assert time.monotonic() - started < 2


# -- hull bracing solver -------------------------------------------------------
def apply(state, moves):
    cells = list(state)
    for src, dst in moves:
        assert cells[dst] == " " and cells[src] != " "
        assert abs(src // SIZE - dst // SIZE) + abs(src % SIZE - dst % SIZE) == 1
        cells[dst], cells[src] = cells[src], " "
    return "".join(cells)


def grid_of(state):
    return [list(state[i:i + SIZE]) for i in range(0, SIZE * SIZE, SIZE)]


def test_solved_grid_needs_no_moves():
    assert solve(grid_of("BBBBRRRROO  OO O")) == []


def test_one_move_puzzle():
    # Rows "BBB ", "OOOB", "RRRR", "OO  ": slide the B at index 7 up into 3.
    start = "BBB OOOBRRRROO  "
    moves = solve(grid_of(start))
    assert moves is not None and is_goal(apply(start, moves))
    assert len(moves) <= 3


SOLVED_BOARDS = {
    "red and blue rows": "BBBBRRRROOO  OOO",
    "red row, green column": "GOOORXRRGO  GOO ",
    "tight, 2 gaps": "GOOORXRRGOO GOO ",
}


@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("kind", SOLVED_BOARDS)
def test_scrambled_puzzles_are_solved(kind, seed):
    rng = random.Random(seed)
    state = SOLVED_BOARDS[kind]
    for _ in range(80):  # random legal slides keep the puzzle solvable
        state, _ = rng.choice(list(neighbours(state)))
    moves = solve(grid_of(state))
    assert moves is not None, state
    assert is_goal(apply(state, moves))
    assert len(moves) <= 30


def test_boards_that_do_not_need_two_lines_are_refused():
    # Only red has four pieces (a hidden tile read as a block): misread.
    assert solve(grid_of("RRR OOOOROO BOBB")) is None
