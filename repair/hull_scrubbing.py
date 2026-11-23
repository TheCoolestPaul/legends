import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

PLAY_REGION = (775, 420, 834, 582)
BLOCK = 20            # ref px; scrub at most one spot per block
MAX_SPOTS = 50        # per pass


class HullScrubbing(RepairMiniGame):
    name = "hull_scrubbing"
    title_marker = "hull_scrubbing/hull_scrubbing.PNG"
    button = BUTTONS[0]

    def find_dirt(self):
        """Bright (dirty) spots as ref Points, one per BLOCK x BLOCK cell."""
        img = self.win.grab(*PLAY_REGION)
        bright = (img > 150).all(axis=-1)
        block = max(1, round(BLOCK * self.win.scale))
        spots, seen = [], set()
        ys, xs = np.nonzero(bright)
        for x, y in zip(xs, ys):
            cell = (x // block, y // block)
            if cell in seen:
                continue
            seen.add(cell)
            rx, ry = self.win.image_to_ref(PLAY_REGION, x, y)
            spots.append(Point(round(rx), round(ry)))
            if len(spots) >= MAX_SPOTS:
                break
        return spots

    def step(self):
        self.win.park_mouse()
        spots = self.find_dirt()
        self.info = {"dirt_spots": len(spots)}
        if not spots:
            self.nothing_found("dirt")
            wait(0.2)
            return
        self.found("dirt")
        for spot in spots:
            check_stop()
            self.win.move(*spot)


if __name__ == "__main__":
    run_standalone(HullScrubbing)
