"""A small always-on-top panel over the game for starting and stopping bots.

Run from the repo folder:  .venv\\Scripts\\python -m overlay
(or double-click overlay.pyw). Default hotkeys: F6 shows/hides the panel,
F7 starts/stops the repair bot, F8 the potions bot. Backtick still stops a
running bot. Settings are saved to overlay/settings.json.
"""
import ctypes
import logging
import sys
import tkinter as tk
from pathlib import Path

if __package__ in (None, ""):           # run as a file: make "overlay" importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from overlay import hotkeys, settings as settings_mod      # noqa: E402
from overlay.runner import BOTS, KILL_AFTER, BotProcess    # noqa: E402

log = logging.getLogger("overlay")

TICK_MS = 40
QUIT_POLL_MS = 100
# On quit, wait a little past the runner's own terminate deadline before
# killing whatever is still running.
QUIT_TRIES = int((KILL_AFTER + 1.0) * 1000 / QUIT_POLL_MS)
BG, PANEL, FG, DIM = "#16191d", "#20252b", "#e8e8e8", "#8b949e"
STATE_COLOURS = {"running": "#3fb950", "stopping": "#d29922", "stopped": "#6e7681"}
FONT = ("Segoe UI", 10)
SMALL = ("Segoe UI", 9)
BOLD = ("Segoe UI Semibold", 10)
WINDOW_TITLES = ("The Legend of Pirates Online [BETA]", "The Legend of Pirates Online")


def make_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def default_position():
    """Left edge of the game, below the health bars; (40, 260) without it."""
    try:
        import win32gui
        for title in WINDOW_TITLES:
            hwnd = win32gui.FindWindow(None, title)
            if hwnd:
                left, top = win32gui.ClientToScreen(hwnd, (0, 0))
                return left + 16, top + 260
    except Exception:
        pass
    return 40, 260


