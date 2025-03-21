import math
import time
import pyautogui
import keyboard
import cv2
import numpy as np
from mss import mss

# Load the "completed" image
completed_img = cv2.imread("./images/fishing/completed.png", cv2.IMREAD_GRAYSCALE)

def capture_screen(region):
    """Captures a region of the screen and returns an OpenCV image."""
    with mss() as sct:
        screenshot = sct.grab(region)
        img = np.array(screenshot)[:, :, :3]  # Remove alpha channel
        return img

def capture_health_bar():
    """Captures the health bar region from the left side of the screen."""
    region = {"top": 393, "left": 75, "width": 41, "height": 391}  # Adjust if needed
    return capture_screen(region)

def get_health_bar_height():
    """Returns the height of the visible health bar."""
    health_bar = capture_health_bar()
    hsv = cv2.cvtColor(health_bar, cv2.COLOR_BGR2HSV)

    # Detects any non-black color (actual health)
    mask = cv2.inRange(hsv, np.array([0, 50, 50]), np.array([180, 255, 255]))

    # Find the highest visible portion of the bar
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(cv2.boundingRect(cnt)[3] for cnt in contours) if contours else 0

def move_mouse_clockwise():
    """Moves the mouse in a fast circular motion while LCTRL is held."""
    radius = 350  # Adjust for smaller/larger circles
    center_x, center_y = pyautogui.position()  # Get current mouse position
    pyautogui.PAUSE=0.02

    angle = 0
    while keyboard.is_pressed("left ctrl"):  # Loop while LCTRL is held
        x = int(center_x + radius * math.cos(angle))
        y = int(center_y + radius * math.sin(angle))
        pyautogui.moveTo(x, y, duration=0.02)  # Fast movement
        angle += math.pi / 3  # Increase angle for smooth circular motion
        
        if angle >= 2 * math.pi:
            angle = 0  # Reset angle after a full circle
    pyautogui.moveTo(center_x, center_y)  # Return to original position``

def spam_click():
    """Rapidly clicks while Left Shift is held down."""
    while keyboard.is_pressed("left shift"):
        pyautogui.click()
        time.sleep(0.015)  # Adjust for faster/slower clicking

def main():
    print("Fishing bot started! Press ` to stop.")
    prev_health = get_health_bar_height()

    while True:
        if keyboard.is_pressed('`'):
            print("Backtick pressed, terminating process.")
            break

        if keyboard.is_pressed("left ctrl"):  # If LCTRL is held, move mouse in a circle
            move_mouse_clockwise()

        if keyboard.is_pressed("left shift"):  # If LSHIFT is held, spam click
            spam_click()

        current_health = get_health_bar_height()

        if current_health < prev_health:  # If health bar drops, stop reeling
            print("Health dropped! Stopping reel.")
            pyautogui.mouseUp(998, 831)

        prev_health = current_health  # Update reference

    print("Fishing complete!")

if __name__ == "__main__":
    main()
