import pyautogui
from repair_mini_game import *
from mss import mss
import mss.tools as tools
import pyscreeze

class PlankSawing(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.button = Point(1058, 1227)
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
                Point(x=1022, y=1017),
                Point(x=1064, y=966),
                Point(x=1226, y=689),
                Point(x=1333, y=866),
                Point(x=1499, y=581)
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
    
    def isGameActive(self):
        img = mss().grab({"top":141, "left":1007, "width":536, "height":254})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/plank_sawing/plank_sawing.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def play(self):
        print("Playing Plank Sawing...")
        pyautogui.PAUSE = 0.015
        while not self.isGameCompleted() and self.isGameActive():
            img = mss().grab({"top":300, "left":650, "width":1250, "height":1000})
            tools.to_png(img.rgb, img.size, output='./temp.png')
            for plank in self.planks.keys():
                try:
                    region = pyscreeze.locate('./images/markers/plank_sawing/{}.png'.format(plank), './temp.png', grayscale=True, confidence=0.95)
                except:
                    region = None
                if region:
                    direction_list = self.planks[plank]
                    pyautogui.moveTo(direction_list[0])
                    for point in direction_list:
                        pyautogui.mouseDown()
                        pyautogui.moveTo(point)
                    pyautogui.mouseUp()
        print("Finished Sawing!")


if __name__ == "__main__":
    test = PlankSawing()
    test.repair()