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

def is_done():
    """Check if the fishing process is completed by detecting the 'completed' image on screen."""
    screen = capture_screen({"top": 206, "left": 29, "width": 94, "height": 80})
    gray_screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray_screen, completed_img, cv2.TM_CCOEFF_NORMED)
    return np.max(res) > 0.8

def detect_red_exclamation():
    """Detects a bright red exclamation mark using HSV filtering & contour detection."""
    screen = capture_screen({"top": 114, "left": 310, "width": 1354, "height": 811})

    # Convert to HSV and extract red color
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
    lower_red1 = np.array([0, 150, 150])  # Red color range (bright red)
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([170, 150, 150])  # Red range at other end of hue spectrum
    upper_red2 = np.array([180, 255, 255])

    # Create masks for both red ranges and combine them
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    # Apply morphological operations to remove noise
    kernel = np.ones((3,3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=2)

    # Find contours in the red mask
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if 300 < area < 5000:  # Adjust this range based on exclamation size
            return True  # Exclamation detected

    return False  # No exclamation found

def main():
    start_time = time.time()
    reeling = False

    print("Fishing bot started! Press ` to stop.")

    # Start reeling initially
    print("Reeling started!")
    pyautogui.mouseDown(1012, 473)
    reeling = True

    while not is_done():  # Continue until fishing is complete
        if keyboard.is_pressed('`'):
            print("Backtick pressed, terminating process.")
            break

        if detect_red_exclamation():
            if reeling:
                print("Exclamation detected! Stopping reel.")
                pyautogui.mouseUp(1012, 473)
                reeling = False
        else:
            if not reeling:
                print("No exclamation detected. Reeling again.")
                pyautogui.mouseDown(1012, 473)
                reeling = True

        time.sleep(0.05)

    elapsed_time = time.time() - start_time
    minutes, seconds = divmod(int(elapsed_time), 60)
    print(f"Done! Total time: {minutes}:{seconds:02d}")

if __name__ == "__main__":
    main()
