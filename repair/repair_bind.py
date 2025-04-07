from hull_bracing import HullBracing
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
        HullScrubbing,
        HullBracing
    ]
    for game in games:
        mini_game = game()
        mini_game.repair()


if __name__ == "__main__":
    gameClasses = [
        PlankSawing,
        Hammering,
        HullPatching,
        HullScrubbing,
        HullBracing
    ]
    games = []
    for game in gameClasses:
        games.append(game())
    while True:
        for game in games:
            game.repair()