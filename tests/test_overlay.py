"""The bot overlay: settings, hotkeys, the bot runner and the panel's logic."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from overlay import hotkeys, runner, settings  # noqa: E402


@pytest.fixture(autouse=True)
def _run_logs_in_tmp(monkeypatch, tmp_path):
    """Bots started in tests never write to the real logs/ folder."""
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")


# -- settings -----------------------------------------------------------------
def test_missing_settings_file_gives_the_defaults(tmp_path):
    assert settings.load(tmp_path / "none.json") == settings.DEFAULTS


def test_saved_settings_override_defaults_and_bad_values_are_ignored(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"input": "mouse", "max_minutes": "lots",
                                "repair": {"max_ships": 5}, "unknown": 1}))
    s = settings.load(path)
    assert s["input"] == "mouse"
    assert s["max_minutes"] == 0                      # wrong type: default kept
    assert s["repair"] == {"record": False, "debug": False, "max_ships": 5}
    assert "unknown" not in s
    assert s["hotkeys"] == settings.DEFAULTS["hotkeys"]


def test_corrupt_settings_file_gives_the_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert settings.load(path) == settings.DEFAULTS


def test_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = settings.load(path)
    s["hotkeys"]["repair"] = "F2"
    s["position"] = [10, 20]
    settings.save(s, path)
    assert settings.load(path) == s


def test_defaults_are_not_shared_between_loads(tmp_path):
    s = settings.load(tmp_path / "none.json")
    s["repair"]["max_ships"] = 9
    assert settings.DEFAULTS["repair"]["max_ships"] == 0


# -- hotkeys ------------------------------------------------------------------
def test_tk_key_names():
    assert hotkeys.from_tk("F7") == "F7"
    assert hotkeys.from_tk("r") == "R"
    assert hotkeys.from_tk("Prior") == "PageUp"
    assert hotkeys.from_tk("KP_5") == "Num5"
    assert hotkeys.from_tk("F9") is None             # the bots' debug-frame key
    assert hotkeys.from_tk("grave") is None          # backtick: the stop key
    assert hotkeys.from_tk("Shift_L") is None


def test_hotkey_fires_once_per_press_and_not_for_a_key_already_held():
    held = {"F7": True}
    fired = []
    poller = hotkeys.HotkeyPoller(key_state=lambda name: held.get(name, False))
    poller.bind({"F7": lambda: fired.append("F7")})
    poller.poll()                                    # held since before binding
    assert fired == []
    held["F7"] = False
    poller.poll()
    held["F7"] = True
    poller.poll()
    poller.poll()                                    # still held: no repeat
    assert fired == ["F7"]


# -- commands -------------------------------------------------------------------
def opts(**changes):
    s = settings.load(Path("no-such-file.json"))
    for path, value in changes.items():
        target = s
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return s


def test_repair_command_with_limits():
    cmd = runner.build_command("repair", opts(max_minutes=30, repair__max_ships=4,
                                              repair__record=True), python="py")
    assert cmd[:2] == ["py", "-u"] and cmd[2].endswith("repair_bind.py")
    assert cmd[3:] == ["--record", "--max-minutes", "30", "--max-ships", "4"]


def test_plain_commands_have_no_options():
    assert runner.build_command("repair", opts(), python="py")[3:] == []
    assert runner.build_command("potions", opts(), python="py")[3:] == []


def test_potions_command_recipes_or_brew_limit():
    cmd = runner.build_command("potions", opts(potions__recipes="Deadeye I, 8",
                                               potions__count=3, potions__max_brews=5,
                                               potions__any_tier=True), python="py")
    assert cmd[3:] == ["--recipes", "Deadeye I, 8", "--count", "3", "--any-tier"]
    cmd = runner.build_command("potions", opts(potions__max_brews=5), python="py")
    assert cmd[3:] == ["--max-brews", "5"]


def test_env_carries_input_mode_and_stop_file(tmp_path):
    env = runner.build_env(opts(input="mouse"), tmp_path / "stop", base={"PATH": "x"})
    assert env == {"PATH": "x", "TLOPO_INPUT": "mouse",
                   "TLOPO_STOP_FILE": str(tmp_path / "stop"), "PYTHONUNBUFFERED": "1"}


def test_bots_run_with_the_project_venv_when_there_is_one(tmp_path):
    venv = tmp_path / "python.exe"
    venv.write_text("")
    assert runner.python_exe("C:/other/pythonw.exe", venv=venv) == str(venv)
    assert runner.python_exe("C:/other/python.exe", venv=tmp_path / "none") == \
        str(Path("C:/other/python.exe"))


# -- running a bot ------------------------------------------------------------------
FAKE_BOT = r"""
import os, sys, time
print("16:00:00 INFO Press ` to stop (or Ctrl+C).", flush=True)
print("16:00:01 DEBUG not shown", flush=True)
print("16:00:02 INFO Ship repaired (1 so far).", flush=True)
print("16:00:03 INFO Ship repaired (2 so far).", flush=True)
stop = os.environ.get("TLOPO_STOP_FILE")
mode = sys.argv[1]
if mode == "wait_for_stop":
    while not os.path.exists(stop):
        time.sleep(0.02)
    print("16:00:09 WARNING Stop requested from the overlay; stopping.", flush=True)
    print("16:00:09 INFO Stopped.", flush=True)
