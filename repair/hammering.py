import math
import time
import keyboard
import pyautogui
from mss import mss
import mss.tools as tools
from repair_mini_game import *

class Hammering(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.name = "hammering"
        self.button = Point(x=1502, y=1223)
        self.clicked_coords = []  # List to store clicked coordinates

    def is_too_close(self, x, y):
        """Checks if the (x, y) coordinate is within 50 pixels of any already clicked coordinate."""
        for cx, cy in self.clicked_coords:
            distance = math.sqrt((cx - x) ** 2 + (cy - y) ** 2)
            if distance < 50:  # If the distance is less than 50 pixels, return True
                return True
        return False

    def click_when_best(self, x, y):
        """Clicks on the nail when it's in the correct hammering state (red highlight)."""
        pyautogui.moveTo(x, y)
        with mss.mss() as sct:
            screenshot = sct.grab({"top": y, "left": x, "width": 30, "height": 30})
        if screenshot.pixel(15, 15)[0] > 180:  # Detect red (best timing to hammer)
            pyautogui.leftClick(x, y)
            return True
        return False

    def find_nails(self):
        """Finds nails dynamically by searching for four exact RGB pixel matches in the center of the nail."""

        with mss.mss() as sct:
            img = sct.grab({"top":617, "left":848, "width":860, "height":46})
        
        detected_nails = []
        
        # Scan the image for matching pixel clusters
        for x in range(img.width):
            for y in range(img.height):
                new_px = img.pixel(x, y)
                above = img.pixel(x, y - 1)
                if x < img.width - 5 and y < img.height - 5 and x > 5 and y > 5 and (new_px[0] >= 120 and new_px[0] <= 200 and new_px[1] >= 130 and new_px[1] <= 200 and new_px[2] >= 120 and new_px[2] <= 200) and (above[0] <= 95 and above[0] >= 80 and above[1] <= 95 and above[1] >= 80 and above[2] <= 95 and above[2] >= 80):
                    if not self.is_too_close(img.left + x, img.top + y):  # Check if the point is not too close to any previous click
                        self.clicked_coords.append((img.left + x - 5, img.top + y - 2))  # Store the clicked coordinates
                        detected_nails.append((img.left + x - 5, img.top + y - 2))  # Store the detected nail coordinates

        return [Point(x, y) for x, y in detected_nails]

    def isGameActive(self):
        """Checks if the hammering mini-game is currently active."""
        with mss.mss() as sct:
            img = sct.grab({"top":165, "left":1029, "width":506, "height":83})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hammering/hammering.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def play(self):
        """Main gameplay loop: Finds nails and hammers them at the right time."""
        print("Playing Hammering!")
        pyautogui.PAUSE = 0.01  # Set a small pause between actions to avoid overwhelming the system
        
        while not self.isGameCompleted() and self.isGameActive():
            if keyboard.is_pressed('`'):
                print("Backtick pressed, terminating process.")
                break

            nails = []
            while not nails and self.isGameActive() and not self.isGameCompleted():
                nails = self.find_nails()
                if not nails or len(nails) > 10 or len(nails) < 3:
                    time.sleep(0.5)
                    print("Looking for nails...")
                    continue
            if self.isGameCompleted() or not self.isGameActive():
                print("Game completed or not active anymore.")
                return

            print(f"Found {len(nails)} nails. Hammering...")

            for nail in nails:
                attempts = 0
                while not self.click_when_best(nail.x, nail.y) and attempts < 100:
                    time.sleep(0.01)
                    attempts += 1

            self.clicked_coords = []

if __name__ == "__main__":
    test = Hammering()
    test.repair()
