import logging

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, match_marker, wait

log = logging.getLogger("repair")

PLAY_REGION = (650, 300, 1250, 1000)
PLANK_THRESHOLD = 0.9

# Each plank shape and the saw path that cuts it (ref coords).
PLANKS = {
    "forward_slash": [Point(1377, 527), Point(1376, 577), Point(1187, 973)],
    "in_top_out_right": [Point(1267, 524), Point(1279, 585), Point(1281, 772),
                         Point(1736, 772)],
    "letter_n": [Point(1022, 1017), Point(1064, 966), Point(1226, 689),
                 Point(1333, 866), Point(1499, 581)],
    "straight_horizontal": [Point(783, 773), Point(853, 773), Point(1716, 773)],
    "straight_vertical": [Point(1255, 525), Point(1268, 587), Point(1268, 968)],
}


class PlankSawing(RepairMiniGame):
    name = "plank_sawing"
    title_marker = "plank_sawing/plank_sawing.PNG"
    button = BUTTONS[1]

    def identify_plank(self):
        """Name of the plank on screen, or None. Picks the best match."""
        img = self.win.grab(*PLAY_REGION)
        scores = {name: match_marker(img, "plank_sawing/%s.PNG" % name, self.win.scale)[0]
                  for name in PLANKS}
        best = max(scores, key=scores.get)
        log.debug("plank scores %s", {k: round(v, 2) for k, v in scores.items()})
        return best if scores[best] >= PLANK_THRESHOLD else None

    def saw(self, path):
        self.win.move(*path[0])
        self.win.mouse_down(*path[0])
        try:
            for point in path[1:]:
                check_stop()
                self.win.move(*point)
        finally:
            self.win.mouse_up(*path[-1])

    def step(self):
        self.win.park_mouse()
        plank = self.identify_plank()
        if plank is None:
            wait(0.2)
            return
        log.info("Sawing %s", plank)
        self.saw(PLANKS[plank])
        wait(0.3)


if __name__ == "__main__":
    run_standalone(PlankSawing)
