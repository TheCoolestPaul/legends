import keyboard
import pyautogui
from repair_mini_game import *
import time


class BilgePumping(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.name = "bilge_pumping"
        self.desired = "top"
        self.button = Point(x=838, y=1221)

    def checkComplete(self):
        pyautogui.moveTo(100, 400)
        img = mss().grab({"top":1155, "left":755, "width":160, "height":136})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/bilge_pumping/bilge_pumping_completion.png", "./temp.png", grayscale=True):
                self.completed = True
            else:
                self.completed = False
        except:
            self.completed = False

    def isGameActive(self):
        img = mss().grab({"top":141, "left":1007, "width":536, "height":254})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/bilge_pumping/bilge_pumping.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def play(self):
        if not self.isGameActive():
            print("{} isn't active.".format(self.name))
            return
        top_strike_point = (953, 545)
        bot_strike_point = (961, 878)
        neutral_point = (982, 570)
        p1 = None
        p2 = None
        while not self.completed:
            if p1 is None:
                p1 = pixel(top_strike_point)
            if p2 is None:
                p2 = pixel(bot_strike_point)
            if p1[1] > p2[1]:
                while p1 == pixel(top_strike_point):
                    pass
                else:
                    pyautogui.click(neutral_point)
                    p1 = (0, 0, 0)
                    p2 = pixel(bot_strike_point)
            else:
                while p2 == pixel(bot_strike_point):
                    pass
                else:
                    pyautogui.click(neutral_point)
                    p2 = (0, 0, 0)
                    p1 = pixel(top_strike_point)
            if keyboard.is_pressed('`'):  # If the backtick key is pressed
                print("Backtick pressed, terminating process.")
                break  # Exit the loop, effectively ending the game
            self.checkComplete()
        print("Done pumping")


if __name__ == "__main__":
    test = BilgePumping()
    test.play()

