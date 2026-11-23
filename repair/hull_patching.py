"""Hull patching: click every leak to patch it.

A leak is a round, pure-black hole in the planks; it starts as a small dot
with a little jet of water and grows. Patched leaks are dark-grey splotches
with water still running down below them; the old detector keyed on that
water and so often clicked old patches while missing open holes.

Measured live (Nov 2026): holes are pure black blobs from ~235 px (new) to
~2700 px, fairly round (fill >= 0.44), with wood or water around them
(grey ring share 0.05-0.17). Black bits inside patches look similar but sit
in grey patch (grey ring share 0.61-1.00); grain lines and edges are thin.
"""
import cv2
import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

PLAY_REGION = (851, 420, 852, 580)
HOLE_MAX = 30          # brightest channel of a hole pixel
HOLE_SPREAD = 14       # holes are neutral black, not brown grain
HOLE_MIN_AREA = 150    # ref px^2 (new holes ~235)
HOLE_MIN_FILL = 0.4    # share of its bounding box: round, not a streak
HOLE_SIZE = (12, 90)   # ref px across, each way
MAX_GREY_RING = 0.3    # share of the surrounding ring that is grey patch


class HullPatching(RepairMiniGame):
    name = "hull_patching"
    title_marker = "hull_patching/hull_patching.PNG"
    button = BUTTONS[4]

    def find_leaks(self):
        """Centres of the open holes, as ref Points (top to bottom)."""
        img = self.win.grab(*PLAY_REGION).astype(int)
        brightest = img.max(axis=-1)
        black = ((brightest < HOLE_MAX) & (brightest - img.min(axis=-1) < HOLE_SPREAD))
        black = cv2.morphologyEx(black.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, labels, stats, centres = cv2.connectedComponentsWithStats(black)
        s = self.win.scale
        ring_size = max(3, round(9 * s)) | 1
        leaks = []
        for i in range(1, count):
            _, _, w, h, area = stats[i]
            if not (area / (s * s) >= HOLE_MIN_AREA and area / (w * h) >= HOLE_MIN_FILL
                    and HOLE_SIZE[0] <= w / s <= HOLE_SIZE[1]
                    and HOLE_SIZE[0] <= h / s <= HOLE_SIZE[1]):
                continue
            if self._grey_ring(img, labels == i, ring_size) > MAX_GREY_RING:
                continue  # a black bit inside an old patch
            x, y = self.win.image_to_ref(PLAY_REGION, *centres[i])
            leaks.append(Point(round(x), round(y)))
        return sorted(leaks, key=lambda p: (p.y, p.x))

    @staticmethod
    def _grey_ring(img, blob, size):
        """Share of the pixels just around ``blob`` that are grey patch."""
        blob = blob.astype(np.uint8)
        ring = cv2.dilate(blob, np.ones((size, size), np.uint8)).astype(bool) & ~blob.astype(bool)
        px = img[ring]
        if len(px) == 0:
            return 1.0
        r, b = px[:, 0], px[:, 2]
        return float(((np.abs(r - b) < 15) & (r > 25)).mean())

    def step(self):
        self.win.park_mouse()
        leaks = self.find_leaks()
        self.info = {"leaks": len(leaks)}
        if not leaks:
            self.nothing_found("leaks")
            wait(0.05)   # new leaks appear over time; look again soon
            return
        self.found("leaks")
        for leak in leaks:
            check_stop()
            if not self.still_open():
                return
            self.win.click(*leak)


if __name__ == "__main__":
    run_standalone(HullPatching)
