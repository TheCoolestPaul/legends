"""Save game-window frames for debugging and for building test fixtures.

A snapshot is the whole game area as a PNG, plus a JSON file next to it with
why it was taken and what the bot saw (scores, grid, ...). Snapshots are
taken automatically when something goes wrong, when F9 is pressed, and on
every game step while recording (``repair_bind.py --record``).

Feed one back through the detectors with:  python repair/replay.py <png>
"""
import json
import logging
import threading
import time
from datetime import datetime

from PIL import Image

from screen import REPO_ROOT

log = logging.getLogger("repair")

SNAPSHOT_DIR = REPO_ROOT / "debug_frames"
MAX_SNAPSHOTS = 300          # oldest are deleted beyond this
MIN_INTERVAL = 5.0           # seconds between two snapshots with the same reason
RECORD_INTERVAL = 1.0        # seconds between recorded frames of one game

VK_F9 = 0x78

_last_saved = {}
_requested = threading.Event()
recording = False


def request():
    """Ask for a snapshot at the bot's next check (the F9 key calls this)."""
    _requested.set()


def take_requested(win, game=None):
    """Save a snapshot if F9 was pressed since the last check."""
    if _requested.is_set():
        _requested.clear()
        save(win, "manual", game=game, force=True)


def record(win, game, info=None):
    """While recording, save about one frame per second of each game."""
    if recording:
        save(win, "record", game=game, info=info, min_interval=RECORD_INTERVAL)


def save(win, reason, game=None, info=None, force=False, min_interval=MIN_INTERVAL):
    """Save the game area and what the bot saw. Never raises.

    Returns the PNG path, or None if rate-limited or saving failed.
    """
    key = (reason, getattr(game, "name", game))
    now = time.monotonic()
    if not force and now - _last_saved.get(key, -1e9) < min_interval:
        return None
    _last_saved[key] = now
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        frame = win._grab_rect(win.rect)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        name = "%s_%s%s" % (stamp, reason, "_" + key[1] if key[1] else "")
        png = SNAPSHOT_DIR / (name + ".png")
        Image.fromarray(frame).save(png)
        meta = {
            "reason": reason,
            "game": key[1],
            "time": datetime.now().isoformat(timespec="seconds"),
            "client": {"left": win.rect.left, "top": win.rect.top,
                       "width": win.rect.width, "height": win.rect.height},
            "scale": round(win.scale, 4),
            "capture": getattr(win, "capture_mode", None),
            "info": info or {},
        }
        png.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
        _prune()
        log.info("Saved debug frame %s (%s)", png.name, reason)
        return png
    except Exception:
        log.exception("Could not save a debug frame")
        return None


def _prune():
    pngs = sorted(SNAPSHOT_DIR.glob("*.png"))
    for old in pngs[:max(0, len(pngs) - MAX_SNAPSHOTS)]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)


def reset():
    """Forget rate limits and pending requests (for tests)."""
    _last_saved.clear()
    _requested.clear()
