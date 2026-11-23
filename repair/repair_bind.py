"""Play every ship-repair minigame until the ship is fully repaired.

Run from anywhere:  python repair/repair_bind.py
Options: --record saves frames of every game to debug_frames/, --debug logs more.
F9 saves a debug frame of whatever is on screen.
Input goes straight to the game window, so it can sit behind other windows
and the mouse stays yours; keep the real cursor off the game while it runs.
Set TLOPO_INPUT=mouse to drive the real cursor instead (game must be in front).
Press ` (backtick) to stop. If the bot has not stopped 2 seconds later, the
process is killed. Ctrl+C also stops it.
"""
import logging
import time

from bilge_pumping import BilgePumping
from hammering import Hammering
from hull_bracing import HullBracing
from hull_patching import HullPatching
from hull_scrubbing import HullScrubbing
from plank_sawing import PlankSawing
import snapshots
from repair_mini_game import (BUTTONS, TITLE_REGION, TITLE_THRESHOLD,
                              button_completed, button_in_progress, parse_args,
                              repair_bar_visible, session_hotkeys)
from screen import (GameWindow, StopRequested, WindowNotFound, live_session,
                    timed_out, wait)

log = logging.getLogger("repair")

GAME_CLASSES = [HullScrubbing, BilgePumping, PlankSawing, HullBracing,
                Hammering, HullPatching]
MAX_START_ATTEMPTS = 3  # per button, before skipping it until the bar resets
START_WAIT = 6          # seconds to wait for a clicked button's game to open
RECLICK_EVERY = 1.0     # click the button again this often until its game opens
SLOW_OPEN = 1.5         # log how long a game took to open beyond this
WINDOW_CLOSED = object()  # wait_for_game(): the repair window went away
# Bilge pumping (this slot at sea; hull scrubbing in port) takes ~10 s
# however well it's played, while the bot beats people at the other games.
# Leave pumping to the crew and take it only once nothing else is open.
LAST_BUTTON = BilgePumping.button
# Hull patching takes people the longest, so the bot takes it first and
# leaves the quicker games to the crew.
FIRST_BUTTON = HullPatching.button
# Live: when the last game finishes, the ship is repaired and every button
# resets, but that game's title lingers for a frame or two. Without a pause
# it looked unfinished and was started again for a moment.
FINISH_SETTLE = 3.0     # seconds a just-finished game isn't replayed


def start_order(button, failed_starts):
    """Sort key for open buttons: a button that failed to open goes to the
    back, so the others get a turn first; then patching first, pumping last."""
    return (failed_starts.get(button, 0), button != FIRST_BUTTON,
            button == LAST_BUTTON)


