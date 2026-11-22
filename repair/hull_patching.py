import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

PLAY_REGION = (851, 420, 852, 580)
EDGE_MARGIN = 10      # ref px ignored around the edge of the play area
MIN_SPACING = 10      # ref px between two clicks in one pass
CLICK_ABOVE = 5       # click slightly above the leak's top edge


class HullPatching(RepairMiniGame):
    name = "hull_patching"
    title_marker = "hull_patching/hull_patching.PNG"
    button = BUTTONS[4]

    def find_leaks(self):
        """Top edges of leaks: blue pixels with a black pixel right above.

        Returns at most one point per image column, as ref Points.
        """
        img = self.win.grab(*PLAY_REGION).astype(int)
        r, g, b = img[..., 0], img[..., 1], img[..., 2]
        blue = (b > 100) & (g < 100) & (r < 100)
        black = (img < 10).all(axis=-1)
        edge = blue[1:] & black[:-1]          # row i+1 blue, row i black
        margin = max(1, round(EDGE_MARGIN * self.win.scale))
        edge[:margin] = edge[-margin:] = False
        edge[:, :margin] = edge[:, -margin:] = False

        leaks = []
        cols = np.flatnonzero(edge.any(axis=0))
        for col in cols:
            row = np.argmax(edge[:, col]) + 1
            x, y = self.win.image_to_ref(PLAY_REGION, col, row)
            p = Point(round(x), round(y - CLICK_ABOVE))
            if all((p.x - q.x) ** 2 + (p.y - q.y) ** 2 >= MIN_SPACING ** 2 for q in leaks):
                leaks.append(p)
        return leaks

    def step(self):
        self.win.park_mouse()
        leaks = self.find_leaks()
        if not leaks:
            wait(0.2)
            return
        for leak in leaks:
            check_stop()
            self.win.click(*leak)


if __name__ == "__main__":
    run_standalone(HullPatching)
