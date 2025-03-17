import collections
import time
import pyautogui
import keyboard
from mss import mss
from PIL import Image
import os

Point = collections.namedtuple("Point", "x y")

# Predefined color thresholds for piece detection
COLOR_THRESHOLDS = {
	"B": lambda p: p[2] > 150,  # Blue
	"R": lambda p: p[0] > 100,  # Red
	"G": lambda p: p[1] > 100   # Green
}

# Define positions for each piece type
COLUMNS = {
	"GG": Point(1011, 140),
	"GB": Point(1117, 140),
	"BB": Point(1182, 140),
	"BR": Point(1249, 140),
	"RR": Point(1320, 140),
	"RG": Point(1416, 140)
}

def is_done():
	"""Check if the crafting process is completed by detecting the 'completed' image on screen."""
	with mss() as sct:
		screenshot = sct.grab({"top": 238, "left": 732, "width": 479, "height": 123})
		img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)  # Convert MSS screenshot to PIL

		image_path = os.path.abspath("./images/potions/completed.png")
		try:
			return pyautogui.locate(image_path, img, grayscale=True) is not None
		except pyautogui.ImageNotFoundException:
			return False

def get_current_piece():
	"""Detects the current piece based on color pixels in a predefined region."""
	pyautogui.moveTo(1017, 599)
	with mss() as sct:
		screenshot = sct.grab({"top": 45, "left": 933, "width": 205, "height": 155})

	first = next((color for color, check in COLOR_THRESHOLDS.items() if check(screenshot.pixel(56, 32))), "")
	second = next((color for color, check in COLOR_THRESHOLDS.items() if check(screenshot.pixel(125, 74))), "")

	return first + second if first and second else ""

def main():
	pyautogui.FAILSAFE = False
	start_time = time.time()  # Start the timer

	while not is_done():
		if keyboard.is_pressed('`'):
			print("Backtick pressed, terminating process.")
			break

		current_piece = get_current_piece()

		if current_piece in COLUMNS:
			print(f"Found a good place for {current_piece}")
			pyautogui.moveTo(COLUMNS[current_piece].x, COLUMNS[current_piece].y)
			pyautogui.click(COLUMNS[current_piece].x, COLUMNS[current_piece].y)
		elif not current_piece:
			print("Current piece is none!")
		else:
			print(f"Nothing found, rotating piece: {current_piece}")
			pyautogui.rightClick(1017, 599)  # Rotate piece

	elapsed_time = time.time() - start_time  # Calculate elapsed time
	minutes, seconds = divmod(int(elapsed_time), 60)  # Convert to minutes:seconds
	print(f"Done! Total time: {minutes}:{seconds:02d}")

if __name__ == "__main__":
	main()