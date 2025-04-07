import os
import cv2
import numpy as np
import mss

# Constants
grid_size = 4
tile_width = 86 
tile_height = 84
pad_x = 15
pad_y = 16
screen_top_left = (1086, 514)

# Make output folder
os.makedirs("blocks", exist_ok=True)

# Capture puzzle area
with mss.mss() as sct:
    monitor = {"top": screen_top_left[1], "left": screen_top_left[0], "width": 395, "height": 394}
    img = np.array(sct.grab(monitor))[:, :, :3]

# Save each tile
for row in range(grid_size):
    for col in range(grid_size):
        x = col * (tile_width + pad_x)
        y = row * (tile_height + pad_y)
        tile = img[y:y+tile_height, x:x+tile_width]

        # Save to blocks/blockX-blockY.png
        path = f"blocks/block{row}-{col}.png"
        cv2.imwrite(path, tile)

print("All blocks saved to /blocks folder.")
