import logging
import time

import cv2
import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

log = logging.getLogger("repair")

# Band above the board where standing nail heads appear. Fully hammered
# nails lie flat on the board below it and are ignored.
NAIL_REGION = (848, 540, 860, 180)
MIN_HEAD_WIDTH = 30     # ref px; heads are ~40 wide, shafts ~10
MIN_NAIL_SPACING = 50   # ref px between two distinct nails
HEAD_DEPTH = 7          # aim this far below the top of the head
# The target circle around the cursor glows when it is at its smallest. The
# glow covers this offset from the cursor; the plain green ring does not.
GLOW_OFFSET = 15
GLOW_RED = 180
GLOW_FRACTION = 0.1
HAMMER_WINDOW = 1.5     # seconds to wait for the glow on one nail


class Hammering(RepairMiniGame):
    name = "hammering"
    title_marker = "hammering/hammering.PNG"
    button = BUTTONS[3]

    def find_nails(self):
        """Standing nails as ref Points (left to right), found by shape.

        A nail head is a grey, unsaturated blob much wider than a nail shaft.
        """
        img = self.win.grab(*NAIL_REGION).astype(int)
        spread = img.max(axis=-1) - img.min(axis=-1)
        brightness = img.mean(axis=-1)
        grey = ((spread < 30) & (brightness > 110) & (brightness < 235)).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(grey)
        nails = []
        for i in range(1, count):
            left, top, width, _, _ = stats[i]
            if width / self.win.scale < MIN_HEAD_WIDTH:
                continue
            x, y = self.win.image_to_ref(NAIL_REGION, left + width / 2, top)
            nail = Point(round(x), round(y + HEAD_DEPTH))
            if all((nail.x - n.x) ** 2 + (nail.y - n.y) ** 2 >= MIN_NAIL_SPACING ** 2
                   for n in nails):
                nails.append(nail)
        return sorted(nails)

    def glowing(self, at):
        """True if the target circle at ``at`` is glowing (best timing)."""
        patch = self.win.grab(at.x + GLOW_OFFSET - 3, at.y + GLOW_OFFSET - 3, 6, 6)
        # Measured: glowing frames 0.22-1.00 of the patch is bright, others 0.00.
        return bool((patch[..., 0] > GLOW_RED).mean() >= GLOW_FRACTION)

    def hammer(self, nail):
        """Hover the nail and click when the circle glows."""
        self.win.move(*nail)
        deadline = time.monotonic() + HAMMER_WINDOW
        while time.monotonic() < deadline:
            check_stop()
            if self.glowing(nail):
                self.win.click(*nail)
                return True
            time.sleep(0.01)
        return False

    def step(self):
        nails = self.find_nails()
        if not nails:
            wait(0.3)
            return
        log.debug("nails at %s", nails)
        for nail in nails[:10]:
            self.hammer(nail)


if __name__ == "__main__":
    run_standalone(Hammering)