class RepairBot:
    def __init__(self, window):
        self.win = window
        self.games = [cls(window) for cls in GAME_CLASSES]
        self.failed_starts = {}
        self.announced_done = False
        self.announced_waiting = False
        self.skipped = set()   # games that timed out on this ship
        self.finished = None   # (name, monotonic time) of the game last won

    def active_game(self):
        """The minigame whose title is on screen, or None."""
        title = self.win.grab(*TITLE_REGION)
        scored = [(game.title_score(title), game) for game in self.games]
        score, game = max(scored, key=lambda pair: pair[0])
        return game if score >= TITLE_THRESHOLD else None

    def playable_game(self):
        """The open minigame if it still needs playing, else None.

        A finished game keeps its title on screen (over "Choose Another
        Game") until another one is picked, so a title alone is not enough.
        """
        game = self.active_game()
        return game if game and not game.is_completed() else None

    def wait_for_game(self, button, seconds=START_WAIT):
        """Wait for the game behind ``button`` to open; return it or None.

        Live, a game can take ~3.5 s to switch in after the previous one
        finishes, and may open already done when this ship doesn't need it.
        """
        started = time.monotonic()
        deadline = started + seconds
        last_click, clicks = started, 1
        while not timed_out(deadline):
            wait(0.1)
            game = self.active_game()
            if game is not None and game.button == button:
                took = time.monotonic() - started
                if took > SLOW_OPEN:
                    log.info("%s took %.1fs to open (%d click%s)", game.name, took,
                             clicks, "" if clicks == 1 else "s")
                return game
            if game is None and not repair_bar_visible(self.win):
                # Live: the player left the repair window (went to a cannon)
                # mid-wait, and the re-click below landed on the cannon view.
                log.info("Repair window closed; not clicking")
                return WINDOW_CLOSED
            if time.monotonic() - last_click >= RECLICK_EVERY:
                # Live: clicks right after a game finishes are sometimes
                # ignored outright (once, two clicks over 6 s both were);
                # keep clicking until the game switches in.
                self.win.press(*button)
                self.win.park_mouse()
                last_click, clicks = time.monotonic(), clicks + 1
        return None

    def just_finished(self, game):
        """Did the bot win ``game`` in the last FINISH_SETTLE seconds?"""
        return (self.finished is not None and self.finished[0] == game.name
                and time.monotonic() - self.finished[1] < FINISH_SETTLE)

    def repair_window_open(self):
        """A fresh look: is the repair window (a game or the bar) on screen?"""
        return self.active_game() is not None or repair_bar_visible(self.win)

    def tick(self):
        """One pass of the main loop."""
        self.win.refresh()
        snapshots.take_requested(self.win)
        shown = self.active_game()
        if shown and not shown.is_completed() and shown.name not in self.skipped:
            if self.just_finished(shown):
                wait(0.1)                  # its title is on its way out
                return
            try:
                if shown.play():
                    self.finished = (shown.name, time.monotonic())
                if shown.timed_out:
                    # Don't restart a game that can't be finished; do the rest.
                    log.warning("Skipping %s for the rest of this ship", shown.name)
                    self.skipped.add(shown.name)
            except (StopRequested, WindowNotFound):
                raise
            except Exception:
                # One broken game should not end the whole run.
                log.exception("%s crashed", shown.name)
                shown.snapshot("error")
                wait(1)
            return
        # A game title means the repair window is open, even when the
        # selected button's frame hides the bar marker.
        if shown is None and not repair_bar_visible(self.win):
            self.failed_starts.clear()
            self.skipped.clear()
            self.announced_done = False
            wait(1)
            return
        done = [button_completed(self.win, b) for b in BUTTONS]
        if shown is None and not any(done):
            self.skipped.clear()           # a fresh ship: try everything again
        skipped_buttons = {g.button for g in self.games if g.name in self.skipped}
        open_buttons = [b for b, d in zip(BUTTONS, done)
                        if not d and b not in skipped_buttons
                        and self.failed_starts.get(b, 0) < MAX_START_ATTEMPTS]
        # On a crewed ship other players take games too; theirs show the
        # yellow progress ring and won't open for us. Leave them be.
        pending = [b for b in open_buttons if not button_in_progress(self.win, b)]
        pending.sort(key=lambda b: start_order(b, self.failed_starts))
        if open_buttons and not pending:
            if not self.announced_waiting:
                log.info("Waiting for other players to finish their repairs.")
                self.announced_waiting = True
            wait(1)
            return
        self.announced_waiting = False
        if not pending:
            if not self.announced_done:
                log.info("Nothing left to repair%s.",
                         " (skipped: %s)" % ", ".join(sorted(self.skipped))
                         if self.skipped else "")
                self.announced_done = True
            wait(1)
            return
        self.announced_done = False
        button = pending[0]
        if not self.repair_window_open():   # it may have closed since we looked
            return
        self.win.press(*button)
        self.win.park_mouse()
        opened = self.wait_for_game(button)
        if opened is WINDOW_CLOSED:
            return
        if opened is not None:
            if opened.is_completed():
                log.info("%s: nothing to repair on this ship", opened.name)
            return
        self.failed_starts[button] = self.failed_starts.get(button, 0) + 1
        log.warning("Button at %s opened nothing (attempt %d)",
                    button, self.failed_starts[button])
        snapshots.save(self.win, "button_opened_nothing",
                       info={"button": button._asdict(),
                             "attempt": self.failed_starts[button]})

    def run(self):
        last_warning = 0.0
        while True:
            try:
                self.tick()
            except WindowNotFound as e:
                if time.monotonic() - last_warning > 30:
                    log.warning("%s; waiting for it", e)
                    last_warning = time.monotonic()
                wait(2)


def main():
    args = parse_args("Play every ship-repair minigame until the ship is repaired.")
    with live_session(hotkeys=session_hotkeys(), debug=args.debug):
        log.info("F9 saves a debug frame%s.", "; recording frames" if args.record else "")
        RepairBot(GameWindow()).run()


if __name__ == "__main__":
    main()
