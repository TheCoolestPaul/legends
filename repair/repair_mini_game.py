import mss
import pyautogui
import collections
import win32gui
import time

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

    def play(self):
        pass
    def isGameActive(self):
        pass
    def isGameCompleted(self):
        with mss.mss() as sct:
            sct.grab({"top": self.button.y, "left": self.button.x, "width": 1, "height": 1})
        if pixel(self.button)[1] > 100 and pixel(self.button)[0] < 10 and pixel(self.button)[2] < 10:
            return True
        else:
            return False

    def repair(self):
        if not self.isGameActive() and not self.isGameCompleted():
            if self.button:
                pyautogui.click(self.button)
                time.sleep(0.5)
                self.play()
            else:
                print("No button found to click.")
                return
        elif self.isGameActive() and not self.isGameCompleted():
            self.play()
        
        print("Game not active or completed.", self.isGameActive(), self.isGameCompleted())