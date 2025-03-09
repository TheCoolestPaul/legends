import pyautogui
import time
import keyboard
import mouse
from window_input import Window, Key

top_strike_point = (787, 420)
bot_strike_point = (787, 648)


def show_pixels(*args):
    px = pyautogui.position()
    print(px)

if __name__ == "__main__":
    mouse.on_click(show_pixels)
    keyboard.on_press_key("`", show_pixels)
    while True:
        pass

