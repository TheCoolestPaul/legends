"""Tests for debug snapshots, the replay tool and the hull bracing tile reader."""
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import replay
import screen
import snapshots
from hammering import Hammering
from hull_bracing import (SIZE, TILE_SAMPLE_H, TILE_SAMPLE_W, HullBracing,
                          classify_tile, tile_center)
from repair_bind import RepairBot
from screen import REF_HEIGHT, REF_TOP, ImageWindow, Rect

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "bracing"
CLIENT_1080 = (0, 23, 1920, 1017)


def tile(name):
    return np.array(Image.open(FIXTURES / (name + ".png")).convert("RGB"))


# -- hull bracing tile reader ------------------------------------------------
@pytest.mark.parametrize("name, expected", [
    ("blue_line", "B"),
    ("red_line", "R"),
    ("green_line", "G"),          # vertical green stripe
    ("red_and_green_line", "X"),  # cross: red row piece and green column piece
    ("blockade", "O"),
])
def test_tile_samples_from_the_game(name, expected):
    assert classify_tile(tile(name)) == expected


def test_cross_tile_centre_is_dark():
    """Why the reader samples the band: the old centre pixel read 'empty'."""
    img = tile("red_and_green_line")
    h, w = img.shape[:2]
    assert img[h // 2, w // 2].max() < 15


def test_empty_slot_reads_as_empty():
    assert classify_tile(np.full((63, 65, 3), 8, np.uint8)) == " "


def test_unknown_colour_reads_as_unknown():
    assert classify_tile(np.full((63, 65, 3), (40, 200, 230), np.uint8)) == "?"


# A board laid out from the samples: X = cross tile, G = green-stripe tile.
LAYOUT = ["B R ",
          "XGO ",
          "BBRB",
          "RR  "]
TILE_FOR = {"B": "blue_line", "R": "red_line", "X": "red_and_green_line",
            "G": "green_line", "O": "blockade"}
EXPECTED = ["B R ", "XGO ", "BBRB", "RR  "]


def board_window(scale=1.0):
    """A reference-size game area with the LAYOUT board painted on it."""
    img = np.full((REF_TOP + REF_HEIGHT, 2560, 3), 8, np.uint8)   # dark tray
    for r in range(SIZE):
        for c in range(SIZE):
            kind = LAYOUT[r][c]
            if kind == " ":
                continue
            t = tile(TILE_FOR[kind])
            cx, cy = tile_center(r, c)
            h, w = t.shape[:2]
            img[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w] = t
    client = Rect(0, REF_TOP, 2560, REF_HEIGHT)
    if scale != 1.0:
        img = np.array(Image.fromarray(img).resize(
            (round(img.shape[1] * scale), round(img.shape[0] * scale)), Image.LANCZOS))
        client = Rect(0, round(REF_TOP * scale), round(2560 * scale),
                      round(REF_HEIGHT * scale))
    return ImageWindow(img, client)


@pytest.mark.parametrize("scale", [1.0, 0.75, 0.5])
def test_read_grid(scale):
    grid = HullBracing(board_window(scale)).read_grid()
    assert ["".join(row) for row in grid] == EXPECTED


def test_tile_sample_size_matches_the_samples():
    for name in TILE_FOR.values():
        h, w = tile(name).shape[:2]
        assert abs(w - TILE_SAMPLE_W) <= 8 and abs(h - TILE_SAMPLE_H) <= 8


# -- snapshots ---------------------------------------------------------------
def frame_window():
    img = np.array(Image.open(ROOT / "tests/test_pics/0.png").convert("RGB"))
    return ImageWindow(img, Rect(*CLIENT_1080))


def test_snapshot_saves_frame_and_info(snapshot_dir):
    win = frame_window()
    png = snapshots.save(win, "timeout", game=Hammering(win), info={"nails": 4})
    assert png and png.exists() and png.parent == snapshot_dir
    assert Image.open(png).size == (1920, 1017)          # game area only
    meta = json.loads(png.with_suffix(".json").read_text())
    assert meta["reason"] == "timeout" and meta["game"] == "hammering"
    assert meta["info"] == {"nails": 4}
    assert meta["client"]["height"] == 1017


def test_snapshots_are_rate_limited_per_reason():
    win = frame_window()
    assert snapshots.save(win, "no_nails")
    assert snapshots.save(win, "no_nails") is None
    assert snapshots.save(win, "timeout")                 # other reason: allowed
    assert snapshots.save(win, "no_nails", force=True)    # F9 is never limited


def test_old_snapshots_are_pruned(monkeypatch, snapshot_dir):
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOTS", 3)
    win = frame_window()
    for i in range(5):
        snapshots.save(win, "r%d" % i)
        time.sleep(0.002)                                 # distinct timestamps
    assert len(list(snapshot_dir.glob("*.png"))) == 3
    assert len(list(snapshot_dir.glob("*.json"))) == 3


def test_snapshot_failure_never_raises():
    win = frame_window()
    win._grab_rect = lambda rect: (_ for _ in ()).throw(OSError("capture failed"))
    assert snapshots.save(win, "error") is None


def test_f9_requests_a_snapshot_on_each_press():
    screen.reset_stop()
    presses = []
    start = time.monotonic()

    def f9():   # down at 0.1-0.2 s and 0.3-0.4 s
        t = time.monotonic() - start
        return 0.1 < t < 0.2 or 0.3 < t < 0.4
    switch = screen.KillSwitch(key_state=lambda: False, kill=lambda: None,
                               hotkeys={f9: lambda: presses.append(1)}).start()
    time.sleep(0.6)
    screen._finished.set()
    switch._thread.join(1)
    screen.reset_stop()
    assert presses == [1, 1]


def test_requested_snapshot_is_taken_at_next_check(snapshot_dir):
    snapshots.request()
    snapshots.take_requested(frame_window())
    assert len(list(snapshot_dir.glob("*_manual.png"))) == 1
    snapshots.take_requested(frame_window())              # request was consumed
    assert len(list(snapshot_dir.glob("*_manual.png"))) == 1


def test_a_crashing_game_is_snapshotted_and_the_run_continues(monkeypatch, snapshot_dir):
    bot = RepairBot(frame_window())
    monkeypatch.setattr(Hammering, "play", lambda self: 1 / 0)
    screen.reset_stop()
    bot.tick()                                            # must not raise
    assert len(list(snapshot_dir.glob("*_error_hammering.png"))) == 1


# -- replay tool -------------------------------------------------------------
def test_replay_describes_a_hammering_frame():
    out = replay.describe(replay.load(ROOT / "tests/test_pics/0.png", CLIENT_1080))
    assert out["active_game"] == "hammering"
    assert out["buttons_done"] == [True, True, True, False, False]
    assert len(standing(out["nails"])) == 4


def test_replay_reads_a_saved_snapshot(snapshot_dir):
    png = snapshots.save(frame_window(), "manual", force=True)
    out = replay.describe(replay.load(png))               # game area only
    assert out["active_game"] == "hammering"
    assert len(standing(out["nails"])) == 4


def standing(nails):
    """Nails still standing; the 2020 frames draw flattened nails higher
    than the live game, so the flush cut-off doesn't apply to them."""
    return [n for n in nails if n[1] < 720]
