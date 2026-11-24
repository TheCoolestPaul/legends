"""Global hotkeys, polled like the bots' kill switch (no keyboard hook).

Backtick stops a running bot from inside the bot itself, and F9 saves a
debug frame there, so neither can be bound here.
"""
import ctypes
import sys

NAMES = {}
NAMES.update({"F%d" % n: 0x6F + n for n in range(1, 13)})          # F1-F12
NAMES.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
NAMES.update({str(d): 0x30 + d for d in range(10)})
NAMES.update({"Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
              "PageUp": 0x21, "PageDown": 0x22, "Pause": 0x13, "ScrollLock": 0x91})
for n in range(10):
    NAMES["Num%d" % n] = 0x60 + n
RESERVED = {"F9": "saves a debug frame in the bots"}  # backtick has no name here

# Tk keysyms that differ from the names above.
_TK = {"Prior": "PageUp", "Next": "PageDown", "Scroll_Lock": "ScrollLock"}
_TK.update({"KP_%d" % n: "Num%d" % n for n in range(10)})


def from_tk(keysym):
    """A Tk key event's keysym as a hotkey name, or None if not bindable."""
    name = _TK.get(keysym, keysym)
    if len(name) == 1:
        name = name.upper()
    return name if name in NAMES and name not in RESERVED else None


def key_down(name):
    if sys.platform != "win32" or name not in NAMES:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(NAMES[name]) & 0x8000)


class HotkeyPoller:
    """Call ``bindings[name]()`` when hotkey ``name`` goes down.

    ``poll()`` is meant to run every few tens of ms on the UI thread (Tk's
    ``after``), so the callbacks run there too. Keys already held when the
    poller starts, or when a binding changes, must be released first.
    """

    def __init__(self, key_state=key_down):
        self.key_state = key_state
        self.bindings = {}          # key name -> callback
        self._down = {}

    def bind(self, bindings):
        self.bindings = dict(bindings)
        self._down = {name: True for name in self.bindings}   # armed on release

    def poll(self):
        for name, callback in list(self.bindings.items()):
            down = self.key_state(name)
            if down and not self._down.get(name, False):
                callback()
            self._down[name] = down