elif mode == "hang":
    time.sleep(30)
elif mode == "crash":
    raise SystemExit("boom")
"""


def fake_popen(script, mode):
    def popen(cmd, **kwargs):
        return subprocess.Popen([sys.executable, "-c", script, mode], **kwargs)
    return popen


def poll_until(bot, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bot.poll()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_a_bot_is_followed_counted_and_stopped_through_its_stop_file():
    bot = runner.BotProcess("repair", popen=fake_popen(FAKE_BOT, "wait_for_stop"))
    bot.start(opts())
    assert bot.state == "running" and not bot.stop_file.exists()
    assert poll_until(bot, lambda: bot.count == 2)
    assert bot.status == "Ship repaired (2 so far)."    # DEBUG lines aren't shown
    bot.stop()
    assert bot.state == "stopping" and bot.stop_file.exists()
    assert poll_until(bot, lambda: bot.state == "stopped")
    assert bot.exit_code == 0 and bot.status == "Stopped."
    assert not bot.stop_file.exists()


def test_a_bot_that_ignores_the_stop_is_terminated():
    clock = [0.0]
    bot = runner.BotProcess("repair", popen=fake_popen(FAKE_BOT, "hang"),
                            clock=lambda: clock[0])
    bot.start(opts())
    assert poll_until(bot, lambda: bot.count == 2)
    bot.stop()
    clock[0] = runner.KILL_AFTER + 1
    assert poll_until(bot, lambda: bot.state == "stopped")
    assert bot.status.startswith("Didn't stop")


def test_kill_terminates_a_hung_bot_at_once():
    bot = runner.BotProcess("repair", popen=fake_popen(FAKE_BOT, "hang"))
    bot.start(opts())
    assert poll_until(bot, lambda: bot.count == 2)
    bot.kill()
    assert bot.proc.poll() is not None
    assert poll_until(bot, lambda: bot.state == "stopped")
    bot.kill()                                       # already gone: no error


def test_a_crash_shows_the_exit_code():
    bot = runner.BotProcess("repair", popen=fake_popen(FAKE_BOT, "crash"))
    bot.start(opts())
    assert poll_until(bot, lambda: bot.state == "stopped")
    assert bot.exit_code == 1
    assert bot.status.startswith("Exited (code 1): ") and "boom" in bot.status


def test_brews_are_counted():
    bot = runner.BotProcess("potions", popen=None)
    bot._take("16:00:00 INFO Brewed 3 so far (this one 40s, average 42s).")
    assert bot.count == 3


# -- the panel's logic (with stand-in bots) -------------------------------------
class StubBot:
    def __init__(self, name):
        self.title = name.title()
        self.state = "stopped"
        self.status = ""
        self.count = 0
        self.started = 0

    @property
    def running(self):
        return self.state != "stopped"

    def start(self, _settings):
        self.state = "running"
        self.started += 1

    def stop(self):
        if self.running:
            self.state = "stopping"

    def poll(self):
        return []

    def kill(self):
        self.state = "stopped"
        self.killed = True


@pytest.fixture(scope="module")
def tk_root():
    """One Tk interpreter for the module: creating a fresh Tk() per test
    fails now and then on Windows."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip("no display (%s)" % e)
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def panel(tmp_path, tk_root):
    import tkinter as tk
    from overlay.app import Overlay
    root = tk.Toplevel(tk_root)
    root.withdraw()
    saved = []
    bots = {"repair": StubBot("repair"), "potions": StubBot("potions")}
    keys = {"F7": False}
    poller = hotkeys.HotkeyPoller(key_state=lambda name: keys.get(name, False))
    s = settings.load(tmp_path / "none.json")
    s["position"] = [0, 0]
    app = Overlay(root, s, bots=bots, poller=poller, save=lambda st: saved.append(1))
    yield app, bots, keys, saved
    if root.winfo_exists():
        root.destroy()


