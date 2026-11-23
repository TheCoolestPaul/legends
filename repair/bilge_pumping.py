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

import snapshots
from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop

log = logging.getLogger("repair")

GAUGE = (840, 480, 240, 500)     # ref region the rod and green zone live in
GAUGE_MIDDLE = 718               # ref y between the top and bottom zones
PUMP_AT = Point(1420, 700)       # the ship picture, right of the gauge
MIN_GOLD = 4                     # gold pixels in a row to count as the rod
# Zone rows hold ~35-100 green px in the recordings, so text can't be told
# apart by width; the "Great!" text floating up after a pump is handled by
# targeting the end opposite the last pump (see read_gauge).
MIN_GREEN = 20                   # green pixels in a row to count as the zone
# The two places the green zone can be, measured live (ref y).
ZONES = {"top": (509, 572), "bottom": (874, 917)}
MIN_ZONE_ROWS = 6                # green rows (rod rows aside) for a zone to show
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


def green_row_mask(image, scale, rod=None):
    """Per image row: is it a green-zone row? Rows the rod covers (``rod`` =
    its centre row) are ignored: green shows through the rod."""
    img = image.astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    green = (g > 140) & (r < 110) & (b < 110)
    counts = green.sum(axis=1)
    if rod is not None:
        half = round(ROD_HALF_HEIGHT * scale)
        counts[max(0, round(rod) - half):round(rod) + half + 1] = 0
    return counts >= max(3, round(MIN_GREEN * scale))


class BilgePumping(RepairMiniGame):
    name = "bilge_pumping"
    title_marker = "bilge_pumping/bilge_pumping.PNG"
    button = BUTTONS[0]  # same slot as hull scrubbing; shown at sea

    def read_gauge(self):
        """(rod y, (zone top, zone bottom)) in ref coords; either may be None.

        The zone is one of the two fixed ZONES: whichever end is green. Live,
        right after a pump a green "Great!" floats up from the old zone
        while the new one appears; picking "the biggest green patch" then
        chose the wrong end and let the rod pass the new zone. The zone
        always alternates, so the end opposite the last pump wins whenever
        it shows green.
        """
        img = self.win.grab(*GAUGE)
        s = self.win.scale
        row = rod_row(img, s)
        rod = None if row is None else self.win.image_to_ref(GAUGE, 0, row)[1]
        green = green_row_mask(img, s, rod=row)
        rows = {}
        for end, (z0, z1) in ZONES.items():
            i0, i1 = round((z0 - GAUGE[1]) * s), round((z1 - GAUGE[1]) * s)
            rows[end] = int(green[max(0, i0):i1 + 1].sum())
        showing = [end for end, n in rows.items() if n >= round(MIN_ZONE_ROWS * s)]
        expected = {"top": "bottom", "bottom": "top"}.get(self.last_pumped_end)
        if expected in showing:
            end = expected
        elif showing:
            end = max(showing, key=rows.get)
        else:
            return rod, None
        return rod, ZONES[end]

    def __init__(self, window=None):
        super().__init__(window)
        self.last_pumped_end = None  # "top"/"bottom": the zone moves away after a pump

    def rod_y(self):
        return self.read_gauge()[0]

    def step(self):
        """Wait until the rod is in the green zone (and will still be when the
        click lands), pump once, then wait for the zone to move away."""
        deadline = time.monotonic() + WAIT_FOR_GREEN
        seen = []  # recent (time, rod y), for the rod's speed
        trace = [] if snapshots.recording else None
        try:
            self._step(deadline, seen, trace)
        finally:
            if trace:
                self._flush_trace(trace)

    def _step(self, deadline, seen, trace):
        if trace is not None:
            trace.append({"t": round(time.monotonic(), 4), "event": "step"})
        while time.monotonic() < deadline:
            check_stop()
            if not self.still_open():
                return
            now = time.monotonic()
            rod, zone = self.read_gauge()
            raw_zone = zone
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
                # The zones sit at the ends of the gauge, where the rod
                # bounces. Heading into that end, the rod can't overshoot:
                # it stays in the green until it comes back out, so pump as
                # soon as it's in. (Live: waiting for the landing point to
                # fit meant waiting for the bounce, every time.) Heading back
                # out, the click must land before it leaves.
                at_top = (zone[0] + zone[1]) / 2 < GAUGE_MIDDLE
                heading_in = speed < 0 if at_top else speed > 0
                in_zone = top <= rod <= bottom
                pump = in_zone and (heading_in or top <= landing <= bottom)
                if trace is not None:
                    trace.append({"t": round(now, 4), "rod": round(rod, 1),
                                  "raw_zone": raw_zone, "zone": zone,
                                  "speed": round(speed * 1000, 3) / 1000,
                                  "pump": bool(pump)})
                if pump:
                    self.win.click(*self._pump_point(zone))
                    self.last_pumped_end = "top" if at_top else "bottom"
                    self.info = {"pumped_at_y": round(rod), "zone": [round(z) for z in zone]}
                    log.debug("pump: rod %d moving %+.2f px/ms, zone %d-%d",
                              rod, speed / 1000, zone[0], zone[1])
                    self.found("rod")
                    self._wait_for_zone_to_move(zone)
                    if trace is not None:
                        trace.append({"t": round(time.monotonic(), 4), "event": "zone_moved"})
                    return
            elif trace is not None:
                trace.append({"t": round(now, 4), "rod": rod, "raw_zone": raw_zone,
                              "zone": zone, "speed": None, "pump": False})
            time.sleep(POLL)
        rod, zone = self.read_gauge()
        self.info = {"rod_y": rod, "zone": zone}
        self.nothing_found("rod", after=3)

    def _flush_trace(self, trace):
        try:
            import json
            snapshots.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            with open(snapshots.SNAPSHOT_DIR / "bilge_trace.jsonl", "a") as f:
                for row in trace:
                    f.write(json.dumps(row, default=float) + "\n")
        except Exception:
            log.exception("Could not save the bilge trace")

    def _pump_point(self, zone):
        """Where to click. Any click on the game pumps, so click on the ship
        picture, well away from the gauge: clicking on the zone left the
        cursor sitting over the very thing being read."""
        return PUMP_AT

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
