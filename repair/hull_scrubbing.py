import keyboard
from repair_mini_game import *
import pyautogui
from mss import mss
import mss.tools as tools


class HullScrubbing(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.name = "hull_scrubbing"
        self.button = Point(x=834, y=1223)

    def checkComplete(self):
        pyautogui.moveTo(100, 400)
        img = mss().grab({"top": 1127, "left": 727, "width": 219, "height": 174})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hull_scrubbing/hull_scrubbing_completion.png", "./temp.png", grayscale=True):
                self.completed = True
            else:
                self.completed = False
        except:
            self.completed = False

    def isGameActive(self):
        img = mss().grab({"top":141, "left":1007, "width":536, "height":254})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hull_scrubbing/hull_scrubbing.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def play(self):
        pyautogui.PAUSE = 0.01
        if not self.isGameActive():
            print("{} isn't active.".format(self.name))
            return
        self.checkComplete()
        while not self.completed:
            if keyboard.is_pressed('`'):
                print("Backtick pressed, terminating process.")
                break
            pyautogui.moveTo(100, 400)
            img = mss().grab({"top": 420, "left": 775, "width": 834, "height": 582})

            placesToScrub = set()  # Stores unique 10x10 blocks
            checked_blocks = set()  # Tracks already checked 10x10 regions

            for x in range(img.width):
                for y in range(img.height):
                    new_px = img.pixel(x, y)
                    if new_px[0] > 150 and new_px[1] > 150 and new_px[2] > 150:
                        block_x = (x // 20) * 20
                        block_y = (y // 20) * 20
                        if (block_x, block_y) not in checked_blocks and len(placesToScrub) < 50:
                            placesToScrub.add((x + 775, y + 420))
                            checked_blocks.add((block_x, block_y))

            print('Found {} places to scrub'.format(len(placesToScrub)))
            for x, y in placesToScrub:
                if keyboard.is_pressed('`'):
                    print("Backtick pressed, terminating process.")
                    break
                pyautogui.moveTo(x, y)

            self.checkComplete()
        print("Finished scrubbing!")

if __name__ == "__main__":
    test = HullScrubbing()
    test.play()
