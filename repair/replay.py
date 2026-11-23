"""Run every detector on saved frames and print what the bots would see.

    python repair/replay.py debug_frames/<frame>.png [more.png ...]

Frames saved by the bots are the game area only. For a full-screen
screenshot, say where the game area is:  --client LEFT,TOP,WIDTH,HEIGHT
(e.g. --client 0,23,1920,1017 for a maximised 1080p window).
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image

from repair_bind import RepairBot
from repair_mini_game import BUTTONS, TITLE_REGION, button_completed, repair_bar_visible
from screen import ImageWindow, Rect


def load(path, client=None):
    img = np.array(Image.open(path).convert("RGB"))
    rect = Rect(*client) if client else Rect(0, 0, img.shape[1], img.shape[0])
    return ImageWindow(img, rect)


def describe(win):
    """What every detector sees on this frame, as a dict."""
    bot = RepairBot(win)
    title = win.grab(*TITLE_REGION)
    games = {g.name: g for g in bot.games}
    active = bot.active_game()
    out = {
        "scale": round(win.scale, 3),
        "active_game": active.name if active else None,
        "title_scores": {name: round(g.title_score(title), 3) for name, g in games.items()},
        "repair_bar_visible": repair_bar_visible(win),
        "buttons_done": [button_completed(win, b) for b in BUTTONS],
    }
    name = active.name if active else None
    if name == "hammering":
        out["nails"] = [tuple(n) for n in games[name].find_nails()]
    elif name == "hull_patching":
        out["leaks"] = len(games[name].find_leaks())
    elif name == "hull_scrubbing":
        out["dirt_spots"] = len(games[name].find_dirt())
    elif name == "plank_sawing":
        plank = games[name].identify_plank()
        out["plank"] = plank
        out.update(games[name].info)
    elif name == "hull_bracing":
        out["grid"] = ["".join(row) for row in games[name].read_grid()]
    elif name == "bilge_pumping":
        y = games[name].rod_y()
        out["rod_y"] = None if y is None else round(y)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("frames", nargs="+")
    parser.add_argument("--client", type=lambda s: tuple(int(v) for v in s.split(",")),
                        help="game area inside a full-screen screenshot")
    args = parser.parse_args(argv)
    for path in args.frames:
        print("==", path)
        print(json.dumps(describe(load(path, args.client)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
