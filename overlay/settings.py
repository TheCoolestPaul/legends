"""Overlay settings: defaults, loading and saving (overlay/settings.json)."""
import copy
import json
import logging
from pathlib import Path

log = logging.getLogger("overlay")

SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

DEFAULTS = {
    "input": "background",          # or "mouse" (TLOPO_INPUT)
    "max_minutes": 0,               # every bot: stop after this long (0: no limit)
    "hotkeys": {"overlay": "F6", "repair": "F7", "potions": "F8"},
    "repair": {"record": False, "debug": False, "max_ships": 0},
    "potions": {"recipes": "", "count": 1, "any_tier": False,
                "record": False, "debug": False, "max_brews": 0},
    "position": None,               # [x, y] of the panel, None: next to the game
    "visible": True,
}


def _merge(defaults, saved):
    """``defaults`` updated with the values in ``saved`` of the right type."""
    out = copy.deepcopy(defaults)
    for key, value in (saved or {}).items():
        if key not in out:
            continue
        if isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        elif out[key] is None or value is None or isinstance(value, type(out[key])):
            out[key] = value
    return out


def load(path=SETTINGS_FILE):
    """Saved settings over the defaults; the defaults if none are saved or
    the file can't be read."""
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    except (OSError, ValueError) as e:
        log.warning("Couldn't read %s (%s); using defaults", path, e)
        saved = {}
    return _merge(DEFAULTS, saved)


def save(settings, path=SETTINGS_FILE):
    Path(path).write_text(json.dumps(settings, indent=2), encoding="utf-8")
