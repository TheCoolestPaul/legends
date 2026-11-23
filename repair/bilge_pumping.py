import time

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop

# The pump strikes alternate between a top and a bottom marker. When the
# watched marker changes colour, clicking pumps.
TOP_STRIKE = Point(953, 545)
BOTTOM_STRIKE = Point(961, 878)
NEUTRAL = Point(982, 570)
STRIKE_WAIT = 3.0  # seconds to wait for a strike before re-deciding


class BilgePumping(RepairMiniGame):
    name = "bilge_pumping"
    title_marker = "bilge_pumping/bilge_pumping.PNG"
    button = BUTTONS[0]  # same slot as hull scrubbing; shown at sea

    def __init__(self, window=None):
        super().__init__(window)
        self.watching = None  # TOP_STRIKE or BOTTOM_STRIKE

    def step(self):
        if self.watching is None:
            top = self.win.pixel(*TOP_STRIKE)
            bottom = self.win.pixel(*BOTTOM_STRIKE)
            self.watching = TOP_STRIKE if top[1] > bottom[1] else BOTTOM_STRIKE
        before = self.win.pixel(*self.watching)
        deadline = time.monotonic() + STRIKE_WAIT
        while time.monotonic() < deadline:
            check_stop()
            if self.win.pixel(*self.watching) != before:
                self.win.click(*NEUTRAL)
                self.watching = BOTTOM_STRIKE if self.watching == TOP_STRIKE else TOP_STRIKE
                self.found("strike")
                return
        self.info = {"watching": self.watching._asdict(), "colour": before}
        self.nothing_found("strike", after=3)
        self.watching = None  # no strike seen; look again from scratch


if __name__ == "__main__":
    run_standalone(BilgePumping)
