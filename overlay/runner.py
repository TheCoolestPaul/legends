"""Start and stop the bots as child processes, and follow their logs.

Each bot runs in its own process with its own kill switch, so a stuck bot
can be killed without taking the overlay down. The overlay asks a bot to
stop by creating its stop file (TLOPO_STOP_FILE), which the bot's
kill-switch thread treats like a backtick press; if the bot is still
running KILL_AFTER seconds later, the process is terminated.
"""
import collections
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("overlay")

REPO_ROOT = Path(__file__).resolve().parent.parent
KILL_AFTER = 5.0            # s after a stop request before terminating
CREATE_NO_WINDOW = 0x08000000

BOTS = {
    "repair": {"title": "Repair", "script": "repair/repair_bind.py",
               "count": re.compile(r"Ship repaired \((\d+) so far\)"), "unit": "ships"},
    "potions": {"title": "Potions", "script": "potions/brew_bot.py",
                "count": re.compile(r"Brewed (\d+) so far"), "unit": "brews"},
}
LOG_LINE = re.compile(r"^\d\d:\d\d:\d\d (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
# Every run's output is also saved here (git-ignored), one file per run:
# the panel only keeps the last few hundred lines in memory.
LOG_DIR = REPO_ROOT / "logs"
KEEP_LOGS_DAYS = 14


def open_run_log(bot, folder=None, now=None):
    """A new log file for one run of ``bot`` (older than KEEP_LOGS_DAYS are
    deleted), or None if it can't be made."""
    folder = Path(folder or LOG_DIR)
    now = time.time() if now is None else now
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.log"):
            if now - old.stat().st_mtime > KEEP_LOGS_DAYS * 86400:
                old.unlink()
        name = "%s-%s.log" % (bot, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
        return open(folder / name, "a", encoding="utf-8")
    except OSError:
        log.exception("Couldn't open a log file for the %s bot", bot)
        return None


def python_exe(executable=sys.executable, venv=REPO_ROOT / ".venv" / "Scripts" / "python.exe"):
    """The Python to run bots with: the project's .venv if there is one (a
    double-clicked overlay.pyw runs under whatever Python owns .pyw files),
    else the console Python next to ``executable``."""
    if venv is not None and Path(venv).exists():
        return str(venv)
    path = Path(executable)
    if path.name.lower() == "pythonw.exe" and (path.parent / "python.exe").exists():
        return str(path.parent / "python.exe")
    return str(path)


def build_command(bot, settings, python=None):
    """The command line that runs ``bot`` with the overlay's settings."""
    opts = settings[bot]
    cmd = [python or python_exe(), "-u", str(REPO_ROOT / BOTS[bot]["script"])]
    if opts.get("record"):
        cmd.append("--record")
    if opts.get("debug"):
        cmd.append("--debug")
    if settings.get("max_minutes"):
        cmd += ["--max-minutes", str(settings["max_minutes"])]
    if bot == "repair" and opts.get("max_ships"):
        cmd += ["--max-ships", str(opts["max_ships"])]
    if bot == "potions":
        recipes = opts.get("recipes", "").strip()
        if opts.get("fill"):
            cmd.append("--fill")
        elif recipes:
            cmd += ["--recipes", recipes, "--count", str(max(1, opts.get("count", 1)))]
        elif opts.get("max_brews"):
            cmd += ["--max-brews", str(opts["max_brews"])]
        if opts.get("any_tier"):
            cmd.append("--any-tier")
    return cmd


def build_env(settings, stop_file, base=None):
    env = dict(os.environ if base is None else base)
    env["TLOPO_INPUT"] = settings.get("input", "background")
    env["TLOPO_STOP_FILE"] = str(stop_file)
    env["PYTHONUNBUFFERED"] = "1"
    return env


class BotProcess:
    """One bot the overlay can start and stop. Call ``poll()`` regularly
    (from the UI thread) to take in new log lines and notice the exit."""

    def __init__(self, bot, popen=subprocess.Popen, clock=time.monotonic):
        self.bot = bot
        self.title = BOTS[bot]["title"]
        self.popen = popen
        self.clock = clock
        self.proc = None
        self.state = "stopped"          # stopped | running | stopping
        self.lines = collections.deque(maxlen=300)
        self.status = ""                # the last log message worth showing
        self.count = 0                  # ships / brews this run
        self.exit_code = None
        self._queue, self._eof = queue.Queue(), False
        self._stop_asked_at = None
        self.stop_file = Path(tempfile.gettempdir()) / ("tlopo_stop_%s_%d.flag"
                                                        % (bot, os.getpid()))

    @property
    def running(self):
        return self.state in ("running", "stopping")

    def start(self, settings):
        if self.running:
            return
        self._remove_stop_file()
        self.lines.clear()
        self.count, self.exit_code, self._stop_asked_at = 0, None, None
        self._queue, self._eof = queue.Queue(), False
        cmd = build_command(self.bot, settings)
        self.proc = self.popen(cmd, cwd=str(REPO_ROOT), env=build_env(settings, self.stop_file),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                               errors="replace", bufsize=1,
                               creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.state = "running"
        self.status = "Starting..."
        threading.Thread(target=self._read, args=(self.proc, self._queue, open_run_log(self.bot)),
                         name="read-" + self.bot, daemon=True).start()

    @staticmethod
    def _read(proc, out, logfile=None):
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if logfile is not None:
                    try:
                        logfile.write(line + "\n")
                        logfile.flush()
                    except (OSError, ValueError):
                        logfile = None
                out.put(line)
        finally:
            if logfile is not None:
                logfile.close()
            out.put(None)

    def stop(self):
        """Ask the bot to stop; it is terminated if it hasn't KILL_AFTER s later."""
        if not self.running or self.state == "stopping":
            return
        try:
            self.stop_file.write_text("stop", encoding="utf-8")
        except OSError:
            self.proc.terminate()
        self.state = "stopping"
        self.status = "Stopping..."
        self._stop_asked_at = self.clock()

    def kill(self, timeout=2.0):
        """Terminate the process now (no stop request) and wait up to
        ``timeout`` s for it to exit. Used when the overlay is closing and
        can't wait for KILL_AFTER, so no bot is left running without it."""
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            log.warning("%s bot (pid %d) didn't exit after terminate", self.title, self.proc.pid)

    def poll(self):
        """Take in new output; returns the new lines."""
        new = []
        while True:
            try:
                line = self._queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                self._eof = True
                continue
            new.append(line)
            self._take(line)
        if self.proc is not None and self.running:
            code = self.proc.poll()
            if code is not None and self._eof:     # every line read first
                self._exited(code)
            elif (self._stop_asked_at is not None
                  and self.clock() - self._stop_asked_at > KILL_AFTER):
                self.proc.terminate()
                self.status = "Didn't stop; terminated."
        return new

    def _take(self, line):
        self.lines.append(line)
        match = BOTS[self.bot]["count"].search(line)
        if match:
            self.count = int(match.group(1))
        parsed = LOG_LINE.match(line)
        if parsed and parsed.group(1) != "DEBUG":
            self.status = parsed.group(2)
        elif not parsed and line.strip() and not line.startswith(" "):
            self.status = line.strip()     # e.g. a traceback's last line, a print()

    def _exited(self, code):
        self.exit_code = code
        self.state = "stopped"
        self._remove_stop_file()
        if code not in (0, None) and not self.status.startswith("Didn't stop"):
            self.status = "Exited (code %d): %s" % (code, self.status)

    def _remove_stop_file(self):
        try:
            self.stop_file.unlink()
        except OSError:
            pass
