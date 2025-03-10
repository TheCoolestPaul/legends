import pyautogui
import keyboard
import mouse

def show_pixels(*args):
    px = pyautogui.position()
    print(px)

lastPoint = None
def makeRegion(_):
    global lastPoint
    if lastPoint == None:
        lastPoint = pyautogui.position()
    else:
        curPoint = pyautogui.position()  # Get the current mouse position
        # Calculate top, left, width, and height
        top = min(lastPoint.y, curPoint.y)
        left = min(lastPoint.x, curPoint.x)
        width = abs(curPoint.x - lastPoint.x)
        height = abs(curPoint.y - lastPoint.y)
        
        # Print in the desired format
        print(f'{{"top":{top}, "left":{left}, "width":{width}, "height":{height}}}')
        
        # Reset lastPoint to None to define a new region on the next key press
        lastPoint = None 

if __name__ == "__main__":
    lastPoint = None
    mouse.on_click(show_pixels)
    keyboard.on_press_key("`", makeRegion)
    while True:
        pass
