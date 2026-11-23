"""Bilge pumping: "Click when the bar is in the green to pump!"

A metal rod with gold end caps bounces up and down a gauge, and part of the
gauge is a green zone. A pump in the green moves the zone to the other end
of the gauge (top -> bottom -> top...), so the zone is found on screen each
time rather than assumed.

Measured live (Nov 2026): the rod travels between y ~520 and ~916; the top
zone was y 519-572, the bottom one y 867-917. Its speed varies (~1 px/ms
in one recording, ~0.3 px/ms in another), so the bot measures it each pass:
it tracks the rod every couple of ms and clicks when the rod is in the zone
*and* will still be in it when the click lands (~10 ms later).
"""
import logging
import time

import numpy as np

from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop

log = logging.getLogger("repair")

GAUGE = (840, 480, 240, 500)     # ref region the rod and green zone live in
MIN_GOLD = 4                     # gold pixels in a row to count as the rod
MIN_GREEN = 20                   # green pixels in a row to count as the zone
ROD_HALF_HEIGHT = 10             # ref px the rod covers either side of its centre
CLICK_LATENCY = 0.012            # seconds from deciding to the click landing
SPEED_WINDOW = 0.02              # seconds of rod positions used for its speed
EDGE_MARGIN = 4                  # ref px kept clear of the zone's edges
WAIT_FOR_GREEN = 2.0             # seconds (2+ cycles) to wait for a pump chance
POLL = 0.002


def _gold(img):
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return (r > 150) & (g > 100) & (b < 80) & (r - b > 90)


def rod_row(image, scale):
    """Image row of the rod's centre (from its gold end caps), or None."""
    gold = _gold(image.astype(int))
    rows = np.flatnonzero(gold.sum(axis=1) >= max(2, round(MIN_GOLD * scale)))
    return float(rows.mean()) if len(rows) else None


def green_rows(image, scale, rod=None):
    """(first, last) image rows of the green zone, or None.

    Rows the rod covers (``rod`` = its centre row) are ignored: live, green
    showing through the rod made a rod resting on the zone's edge stretch
    the zone by ~10 px.
    """
    img = image.astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    green = (g > 140) & (r < 110) & (b < 110)
    counts = green.sum(axis=1)
    if rod is not None:
        half = round(ROD_HALF_HEIGHT * scale)
        counts[max(0, round(rod) - half):round(rod) + half + 1] = 0
    rows = np.flatnonzero(counts >= max(3, round(MIN_GREEN * scale)))
    if not len(rows):
        return None
    # the rod crossing the zone splits it: bridge rod-sized gaps, keep the
    # largest block
    bridge = 2 * ROD_HALF_HEIGHT + 10       # the ignored rod rows plus soft edges
    runs = np.split(rows, np.flatnonzero(np.diff(rows) > max(3, round(bridge * scale))) + 1)
    run = max(runs, key=len)
    return int(run[0]), int(run[-1])


class BilgePumping(RepairMiniGame):
    name = "bilge_pumping"
    title_marker = "bilge_pumping/bilge_pumping.PNG"
    button = BUTTONS[0]  # same slot as hull scrubbing; shown at sea

    def read_gauge(self):
        """(rod y, (zone top, zone bottom)) in ref coords; either may be None."""
        img = self.win.grab(*GAUGE)
        s = self.win.scale
        row = rod_row(img, s)
        rod = None if row is None else self.win.image_to_ref(GAUGE, 0, row)[1]
        zone = green_rows(img, s, rod=row)
        if zone is not None:
            zone = tuple(self.win.image_to_ref(GAUGE, 0, z)[1] for z in zone)
        return rod, zone

    def rod_y(self):
        return self.read_gauge()[0]

    def step(self):
        """Wait until the rod is in the green zone (and will still be when the
        click lands), pump once, then wait for the zone to move away."""
        deadline = time.monotonic() + WAIT_FOR_GREEN
        seen = []  # recent (time, rod y), for the rod's speed
        clean_zone = None  # the zone as read while the rod wasn't on it
        while time.monotonic() < deadline:
            check_stop()
            if not self.still_open():
                return
            now = time.monotonic()
            rod, zone = self.read_gauge()
            if zone is not None:
                clear = ROD_HALF_HEIGHT + 2
                if rod is None or rod < zone[0] - clear or rod > zone[1] + clear:
                    clean_zone = zone
                # While the rod is on the zone it hides part of it (all of the
                # bottom, at the end of its travel): use the clean reading.
                zone = clean_zone or zone
            if rod is None:
                seen = []
            else:
                seen = [(t, y) for t, y in seen if now - t <= SPEED_WINDOW] + [(now, rod)]
            # Only decide with a measured speed: the rod's speed varies, and a
            # rod leaving the zone looks the same as one entering it. Measure
            # over the last ~20 ms: single frames repeat, then jump.
            if (rod is not None and zone is not None and len(seen) >= 2
                    and now - seen[0][0] >= SPEED_WINDOW / 2):
                speed = (rod - seen[0][1]) / (now - seen[0][0])
                landing = rod + speed * CLICK_LATENCY
                top, bottom = zone[0] + EDGE_MARGIN, zone[1] - EDGE_MARGIN
                if top <= rod <= bottom and top <= landing <= bottom:
                    self.win.click(*self._pump_point(zone))
                    self.info = {"pumped_at_y": round(rod), "zone": [round(z) for z in zone]}
                    log.debug("pump: rod %d moving %+.2f px/ms, zone %d-%d",
                              rod, speed / 1000, zone[0], zone[1])
                    self.found("rod")
                    self._wait_for_zone_to_move(zone)
                    return
            time.sleep(POLL)
        rod, zone = self.read_gauge()
        self.info = {"rod_y": rod, "zone": zone}
        self.nothing_found("rod", after=3)

    def _pump_point(self, zone):
        """Click on the gauge, in the middle of the green zone."""
        return Point(GAUGE[0] + GAUGE[2] // 2 - 10, round((zone[0] + zone[1]) / 2))

    def _wait_for_zone_to_move(self, zone):
        """After a pump the zone jumps to the other end; wait for that (or for
        the rod to leave it, if the pump didn't count)."""
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            check_stop()
            rod, now_zone = self.read_gauge()
            moved = now_zone is not None and abs(now_zone[0] - zone[0]) > 40
            rod_out = rod is not None and not (zone[0] - 10 <= rod <= zone[1] + 10)
            if moved or rod_out:
                return
            time.sleep(POLL)


if __name__ == "__main__":
    run_standalone(BilgePumping)
