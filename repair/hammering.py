import logging
import time

import cv2
import numpy as np

import snapshots
from repair_mini_game import BUTTONS, Point, RepairMiniGame, run_standalone
from screen import check_stop, wait

log = logging.getLogger("repair")

# Band from above the standing nails down past the board. Live (Nov 2026)
# head tops: standing 632, half-sunk 733, flush "Perfect" 749; board 722.
NAIL_REGION = (848, 540, 860, 230)
FLUSH_TOP = 745         # a head whose top is this low is fully driven: done
NEAR_BOARD_TOP = 700    # below this no shaft shows, so don't ask for one
MIN_HEAD_WIDTH = 30     # ref px; heads are ~40 wide, shafts ~10
MAX_HEAD_WIDTH = 50
MAX_HEAD_HEIGHT = 22    # heads are ~16 tall
MIN_NAIL_SPACING = 50   # ref px between two distinct nails
HEAD_DEPTH = 7          # aim this far below the top of the head
# The target circle around the cursor glows when it is at its smallest. The
# glow covers this offset from the cursor; the plain green ring does not.
GLOW_OFFSET = 15
GLOW_RED = 180
GLOW_FRACTION = 0.1
HAMMER_WINDOW = 1.5     # seconds to wait for the glow on one nail
TRACE_LIMIT = 3000      # polls kept per round when tracing
# A real nail has a plain grey shaft under its head; the hammer sprite that
# follows the cursor (wooden handle) and other grey bits don't. Checked on
# a short stretch below the head, above the board even for half-driven nails.
SHAFT_FROM, SHAFT_TO = 28, 42   # ref px below the top of the head (past its shadow)
# Live trace (Nov 2026): the glow cycle is ~0.95 s; once it shows, it lasts
# ~90-100 ms, and a real hit restarts the cycle. So after the first nail
# glows, hit the next nails straight away, without waiting to see the glow
# move with the cursor (that costs a frame per nail and used up the window).
# Live, two runs: clicks landing 11-28 ms after the glow was first seen
# drove their nail home (one at 33 ms did, one didn't); everything from
# 38 ms on left the nail standing. The glow is sampled every few ms and the
# game draws every ~16 ms, so the true window drifts: keep a safe 30 ms.
# Clicks land ~10 ms apart, so that's 2-3 nails per glow, none wasted.
BURST_WINDOW = 0.03     # every click must land within this of the glow
BURST_MAX = 5           # nails per glow
STEADY_GAP = 0.03       # seconds between the two scans of steady_nails()
STEADY_TOLERANCE = 3    # ref px a real nail may "move" between them


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
        shaft = (spread < 15) & (brightness > 70) & (brightness < 160)
        count, _, stats, _ = cv2.connectedComponentsWithStats(grey)
        s = self.win.scale
        nails = []
        for i in range(1, count):
            left, top, width, height, _ = stats[i]
            # Heads measure 39-41 x 15-16; banner letters etc. are taller.
            if not (MIN_HEAD_WIDTH <= width / s <= MAX_HEAD_WIDTH and height / s <= MAX_HEAD_HEIGHT):
                continue
            x, y = self.win.image_to_ref(NAIL_REGION, left + width / 2, top)
            if y >= FLUSH_TOP:
                continue  # driven flush: nothing left to hit
            if y < NEAR_BOARD_TOP:
                cx = left + width // 2
                y0, y1 = top + round(SHAFT_FROM * s), top + max(1, round(SHAFT_TO * s))
                column = shaft[y0:y1, max(0, cx - 1):cx + 2]   # shafts are thin at 720p
                if column.size == 0 or column.any(axis=1).mean() < 0.8:
                    continue  # no shaft under it: the hammer sprite, a sinking nail...
            nail = Point(round(x), round(y + HEAD_DEPTH))
            if all((nail.x - n.x) ** 2 + (nail.y - n.y) ** 2 >= MIN_NAIL_SPACING ** 2
                   for n in nails):
                nails.append(nail)
        return sorted(nails)

    def glow_fraction(self, at):
        """How much of the glow sample spot next to ``at`` is bright (0-1)."""
        patch = self.win.grab(at.x + GLOW_OFFSET - 3, at.y + GLOW_OFFSET - 3, 6, 6)
        return float((patch[..., 0] > GLOW_RED).mean())

    def glowing(self, at):
        """True if the target circle at ``at`` is glowing (best timing)."""
        # Measured: glowing frames 0.22-1.00 of the patch is bright, others 0.00.
        return self.glow_fraction(at) >= GLOW_FRACTION

    def wait_for_glow(self, nail):
        """Hover ``nail`` until the circle glows; the time it did, or None."""
        self.win.move(*nail)
        self._trace_event("move", nail)
        deadline = time.monotonic() + HAMMER_WINDOW
        while time.monotonic() < deadline:
            check_stop()
            if not self.still_open():
                return None
            frac = self.glow_fraction(nail)
            self._trace_poll(nail, frac)
            if frac >= GLOW_FRACTION:
                return time.monotonic()
            time.sleep(0.005)
        self._trace_event("gave_up", nail)
        return None

    def burst(self, nails):
        """Wait for the glow on the first nail, then hit as many as the glow
        lasts for. Returns how many nails were hit."""
        glow_at = self.wait_for_glow(nails[0])
        if glow_at is None:
            return 0
        hits = 0
        click_time = 0.0  # how long the last click took, to predict the next
        for nail in nails[:BURST_MAX]:
            # The first nail is the one that glowed: always hit it. After
            # that, only if this click will land inside the window.
            if hits and time.monotonic() + click_time - glow_at > BURST_WINDOW:
                break
            before = time.monotonic()
            self.win.click(*nail)
            click_time = time.monotonic() - before
            self._trace_event("click", nail)
            hits += 1
        return hits

    def steady_nails(self):
        """Nails seen in the same place in two scans a moment apart.

        Live: as the "Ready!" banner fades it drifts up past the nails and
        its letter fragments briefly pass as half-sunk heads. Real nails
        don't move; the fading text does.
        """
        first = self.find_nails()
        if not first:
            return []
        time.sleep(STEADY_GAP)
        return [n for n in self.find_nails()
                if any(abs(n.x - m.x) <= STEADY_TOLERANCE and abs(n.y - m.y) <= STEADY_TOLERANCE
                       for m in first)]

    def step(self):
        self.win.park_mouse()   # keep the hammer sprite out of the nail scan
        nails = self.steady_nails()
        self.info = {"nails": [n._asdict() for n in nails]}
        if not nails:
            self.nothing_found("nails")
            wait(0.2)
            return
        self.found("nails")
        log.debug("nails at %s", nails)
        self._trace_start(nails)
        try:
            hits = self.burst(nails)
            log.debug("hit %d nail(s) in one glow", hits)
        finally:
            self._trace_flush()

    # -- timing trace (with --record) ------------------------------------
    def _trace_start(self, nails):
        self._trace = None
        if snapshots.recording:
            self._trace = {"t0": time.monotonic(), "nails": [tuple(n) for n in nails],
                           "events": [], "crops": []}

    def _trace_event(self, kind, nail):
        if getattr(self, "_trace", None) is not None:
            self._trace["events"].append(
                (round(time.monotonic() - self._trace["t0"], 4), kind, tuple(nail), None))

    def _trace_poll(self, nail, frac):
        tr = getattr(self, "_trace", None)
        if tr is None or len(tr["events"]) > TRACE_LIMIT:
            return
        t = round(time.monotonic() - tr["t0"], 4)
        tr["events"].append((t, "poll", tuple(nail), round(frac, 3)))
        tr["crops"].append((t, self.win.grab(nail.x - 60, nail.y - 60, 120, 120).copy()))

    def _trace_flush(self):
        tr, self._trace = getattr(self, "_trace", None), None
        if not tr:
            return
        try:
            import json
            from datetime import datetime
            from PIL import Image
            out = snapshots.SNAPSHOT_DIR / ("hammer_trace_" + datetime.now().strftime("%H%M%S-%f")[:-3])
            out.mkdir(parents=True, exist_ok=True)
            for i, (t, crop) in enumerate(tr["crops"]):
                Image.fromarray(crop).save(out / ("%04d_%07.3f.png" % (i, t)))
            (out / "trace.json").write_text(json.dumps(
                {"nails": tr["nails"], "events": tr["events"]}, indent=0))
            log.info("Saved hammering trace %s (%d polls)", out.name, len(tr["crops"]))
        except Exception:
            log.exception("Could not save the hammering trace")


if __name__ == "__main__":
    run_standalone(Hammering)
