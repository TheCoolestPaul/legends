import pyautogui
from repair_mini_game import *
import time
import math
import keyboard
from mss import mss
import mss.tools as tools
import pyscreeze


def endOfRound():
            try:
                img = mss().grab({"top":300, "left":650, "width":1250, "height":1000})
                tools.to_png(img.rgb, img.size, output='./monitor-1.png')
                region = pyscreeze.locate('./images/markers/another_game.png', './monitor-1.png', grayscale=True)
            except:
                    return False
            return True

class PlankSawing(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.button = Point(x=796, y=914)
        self.name = "plank_sawing"
        self.planks = {
            'forward_slash': [
                Point(x=1377, y=527),
                Point(x=1376, y=577),
                Point(x=1187, y=973)
            ],
            'in_top_out_right': [
                Point(x=1267, y=524),
                Point(x=1279, y=585),
                Point(x=1281, y=772),
                Point(x=1736, y=772)
            ],
            'letter_n': [
                Point(x=1061, y=968),
                Point(x=1226, y=687),
                Point(x=1334, y=868),
                Point(x=1497, y=589)
            ],
            'straight_horizontal': [
                Point(x=783, y=773),
                Point(x=853, y=773),
                Point(x=1716, y=773)
            ],
            'straight_vertical': [
                Point(x=1255, y=525),
                Point(x=1268, y=587),
                Point(x=1268, y=968)
            ]
        }

    def play(self):
        while endOfRound() == False:
            for plank in self.planks.keys():
                try:
                    img = mss().grab({"top":300, "left":650, "width":1250, "height":1000})
                    tools.to_png(img.rgb, img.size, output='./monitor-1.png')
                    region = pyscreeze.locate('./images/markers/plank_sawing/{}.png'.format(plank), './monitor-1.png', grayscale=True)
                except:
                    region = None
                if region:
                    print('Plank {} [{}] found'.format(plank, region))
                    direction_list = self.planks[plank]
                    pyautogui.moveTo(direction_list[0])
                    for point in direction_list:
                        x1, y1 = pyautogui.position()
                        x2, y2 = point
                        dx = x2 - x1
                        dy = y2 - y1
                        dh = math.sqrt((dx ** 2) + (dy ** 2))
                        duration = dh * .0002
                        pyautogui.mouseDown(duration=duration)
                        pyautogui.moveTo(point, duration=duration)
                    pyautogui.mouseUp()
                else:
                    print('Plank {} not found'.format(plank))
        print("End of round")


if __name__ == "__main__":
    test = PlankSawing()
    test.play()