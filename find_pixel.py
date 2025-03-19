import pyautogui
import keyboard
import mouse
import time

def show_pixels():
    px = pyautogui.position()
    print(f"Point({px.x}, {px.y})")

lastPoint = None
def makeRegion(*args):
    global lastPoint
    if lastPoint is None:
        lastPoint = pyautogui.position()
        print("First point set. Click another point or press ` to define the region.")
    else:
        curPoint = pyautogui.position()
        top = min(lastPoint.y, curPoint.y)
        left = min(lastPoint.x, curPoint.x)
        width = abs(curPoint.x - lastPoint.x)
        height = abs(curPoint.y - lastPoint.y)

        print(f'Region: {{"top":{top}, "left":{left}, "width":{width}, "height":{height}}}')
        
        lastPoint = None  # Reset for new region selection

if __name__ == "__main__":
    mouse.on_click(show_pixels)
    keyboard.on_press_key("`", makeRegion)
    
    print("Script running. Click to show pixel positions, press ` to define regions, and press Esc to exit.")
    
    keyboard.wait("esc")  # Keeps the script running until Esc is pressed
