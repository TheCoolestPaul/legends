from hull_scrubbing import HullScrubbing
from plank_sawing import PlankSawing
from hammering import Hammering
from hull_patching import HullPatching
import keyboard


def repair_callback(*args):
    games = [
        PlankSawing,
        Hammering,
        HullPatching,
        HullScrubbing
    ]
    for game in games:
        mini_game = game()
        if mini_game.isGameActive():
            mini_game.play()


if __name__ == "__main__":
    keyboard.on_press_key(key="`", callback=repair_callback)
    while True:
        pass