def test_hotkey_starts_and_stops_a_bot(panel):
    app, bots, keys, _ = panel
    app.tick()                                       # keys count once seen released
    keys["F7"] = True
    app.tick()
    assert bots["repair"].state == "running"
    keys["F7"] = False
    app.tick()
    keys["F7"] = True
    app.tick()
    assert bots["repair"].state == "stopping"


def test_starting_a_bot_stops_the_other_first(panel):
    app, bots, _, _ = panel
    app.toggle_bot("repair")
    app.toggle_bot("potions")
    assert bots["repair"].state == "stopping" and bots["potions"].started == 0
    app.tick()
    assert bots["potions"].started == 0              # repair hasn't exited yet
    bots["repair"].state = "stopped"
    app.tick()
    assert bots["potions"].state == "running"


def test_rebinding_a_hotkey_swaps_with_one_already_used(panel):
    app, _, _, saved = panel
    app.capture_key("repair")
    app._captured(type("E", (), {"keysym": "F8"})())
    assert app.settings["hotkeys"]["repair"] == "F8"
    assert app.settings["hotkeys"]["potions"] == "F7"
    assert saved
    app.capture_key("overlay")
    app._captured(type("E", (), {"keysym": "F9"})())   # reserved: unchanged
    assert app.settings["hotkeys"]["overlay"] == "F6"


def test_settings_changes_are_saved(panel):
    app, _, _, saved = panel
    app.vars[("repair", "max_ships")].set("12")
    app.vars[("input",)].set("mouse")
    assert app.settings["repair"]["max_ships"] == 12
    assert app.settings["input"] == "mouse"
    app.vars[("repair", "max_ships")].set("abc")       # not a number: ignored
    assert app.settings["repair"]["max_ships"] == 12
    assert len(saved) == 2


def test_quitting_kills_a_bot_that_wont_stop(panel):
    app, bots, _, _ = panel
    app.toggle_bot("repair")
    app.quit()
    assert bots["repair"].state == "stopping"        # asked first, not killed yet
    assert not getattr(bots["repair"], "killed", False)
    app._quit_wait(0)                                 # the wait has run out
    assert bots["repair"].killed and bots["repair"].state == "stopped"
    assert not app.root.winfo_exists()


def test_quit_waits_longer_than_the_runner_kill_deadline():
    from overlay import app
    assert app.QUIT_TRIES * app.QUIT_POLL_MS / 1000 > runner.KILL_AFTER


# -- run logs -----------------------------------------------------------------
def test_each_run_gets_a_log_file_and_old_ones_are_cleared(tmp_path):
    import os
    folder = tmp_path / "runs"
    folder.mkdir()
    old = folder / "potions-20260101-000000.log"
    old.write_text("old")
    now = time.time()
    os.utime(old, (now - 20 * 86400, now - 20 * 86400))
    f = runner.open_run_log("repair", folder, now)
    f.close()
    assert not old.exists()
    assert [p.name.split("-")[0] for p in folder.glob("*.log")] == ["repair"]


def test_the_bots_output_is_written_to_its_log(tmp_path):
    import queue

    class Proc:
        stdout = iter(["12:00:00 INFO one\n", "12:00:01 WARNING two\r\n"])
    out = queue.Queue()
    path = tmp_path / "run.log"
    runner.BotProcess._read(Proc(), out, open(path, "a", encoding="utf-8"))
    assert path.read_text(encoding="utf-8") == "12:00:00 INFO one\n12:00:01 WARNING two\n"
    assert [out.get_nowait() for _ in range(3)] == ["12:00:00 INFO one",
                                                     "12:00:01 WARNING two", None]
