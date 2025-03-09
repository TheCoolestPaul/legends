from PIL import Image
from repair_mini_game import *
import time
import win32api
import win32con
from mss import mss
import mss.tools as tools
import pyscreeze

class Hammering(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.name = "hammering"
        self.button = Point(x=1505, y=1229),
        self.t1 = time.time()
        self.easy = [
            Point(x=1003, y=644),
            Point(x=1185, y=644),
            Point(x=1370, y=644),
            Point(x=1555, y=644)
        ]
        self.medium = [
            Point(x=934, y=644),
            Point(x=1079, y=644),
            Point(x=1210, y=644),
            Point(x=1347, y=644),
            Point(x=1483, y=644),
            Point(x=1622, y=644)
        ]
        self.hard = [
            Point(x=897, y=644),
            Point(x=1005, y=644),
            Point(x=1113, y=644),
            Point(x=1224, y=644),
            Point(x=1335, y=644),
            Point(x=1445, y=644),
            Point(x=1553, y=644),
            Point(x=1662, y=644)
        ]

    def click_when_best(self, x, y):
        win32api.SetCursorPos((x, y))
        screenshot = mss().grab({"top":y, "left":x, "width":30, "height":30})
        if screenshot.pixel(18, 18)[0] > 200:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            #screenshot = mss().grab({"top":y, "left":x, "width":20, "height":20})
            #tools.to_png(screenshot.rgb, screenshot.size, output='./monitor-1.png')
            return True
        else:
            return False

    def find_nails(self):
        img = mss().grab({"top":300, "left":650, "width":1250, "height":1000})
        tools.to_png(img.rgb, img.size, output='./monitor-1.png')
        try:
            if pyscreeze.locate("./images/markers/hammering/easy.png", "./monitor-1.png", grayscale=True):
                return self.easy
        except:
            pass
        try:
            if pyscreeze.locate("./images/markers/hammering/medium.png", "./monitor-1.png", grayscale=True):
                return self.medium
        except:
            pass
        try:
            if pyscreeze.locate("./images/markers/hammering/hard.png", "./monitor-1.png", grayscale=True):
                return self.hard
        except:
            pass

        return None

    def play(self):
        nails = None
        while not nails:
            nails = self.find_nails()
        else:
            print(nails)
            for nail in nails:
                while not self.click_when_best(nail[0], nail[1]):
                    pass
                time.sleep(.05)


if __name__ == "__main__":
    test = Hammering()
    test.play()
