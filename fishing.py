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
    """Detects the fishing completion screen in the right half of the screen and extracts details."""
    screen_width, screen_height = pyautogui.size()

    # Capture only the right half of the screen
    screen = capture_screen({"top": 0, "left": screen_width // 2, "width": screen_width // 2, "height": screen_height})
    gray_screen = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)

    # Use ORB to detect the "completed" image
    orb = cv2.ORB_create()
    keypoints1, descriptors1 = orb.detectAndCompute(completed_img, None)
    keypoints2, descriptors2 = orb.detectAndCompute(gray_screen, None)

    if descriptors1 is None or descriptors2 is None:
        return False  

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(descriptors1, descriptors2)

    if len(matches) > 10:
        return True

    return False

def detect_red_exclamation(debug=False):
    """Detects a deep red exclamation mark while ignoring orange tones.
       If debug=True, displays the processed mask to visualize detected red areas.
    """
    screen = capture_screen({"top": 114, "left": 310, "width": 1354, "height": 811})

    # Convert to HSV
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)

    # Adjusted Red Ranges (Deeper Red, Less Orange)
    lower_red1 = np.array([0, 200, 200])   # Deeper, richer reds
    upper_red1 = np.array([5, 255, 255])  
    lower_red2 = np.array([175, 200, 200])  # Second red range at the other end
    upper_red2 = np.array([180, 255, 255])

    # Create masks for both red ranges
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask = cv2.bitwise_or(mask1, mask2)

    # Noise Reduction: Morphological Operations
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=2)

    # Find contours in the red mask
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if debug:
        # Display the detected red regions
        debug_display(mask)

    for cnt in contours:
        area = cv2.contourArea(cnt)
        x, y, w, h = cv2.boundingRect(cnt)  # Bounding box

        # Check if it's tall enough (verticality) and within area range
        if 100 < area < 4000 and h > w * 1.5:  # Ensure height is at least 1.5x width
            return True  # Exclamation detected

    return False  # No exclamation found

def debug_display(mask):
    """Displays the mask with detected red areas in a window for debugging."""
    cv2.imshow("Red Detection Debug", mask)
    cv2.waitKey(1)  # Refresh window quickly

def check_health_bar():
    """Stops reeling if the health bar decreases at all."""
    global previous_height
    current_height = get_health_bar_height()

    if current_height < previous_height:  # Bar has shrunk
        print("Health bar dropped! Stopping reel.")
        pyautogui.mouseUp(998, 831)  # Stop reeling
        previous_height = current_height  # Update tracked height
        return False

    previous_height = current_height  # Update for next check
    return True


def capture_health_bar():
    """Captures the health bar region from the left side of the screen."""
    region = {"top":297, "left":55, "width":28, "height":284} # Adjust these values
    screen = capture_screen(region)
    return screen


def get_health_bar_height():
    """Returns the height of the visible health bar."""
    health_bar = capture_health_bar()
    hsv = cv2.cvtColor(health_bar, cv2.COLOR_BGR2HSV)

    # Define non-black mask (detects any color except black)
    mask = cv2.inRange(hsv, np.array([0, 50, 50]), np.array([180, 255, 255]))

    # Find contours of the remaining health bar
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if contours:
        # Get the highest contour (tallest part of the bar)
        _, y, _, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        return h  # Return height of the bar
    return 0  # If no bar detected, assume it's empty

previous_height = get_health_bar_height()  # Initialize height tracking

def main():
    start_time = time.time()
    reeling = False

    print("Fishing bot started! Press ` to stop.")

    # Start reeling initially
    print("Reeling started!")
    pyautogui.mouseDown(998, 831)
    reeling = True

    while not is_done():  # Continue until fishing is complete
        if keyboard.is_pressed('`'):
            print("Backtick pressed, terminating process.")
            break

        if check_health_bar():  # Stop if the bar moves at all
            if not reeling:
                print("No exclamation, and line is stable. Reeling again.")
                pyautogui.mouseDown(998, 831)
                reeling = True

        #if detect_red_exclamation():
        #    if reeling:
        #        print("Exclamation detected! Stopping reel.")
        #        pyautogui.mouseUp(998, 831)
        #        reeling = False
        #else:
        #    if not reeling:
        #        print("No exclamation detected. Reeling again.")
        #        pyautogui.mouseDown(998, 831)
        #        reeling = True

        #time.sleep(0.05)

    elapsed_time = time.time() - start_time
    minutes, seconds = divmod(int(elapsed_time), 60)
    print(f"Done! Total time: {minutes}:{seconds:02d}")

if __name__ == "__main__":
    main()
