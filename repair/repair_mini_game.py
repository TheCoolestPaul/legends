import pyautogui
import collections
import win32gui
import win32api
import win32ui
import pyscreeze
import mouse
import time
from mss import mss
import mss.tools as tools

Point = collections.namedtuple("Point", "x y")
Color = collections.namedtuple("Color", "r g b")


def rgbint2rgbtuple(rgb_int):
    blue = rgb_int & 255
    green = (rgb_int >> 8) & 255
    red = (rgb_int >> 16) & 255
    return red, green, blue


def pixel(x, y=None):
    if y is None:
        y = x[1]
        x = x[0]
    try:
        window = win32gui.FindWindow(None, "The Legend of Pirates Online [BETA]")
        dc = win32gui.GetWindowDC(window)
        colorref = win32gui.GetPixel(dc, x+8, y+8)
    except:
        print("woops")
        return 0, 0, 0
    win32gui.DeleteDC(dc)
    return rgbint2rgbtuple(colorref)


def points_to_region(p1, p2):
    # Replace indexing (p1[0]) with accessing the 'x' attribute (p1.x)
    xi = min(p1.x, p2.x)
    yi = min(p1.y, p2.y)
    xf = max(p1.x, p2.x)
    yf = max(p1.y, p2.y)
    return (xi, yi, xf, yf)


class RepairMiniGame:
    def __init__(self):
        self.name = None
        self.button = None
        self.completed = False
        self.snapshot = "./images/markers/temp.png"

    def play(self):
        pass

    def isGameActive(self):
        pass

    def repair(self):
        if self.button:
            pyautogui.click(self.button)
            self.play()
