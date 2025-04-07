import os
import cv2
import numpy as np
import mss

# === Config ===
grid_size = 4
tile_width = 86
tile_height = 84
pad_x = 15
pad_y = 16
screen_top_left = (1086, 514)

# === Setup ===
os.makedirs("blocks", exist_ok=True)

# === Screen Capture ===
with mss.mss() as sct:
    monitor = {
        "top": screen_top_left[1],
        "left": screen_top_left[0],
        "width": (tile_width * grid_size) + (pad_x * (grid_size - 1)),
        "height": (tile_height * grid_size) + (pad_y * (grid_size - 1))
    }
    img = np.array(sct.grab(monitor))[:, :, :3]
    screenshot = sct.grab(monitor)
    mss.tools.to_png(screenshot.rgb, screenshot.size, output='temp.png')

# === Color Classifier ===
def get_tile_color(tile_img, row, col):
    # Crop center region of tile
    center = tile_img[tile_img.shape[0]//3:tile_img.shape[0]*2//3,
                      tile_img.shape[1]//3:tile_img.shape[1]*2//3]
    
    center_y = tile_img.shape[0] // 2
    center_x = tile_img.shape[1] // 2
    # Extract the RGB color of the center pixel
    center_pixel = tile_img[center_y, center_x]

    # Print or return the RGB color
    print(f"Center pixel RGB {row+1,col+1}: {center_pixel}")

    if center_pixel[2] > 55 and center_pixel[2] < 100:
        return "R"
    elif center_pixel[0] > 40 and center_pixel[0] < 60 and center_pixel[1] < 50 and center_pixel[2] < 50:
        return "B"
    elif center_pixel[0] < 15 and center_pixel[1] < 15 and center_pixel[2] < 15:
        return " "
    else:
        return "N"


# === Detect Grid ===
grid = []
positions = []

for row in range(grid_size):
    row_colors = []
    row_positions = []
    for col in range(grid_size):
        x = col * (tile_width + pad_x)
        y = row * (tile_height + pad_y)
        tile = img[y:y+tile_height, x:x+tile_width]

        # Save tile to blocks folder
        filename = f"blocks/block{row}-{col}.png"
        cv2.imwrite(filename, tile)

        center = tile[tile.shape[0]//3:tile.shape[0]*2//3,
                      tile.shape[1]//3:tile.shape[1]*2//3]
        filename = f"blocks/center{row}-{col}.png"
        cv2.imwrite(filename, center)

        # Classify and store position
        row_colors.append(get_tile_color(tile, row, col))
        screen_x = screen_top_left[0] + x + tile_width // 2
        screen_y = screen_top_left[1] + y + tile_height // 2
        row_positions.append((screen_x, screen_y))

    grid.append(row_colors)
    positions.append(row_positions)

# === Output ===
print("Detected Grid:")
for row in grid:
    print(row)
