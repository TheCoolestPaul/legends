import pyautogui
from repair_mini_game import *
from mss import mss
import mss.tools as tools
import keyboard  # Import the keyboard module
import math  # To calculate distance

class HullPatching(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.needed = 7
        self.button = Point(x=1708, y=1218)
        self.name = "hull_patching"
        self.clicked_coords = []  # List to store clicked coordinates

    def is_too_close(self, x, y):
        """Checks if the (x, y) coordinate is within 10 pixels of any already clicked coordinate."""
        for cx, cy in self.clicked_coords:
            distance = math.sqrt((cx - x) ** 2 + (cy - y) ** 2)
            if distance < 10:  # If the distance is less than 10 pixels, return True
                return True
        return False

    def isGameActive(self):
        img = mss().grab({"top":141, "left":1007, "width":536, "height":254})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hull_patching/hull_patching.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def play(self):
        print("Playing Hull Patching!")
        pyautogui.PAUSE = 0.01
        while not self.isGameCompleted() and self.isGameActive():
            if keyboard.is_pressed('`'):  # If the backtick key is pressed
                print("Backtick pressed, terminating process.")
                break  # Exit the loop, effectively ending the game
            
            pyautogui.moveTo(100, 400)
            img = mss().grab({"top": 420, "left": 851, "width": 852, "height": 580})
            tools.to_png(img.rgb, img.size, output='./temp.png')

            for x in range(img.width):
                for y in range(img.height):
                    new_px = img.pixel(x, y)
                    above = img.pixel(x, y - 1)
                    if x < 852 - 10 and y < 591 - 10 and x > 10 and y > 10 and new_px[2] > 100 and new_px[1] < 100 and new_px[0] < 100 and (above[0] < 10 and above[1] < 10 and above[2] < 10):
                        if not self.is_too_close(851 + x, 415 + y):  # Check if the point is not too close to any previous click
                            pyautogui.click(851 + x, 415 + y)
                            self.clicked_coords.append((851 + x, 415 + y))  # Store the clicked coordinates
                        break
            self.clicked_coords = []
        print("Finished Patching!")

if __name__ == "__main__":
    test = HullPatching()
    test.repair()
