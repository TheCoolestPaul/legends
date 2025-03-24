import math
import time
import win32api
import win32con
import pyautogui
import cv2
import numpy as np
from mss import mss
from repair_mini_game import *

class Hammering(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.name = "hammering"
        self.button = Point(x=1505, y=1229)
        self.monitor = {"top":436, "left":618, "width":743, "height":194}  # Screen area for nail detection
        self.nail_template = "./images/repair/hammering/nail_head.png"  # Reference nail image for detection

    def click_when_best(self, x, y):
        """Clicks on the nail when it's in the correct hammering state (red highlight)."""
        pyautogui.moveTo(x, y)
        with mss() as sct:
            screenshot = sct.grab({"top": y, "left": x, "width": 30, "height": 30})
        if screenshot.pixel(15, 15)[0] > 200:  # Detect red (best timing to hammer)
            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
            return True
        return False

    def find_nails(self):
        """Finds nails dynamically using OpenCV template matching and filters close detections."""
        with mss() as sct:
            img = np.array(sct.grab(self.monitor))  # Capture screen region as NumPy array
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)  # Convert to grayscale

        template = cv2.imread(self.nail_template, 0)
        if template is None:
            print("Error: Nail template image not found.")
            return []

        res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        threshold = 0.75
        loc = np.where(res >= threshold)

        detected_nails = []
        for y, x in zip(*loc):
            detected_nails.append((self.monitor["left"] + x, self.monitor["top"] + y))

        # Filter out nails that are too close to each other
        filtered_nails = []
        for x, y in detected_nails:
            if all(math.hypot(x - fx, y - fy) >= 20 for fx, fy in filtered_nails):
                filtered_nails.append((x, y))

        return [Point(x, y) for x, y in filtered_nails]


    def isGameActive(self):
        """Checks if the hammering mini-game is currently active."""
        with mss() as sct:
            img = sct.grab({"top": 141, "left": 1007, "width": 536, "height": 254})
        try:
            return pyautogui.locate("./images/markers/hammering/hammering.png", img, grayscale=True) is not None
        except OSError:
            return False

    def play(self):
        """Main gameplay loop: Finds nails and hammers them at the right time."""
        #if not self.isGameActive():
        #    print(f"{self.name} isn't active.")
        #    return

        print("Searching for nails...")
        nails = []
        while not nails:
            nails = self.find_nails()
            if not nails:
                time.sleep(0.5)  # Wait and retry
                print("No nails found. Retrying...")

        print(f"Found {len(nails)} nails. Hammering...")

        for nail in nails:
            while not self.click_when_best(nail.x+10, nail.y+5):
                time.sleep(0.05)  # Check frequently until it's the best moment to hammer

        print("Finished hammering.")

if __name__ == "__main__":
    test = Hammering()
    test.play()
