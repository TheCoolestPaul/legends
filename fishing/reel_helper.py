"""Legendary fishing helper: hold Left Ctrl to reel in circles.

    python fishing/reel_helper.py

While Left Ctrl is held, the real cursor circles clockwise around where it
was when Ctrl went down (legendary fish are reeled by circling the mouse;
normal fish by holding the button, which fish_bot.py does), and goes back
there when Ctrl is let go. You play everything else. Ctrl+C or ` quits.

This moves the real cursor, so keep the game in front while using it.
"""
import ctypes
import math
import sys
import time

RADIUS = 300          # px of the circle (the old fishing.py used 350 at 1920 wide)
STEPS = 12            # points per circle
STEP_DELAY = 0.015    # s per point: ~0.18 s a circle
VK_LCONTROL, VK_BACKTICK = 0xA2, 0xC0

user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def held(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def cursor():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def main():
    print("Hold Left Ctrl to reel in circles; Ctrl+C or ` to quit.")
    while True:
        if held(VK_BACKTICK):
            print("Stopped.")
            return 0
        if held(VK_LCONTROL):
            cx, cy = cursor()
            angle = 0.0
            while held(VK_LCONTROL):
                angle += 2 * math.pi / STEPS
                user32.SetCursorPos(int(cx + RADIUS * math.cos(angle)),
                                    int(cy + RADIUS * math.sin(angle)))
                time.sleep(STEP_DELAY)
            user32.SetCursorPos(cx, cy)
        time.sleep(0.01)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
