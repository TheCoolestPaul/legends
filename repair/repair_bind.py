"""Play every ship-repair minigame until the ship is fully repaired.

Run from anywhere:  python repair/repair_bind.py
Press ` (backtick) to stop. If the bot has not stopped 2 seconds later, the
process is killed. Ctrl+C or moving the mouse into a screen corner also stop it.
"""
import logging
import time

from bilge_pumping import BilgePumping
from hammering import Hammering
from hull_bracing import HullBracing
from hull_patching import HullPatching
from hull_scrubbing import HullScrubbing
from plank_sawing import PlankSawing
from repair_mini_game import (BUTTONS, TITLE_REGION, TITLE_THRESHOLD,
                              button_completed, repair_bar_visible)
from screen import GameWindow, WindowNotFound, live_session, timed_out, wait

log = logging.getLogger("repair")

GAME_CLASSES = [HullScrubbing, BilgePumping, PlankSawing, HullBracing,
                Hammering, HullPatching]
MAX_START_ATTEMPTS = 3  # per button, before skipping it until the bar resets


class RepairBot:
    def __init__(self, window):
        self.win = window
        self.games = [cls(window) for cls in GAME_CLASSES]
        self.failed_starts = {}
        self.announced_done = False

    def active_game(self):
        """The minigame whose title is on screen, or None."""
        title = self.win.grab(*TITLE_REGION)
        scored = [(game.title_score(title), game) for game in self.games]
        score, game = max(scored, key=lambda pair: pair[0])
        return game if score >= TITLE_THRESHOLD else None

    def wait_for_game(self, seconds=3):
        deadline = time.monotonic() + seconds
        while not timed_out(deadline):
            wait(0.2)
            game = self.active_game()
            if game:
                return game
        return None

    def tick(self):
        """One pass of the main loop."""
        self.win.refresh()
        game = self.active_game()
        if game:
            game.play()
            return
        if not repair_bar_visible(self.win):
            self.failed_starts.clear()
            self.announced_done = False
            wait(1)
            return
        pending = [b for b in BUTTONS
                   if not button_completed(self.win, b)
                   and self.failed_starts.get(b, 0) < MAX_START_ATTEMPTS]
        if not pending:
            if not self.announced_done:
                log.info("Nothing left to repair.")
                self.announced_done = True
            wait(1)
            return
        self.announced_done = False
        button = pending[0]
        self.win.click(*button)
        self.win.park_mouse()
        if self.wait_for_game() is None:
            self.failed_starts[button] = self.failed_starts.get(button, 0) + 1
            log.warning("Button at %s opened nothing (attempt %d)",
                        button, self.failed_starts[button])

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
    with live_session():
        RepairBot(GameWindow()).run()


if __name__ == "__main__":
    main()