class Overlay:
    def __init__(self, root, settings, bots=None, poller=None, save=settings_mod.save):
        self.root = root
        self.settings = settings
        self.save_settings = save
        self.bots = bots or {name: BotProcess(name) for name in BOTS}
        self.poller = poller or hotkeys.HotkeyPoller()
        self.pending_start = None       # bot to start once the others have stopped
        self.capturing = None           # hotkey action waiting for a key press
        self.rows = {}
        self.vars = {}
        self._drag = None
        self._build()
        self.bind_hotkeys()
        if not settings.get("visible", True):
            self.root.withdraw()
        self.root.after(TICK_MS, self.tick)

    # -- window ---------------------------------------------------------------
    def _build(self):
        r = self.root
        r.title("Legends bots")
        r.overrideredirect(True)                 # no title bar, not in the taskbar
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.94)
        r.configure(bg=BG, highlightthickness=1, highlightbackground="#30363d")
        x, y = self.settings.get("position") or default_position()
        r.geometry("+%d+%d" % (x, y))

        head = tk.Frame(r, bg=BG)
        head.pack(fill="x", padx=8, pady=(6, 2))
        title = tk.Label(head, text="Legends bots", bg=BG, fg=FG, font=BOLD)
        title.pack(side="left")
        for widget in (head, title):          # drag the panel by its header
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
        self._button(head, "✕", self.quit, width=2).pack(side="right")
        self._button(head, "–", self.toggle_visible, width=2).pack(side="right", padx=2)
        self.gear = self._button(head, "⚙", self.toggle_settings, width=2)
        self.gear.pack(side="right")

        for name in self.bots:
            self._bot_row(name)

        self.hint = tk.Label(r, bg=BG, fg=DIM, font=SMALL, justify="left")
        self.hint.pack(fill="x", padx=8, pady=(0, 6))
        self.settings_frame = tk.Frame(r, bg=PANEL)
        self._build_settings(self.settings_frame)
        self._refresh_hint()

    def _button(self, parent, text, command, width=None):
        return tk.Button(parent, text=text, command=command, width=width, bg=PANEL, fg=FG,
                         activebackground="#30363d", activeforeground=FG, relief="flat",
                         font=FONT, bd=0, padx=6, cursor="hand2")

    def _bot_row(self, name):
        bot = self.bots[name]
        frame = tk.Frame(self.root, bg=PANEL)
        frame.pack(fill="x", padx=8, pady=3)
        top = tk.Frame(frame, bg=PANEL)
        top.pack(fill="x", padx=6, pady=(4, 0))
        dot = tk.Label(top, text="●", bg=PANEL, font=FONT)
        dot.pack(side="left")
        tk.Label(top, text=bot.title, bg=PANEL, fg=FG, font=BOLD, width=8,
                 anchor="w").pack(side="left", padx=(2, 0))
        state = tk.Label(top, bg=PANEL, fg=DIM, font=SMALL, width=14, anchor="w")
        state.pack(side="left")
        button = self._button(top, "Start", lambda n=name: self.toggle_bot(n), width=6)
        button.pack(side="right")
        key = tk.Label(top, bg=PANEL, fg=DIM, font=SMALL)
        key.pack(side="right", padx=6)
        status = tk.Label(frame, bg=PANEL, fg=DIM, font=SMALL, anchor="w", justify="left",
                          wraplength=330)
        status.pack(fill="x", padx=6, pady=(0, 4))
        self.rows[name] = {"dot": dot, "state": state, "button": button, "key": key,
                           "status": status}

    def _build_settings(self, f):
        s = self.settings

        def section(text):
            tk.Label(f, text=text, bg=PANEL, fg=FG, font=BOLD, anchor="w").pack(
                fill="x", padx=8, pady=(8, 2))

        def row():
            fr = tk.Frame(f, bg=PANEL)
            fr.pack(fill="x", padx=14, pady=1)
            return fr

        def label(parent, text):
            tk.Label(parent, text=text, bg=PANEL, fg=DIM, font=SMALL).pack(side="left")

        def check(parent, text, path):
            var = tk.BooleanVar(value=self._get(path))
            var.trace_add("write", lambda *_: self._set(path, var.get()))
            tk.Checkbutton(parent, text=text, variable=var, bg=PANEL, fg=FG, font=SMALL,
                           selectcolor=BG, activebackground=PANEL, activeforeground=FG,
                           highlightthickness=0, bd=0).pack(side="left", padx=(0, 8))
            self.vars[path] = var

        def number(parent, path, width=5, kind=int, minimum=0):
            var = tk.StringVar(value=str(self._get(path)))

            def changed(*_):
                try:
                    value = kind(var.get())
                except ValueError:
                    return
                if value >= minimum:
                    self._set(path, value)
            var.trace_add("write", changed)
            tk.Spinbox(parent, from_=minimum, to=9999, textvariable=var, width=width,
                       bg=BG, fg=FG, buttonbackground=PANEL, insertbackground=FG,
                       relief="flat", font=SMALL).pack(side="left", padx=4)
            self.vars[path] = var

        section("Input")
        fr = row()
        mode = tk.StringVar(value=s["input"])
        mode.trace_add("write", lambda *_: self._set(("input",), mode.get()))
        for value, text in (("background", "Background (game can be behind)"),
                            ("mouse", "Real mouse")):
            tk.Radiobutton(fr, text=text, value=value, variable=mode, bg=PANEL, fg=FG,
                           font=SMALL, selectcolor=BG, activebackground=PANEL,
                           activeforeground=FG, highlightthickness=0).pack(side="left",
                                                                           padx=(0, 8))
        self.vars[("input",)] = mode

        section("Stop conditions")
        fr = row()
        label(fr, "Any bot: stop after")
        number(fr, ("max_minutes",), kind=float)
        label(fr, "minutes (0 = no limit)")
        fr = row()
        label(fr, "Repair: stop after")
        number(fr, ("repair", "max_ships"))
        label(fr, "ships")
        fr = row()
        label(fr, "Potions: stop after")
        number(fr, ("potions", "max_brews"))
        label(fr, "brews (when no recipes are set)")

        section("Repair")
        fr = row()
        check(fr, "Record frames", ("repair", "record"))
        check(fr, "Debug log", ("repair", "debug"))

        section("Potions")
        fr = row()
        label(fr, "Recipes")
        var = tk.StringVar(value=s["potions"]["recipes"])
        var.trace_add("write", lambda *_: self._set(("potions", "recipes"), var.get()))
        tk.Entry(fr, textvariable=var, width=26, bg=BG, fg=FG, insertbackground=FG,
                 relief="flat", font=SMALL).pack(side="left", padx=4)
        self.vars[("potions", "recipes")] = var
        label(fr, "x")
        number(fr, ("potions", "count"), width=3, minimum=1)
        fr = row()
        label(fr, "Empty: keep brewing the open recipe. Names or list rows, comma-separated.")
        fr = row()
        check(fr, "Fill up all potions", ("potions", "fill"))
        label(fr, "(every recipe until maxed out, then the first one)")
        fr = row()
        check(fr, "Any tier", ("potions", "any_tier"))
        check(fr, "Record frames", ("potions", "record"))
        check(fr, "Debug log", ("potions", "debug"))

        section("Hotkeys")
        self.key_buttons = {}
        for action, text in (("overlay", "Show/hide panel"), ("repair", "Repair on/off"),
                             ("potions", "Potions on/off")):
            fr = row()
            tk.Label(fr, text=text, bg=PANEL, fg=DIM, font=SMALL, width=16,
                     anchor="w").pack(side="left")
            button = self._button(fr, s["hotkeys"][action],
                                  lambda a=action: self.capture_key(a), width=10)
            button.configure(bg=BG, font=SMALL)           # a field to click and set
            button.pack(side="left")
            self.key_buttons[action] = button
        fr = row()
        label(fr, "Backtick always stops a running bot. Settings apply on the next start.")
        tk.Frame(f, bg=PANEL, height=6).pack()

    # -- settings ---------------------------------------------------------------
    def _get(self, path):
        value = self.settings
        for key in path:
            value = value[key]
        return value

    def _set(self, path, value):
        target = self.settings
        for key in path[:-1]:
            target = target[key]
        if target.get(path[-1]) == value:
            return
        target[path[-1]] = value
        self._save()

    def _save(self):
        try:
            self.save_settings(self.settings)
        except OSError as e:
            log.warning("Couldn't save settings: %s", e)

    # -- hotkeys ---------------------------------------------------------------
    def bind_hotkeys(self):
        keys = self.settings["hotkeys"]
        bindings = {keys["overlay"]: self.toggle_visible}
        for name in self.bots:
            bindings[keys[name]] = lambda n=name: self.toggle_bot(n)
        self.poller.bind(bindings)
        for name, row in self.rows.items():
            row["key"].configure(text=keys[name])
        self._refresh_hint()

    def capture_key(self, action):
        """Wait for the next key press in the panel and bind it to ``action``."""
        self.capturing = action
        self.key_buttons[action].configure(text="press a key…")
        self.root.focus_force()
        self.root.bind("<Key>", self._captured)

    def _captured(self, event):
        action, self.capturing = self.capturing, None
        self.root.unbind("<Key>")
        if action is None:
            return
        name = hotkeys.from_tk(event.keysym)
        keys = self.settings["hotkeys"]
        if event.keysym == "Escape" or name is None:
            name = keys[action]                       # unbindable: keep the old one
        for other, key in keys.items():               # swap if already used
            if other != action and key == name:
                keys[other] = keys[action]
                self.key_buttons[other].configure(text=keys[other])
        keys[action] = name
        self.key_buttons[action].configure(text=name)
        self._save()
        self.bind_hotkeys()

    # -- bots ---------------------------------------------------------------------
    def toggle_bot(self, name):
        bot = self.bots[name]
        if bot.running or self.pending_start == name:
            self.pending_start = None
            bot.stop()
            return
        # Both bots drive the same game window: one at a time.
        others = [b for n, b in self.bots.items() if n != name and b.running]
        for other in others:
            other.stop()
        if others:
            self.pending_start = name
        else:
            bot.start(self.settings)

    def tick(self):
        try:
            if self.capturing is None:
                self.poller.poll()
            for name, bot in self.bots.items():
                for line in bot.poll():
                    print("[%s] %s" % (name, line), flush=True)   # the bots' logs, if run in a console
            if self.pending_start and not any(b.running for b in self.bots.values()):
                name, self.pending_start = self.pending_start, None
                self.bots[name].start(self.settings)
            self._refresh_rows()
        except Exception:
            log.exception("Overlay tick failed")
        self.root.after(TICK_MS, self.tick)

    def _refresh_rows(self):
        for name, bot in self.bots.items():
            row = self.rows[name]
            state = bot.state
            if self.pending_start == name:
                state_text = "waiting…"
            elif state == "running":
                unit = BOTS[name]["unit"]
                state_text = "running · %d %s" % (bot.count, unit)
            else:
                state_text = state
            row["dot"].configure(fg=STATE_COLOURS.get(state, DIM))
            row["state"].configure(text=state_text)
            row["button"].configure(text="Stop" if bot.running or self.pending_start == name
                                    else "Start")
            row["status"].configure(text=bot.status or "—")

    # -- panel --------------------------------------------------------------------
    def _refresh_hint(self):
        keys = self.settings["hotkeys"]
        self.hint.configure(text="%s hides this panel · ` stops a bot" % keys["overlay"])

    def toggle_visible(self):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.settings["visible"] = True
        else:
            self.root.withdraw()
            self.settings["visible"] = False
        self._save()

    def toggle_settings(self):
        if self.settings_frame.winfo_ismapped():
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(fill="x", padx=8, pady=(0, 8))

    def _drag_start(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        if self._drag:
            self.root.geometry("+%d+%d" % (event.x_root - self._drag[0],
                                           event.y_root - self._drag[1]))

    def _drag_end(self, _event):
        if self._drag:
            self._drag = None
            self.settings["position"] = [self.root.winfo_x(), self.root.winfo_y()]
            self._save()

    def quit(self):
        """Stop any running bot, wait for it to exit, then close. A bot still
        running when the wait runs out is terminated first, so closing the
        panel never leaves a bot playing with nothing to stop it."""
        for bot in self.bots.values():
            bot.stop()
        self.pending_start = None
        self._quit_wait(QUIT_TRIES)

    def _quit_wait(self, tries):
        for bot in self.bots.values():
            bot.poll()
        running = [b for b in self.bots.values() if b.running]
        if tries <= 0 or not running:
            for bot in running:
                log.warning("%s bot didn't stop; terminating it", bot.title)
                bot.kill()
            self.root.destroy()
            return
        self.root.after(QUIT_POLL_MS, lambda: self._quit_wait(tries - 1))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    make_dpi_aware()
    root = tk.Tk()
    Overlay(root, settings_mod.load())
    root.mainloop()


if __name__ == "__main__":
    main()
