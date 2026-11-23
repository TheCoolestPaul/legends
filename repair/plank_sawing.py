import logging
import math

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import match_marker, wait

log = logging.getLogger("repair")

PLAY_REGION = (650, 300, 1250, 1000)
# Live scores (Nov 2026): the plank on screen 0.995-1.0, other planks up to
# 0.93, mid-animation frames ~0.86 for every plank.
PLANK_THRESHOLD = 0.97
PLANK_MARGIN = 0.04    # best match must beat the runner-up by this much
# Live: 40 px steps cut every plank in one stroke; 70 px needed up to 4.
SAW_STEP = 40          # ref px between cursor positions along a cut
AFTER_CUT = 0.15       # seconds to let the next plank start sliding in
NO_PLANK_WAIT = 0.1    # seconds between looks while no plank is ready
# Live: cuts were "Perfect" but sometimes didn't register as going all the
# way through; carrying on past the plank's edge makes the exit unambiguous.
SAW_OVERSHOOT = 60     # ref px past the last point, same direction


def overshoot(path, distance):
    """``path`` with its last segment carried on ``distance`` further."""
    (x0, y0), (x1, y1) = path[-2], path[-1]
    length = math.hypot(x1 - x0, y1 - y0) or 1
    end = Point(round(x1 + (x1 - x0) / length * distance),
                round(y1 + (y1 - y0) / length * distance))
    return list(path) + [end]

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
        ranked = sorted(scores, key=scores.get, reverse=True)
        best, runner_up = ranked[0], ranked[1]
        self.info = {"plank_scores": {k: round(v, 3) for k, v in scores.items()}}
        log.debug("plank scores %s", self.info["plank_scores"])
        if scores[best] < PLANK_THRESHOLD or scores[best] - scores[runner_up] < PLANK_MARGIN:
            return None
        return best

    def saw(self, path):
        # Live: jumping corner to corner failed most cuts; 15 px steps with
        # pauses worked but was slower than needed; 40 px steps at input
        # pace (~0.3 s per cut) work reliably, 70 px steps did not.
        self.win.drag(overshoot(path, SAW_OVERSHOOT), step=SAW_STEP, delay=0)

    def step(self):
        self.win.park_mouse()
        plank = self.identify_plank()
        if plank is None:
            self.nothing_found("plank", after=20)
            wait(NO_PLANK_WAIT)
            return
        self.found("plank")
        log.info("Sawing %s", plank)
        self.saw(PLANKS[plank])
        wait(AFTER_CUT)


if __name__ == "__main__":
    run_standalone(PlankSawing)
