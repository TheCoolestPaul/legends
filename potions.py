import collections
import time
import pyautogui
import keyboard
from mss import mss
import mss.tools as tools

Point = collections.namedtuple("Point", "x y")

def isDone() :
	screenshot = mss().grab({"top":238, "left":732, "width":479, "height":123})
	tools.to_png(screenshot.rgb, screenshot.size, output='./monitor-1.png')
	try:
		if pyautogui.locate("./images/potions/completed.png", "./monitor-1.png", grayscale=True):
			return True
	except:
		pass
	return False

def getCurrentPiece():
	pyautogui.moveTo(1001, 185)
	time.sleep(0.05)
	first = ""
	second = ""
	screenshot = mss().grab({"top":45, "left":933, "width":205, "height":155})
	tools.to_png(screenshot.rgb, screenshot.size, output='./monitor-1.png')
	if screenshot.pixel(56, 32)[2] > 150:
		first = "B"
	elif screenshot.pixel(56, 32)[0] > 100:
		first = "R"
	elif screenshot.pixel(56, 32)[1] > 100:
		first = "G"
	if screenshot.pixel(125, 74)[2] > 150:
		second = "B"
	elif screenshot.pixel(125, 74)[0] > 100:
		second = "R"
	elif screenshot.pixel(125, 74)[1] > 100:
		second = "G"

	return first+second

if __name__ == "__main__":
	columns = {
		"GG": Point(x=1044, y=135),
		"GB": Point(x=1117, y=168),
		"BB": Point(x=1182, y=141),
		"BR": Point(x=1249, y=126),
		"RR": Point(x=1375, y=134),
		"RG": Point(x=1450, y=120)
	}
	currentPiece = getCurrentPiece()
	pyautogui.FAILSAFE = False
	while not isDone():
		if keyboard.is_pressed('`'):  # If the backtick key is pressed
			print("Backtick pressed, terminating process.")
			break  # Exit the loop, effectively ending the game
		currentPiece = getCurrentPiece()
		if currentPiece in columns:
			print("Found a good place for", currentPiece)
			pyautogui.moveTo(columns[currentPiece].x, columns[currentPiece].y)
			pyautogui.click(columns[currentPiece].x, columns[currentPiece].y)
		elif currentPiece == "none" or currentPiece == "":
			print("Currentpiece is none!")
		else:
			print("Nothing found, rotating piece.", currentPiece)
			pyautogui.moveTo(1037, 121)
			pyautogui.rightClick(1037, 121) # center of screen
		#time.sleep(0.05)
	
	print("Done! ", currentPiece)