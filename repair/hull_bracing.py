import hashlib
import os
import numpy as np
import mss
import pyautogui
import time
import concurrent.futures
from queue import PriorityQueue
from threading import Lock
import mss.tools as tools
from repair_mini_game import *
import keyboard

class HullBracing(RepairMiniGame):
    def __init__(self):
        super().__init__()
        self.button = Point(x=1274, y=1227)
        self.grid_size = 4
        self.tile_width = 86
        self.tile_height = 84
        self.pad_x = 15
        self.pad_y = 16
        self.screen_top_left = (1086, 514)
        self.name = "hull_bracing"
        pyautogui.PAUSE = 0.01
        os.makedirs("blocks", exist_ok=True)

    def isGameCompleted(self):
        pyautogui.moveTo(100, 400)
        with mss.mss() as sct:
            img = sct.grab({"top":1158, "left":1190, "width":162, "height":136})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hull_bracing/hull_bracing_completion.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False
        
    def isGameActive(self):
        pyautogui.moveTo(100, 400)
        with mss.mss() as sct:
            img = sct.grab({"top":162, "left":1028, "width":505, "height":85})
        tools.to_png(img.rgb, img.size, output='./temp.png')
        try:
            if pyautogui.locate("./images/markers/hull_bracing/hull_bracing.png", "./temp.png", grayscale=True):
                return True
            else:
                return False
        except:
            return False

    def get_tile_color(self, tile_img):
        center_y = tile_img.shape[0] // 2
        center_x = tile_img.shape[1] // 2
        center_pixel = tile_img[center_y, center_x]
        if center_pixel[2] > 50 and center_pixel[2] < 150:
            return "R"
        elif center_pixel[0] > 40 and center_pixel[0] < 60 and center_pixel[1] < 50 and center_pixel[2] < 50:
            return "B"
        elif center_pixel[0] < 15 and center_pixel[1] < 15 and center_pixel[2] < 15:
            return " "
        else:
            return "O"

    def capture_grid(self):
        with mss.mss() as sct:
            monitor = {
                "top": self.screen_top_left[1],
                "left": self.screen_top_left[0],
                "width": (self.tile_width * self.grid_size) + (self.pad_x * (self.grid_size - 1)),
                "height": (self.tile_height * self.grid_size) + (self.pad_y * (self.grid_size - 1))
            }
            img = np.array(sct.grab(monitor))[:, :, :3]
            return img

    def detect_grid(self, img):
        grid = []
        positions = []
       
        for row in range(self.grid_size):
            row_colors = []
            row_positions = []
            for col in range(self.grid_size):
                x = col * (self.tile_width + self.pad_x)
                y = row * (self.tile_height + self.pad_y)
                tile = img[y:y+self.tile_height, x:x+self.tile_width]
                row_colors.append(self.get_tile_color(tile))
                screen_x = self.screen_top_left[0] + x + self.tile_width // 2
                screen_y = self.screen_top_left[1] + y + self.tile_height // 2
                row_positions.append((screen_x, screen_y))
            grid.append(row_colors)
            positions.append(row_positions)
        return grid, positions

    def hash_state(self, state):
        state_str = ''.join(state)
        return hashlib.md5(state_str.encode()).hexdigest()

    def solve_sliding_puzzle(self, initial_grid, num_threads=8):
        # Initialize shared variables
        global solution_found, shared_visited, visited_lock, flat_grid
        solution_found = False
        shared_visited = {}
        visited_lock = Lock()
        
        flat_grid = ''.join(''.join(row) for row in initial_grid)

        def worker(self, worker_id, start_f):
            global solution_found
            local_open = PriorityQueue()
            local_open.put((start_f, 0, flat_grid, []))
            
            while not local_open.empty() and not solution_found:
                _, g_cost, current_state, moves = local_open.get()
                
                if self.is_goal_state(current_state):
                    solution_found = True
                    return moves
                    
                with visited_lock:
                    if current_state in shared_visited and shared_visited[current_state] <= g_cost:
                        continue
                    shared_visited[current_state] = g_cost
                
                for next_state, move in self.get_next_states(current_state):
                    new_g = g_cost + 1
                    
                    with visited_lock:
                        if next_state in shared_visited and shared_visited[next_state] <= new_g:
                            continue
                    
                    f = new_g + self.heuristic(next_state) + (worker_id * 0.001)
                    local_open.put((f, new_g, next_state, moves + [move]))
            
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for i in range(num_threads):
                start_f = self.heuristic(flat_grid) + (i * 0.001)
                futures.append(executor.submit(worker, self, i, start_f))
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is not None:
                    return result
        
        return None

    def is_goal_state(self, grid):
        rows = [grid[i:i+4] for i in range(0, 16, 4)]
        return any('BBBB' in row for row in rows) and any('RRRR' in row for row in rows)

    def heuristic(self, grid):
        rows = [grid[i:i+4] for i in range(0, 16, 4)]
        
        best_b_count = 0
        best_r_count = 0
        b_row_idx = -1
        r_row_idx = -1
        
        for i, row in enumerate(rows):
            b_count = row.count('B')
            r_count = row.count('R')
            space_count = row.count(' ')
            o_count = row.count('O')
            
            if o_count == 0:
                if b_count > best_b_count and (b_count + space_count) >= 4:
                    best_b_count = b_count
                    b_row_idx = i
                if r_count > best_r_count and (r_count + space_count) >= 4:
                    best_r_count = r_count
                    r_row_idx = i
        
        b_moves_needed = 4 - best_b_count if best_b_count > 0 else 16
        r_moves_needed = 4 - best_r_count if best_r_count > 0 else 16
        
        distance_penalty = 0
        for i, row in enumerate(rows):
            if i != b_row_idx:
                b_count = row.count('B')
                distance_penalty += b_count * abs(i - (b_row_idx if b_row_idx != -1 else 0))
            if i != r_row_idx:
                r_count = row.count('R')
                distance_penalty += r_count * abs(i - (r_row_idx if r_row_idx != -1 else 0))
        
        completion_bonus = 0
        if best_b_count == 3:
            completion_bonus -= 5
        if best_r_count == 3:
            completion_bonus -= 5
        
        return b_moves_needed + r_moves_needed + (distance_penalty * 0.5) + completion_bonus

    def get_next_states(self, grid):
        states = []
        grid = list(grid)
        
        empty_positions = [i for i, c in enumerate(grid) if c == ' ']
        
        for pos in empty_positions:
            row, col = pos // 4, pos % 4
            
            for dr, dc in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                new_row, new_col = row + dr, col + dc
                
                if 0 <= new_row < 4 and 0 <= new_col < 4:
                    new_pos = new_row * 4 + new_col
                    if grid[new_pos] != ' ':
                        new_grid = grid.copy()
                        new_grid[pos], new_grid[new_pos] = new_grid[new_pos], new_grid[pos]
                        states.append((''.join(new_grid), (new_pos, pos)))
        
        return states

    def execute_moves(self, grid_positions, moves):
        for block_pos, empty_pos in moves:
            block_row, block_col = block_pos // 4, block_pos % 4
            empty_row, empty_col = empty_pos // 4, empty_pos % 4
            
            start_x, start_y = grid_positions[block_row][block_col]
            end_x, end_y = grid_positions[empty_row][empty_col]
            
            pyautogui.moveTo(start_x, start_y)
            pyautogui.mouseDown()
            pyautogui.moveTo(end_x, end_y, duration=0.02)
            pyautogui.mouseUp()
    def play(self):
        print("Playing Hull Bracing mini-game...")
        while not self.isGameCompleted() and self.isGameActive():
            img = self.capture_grid()
            grid, positions = self.detect_grid(img)
            
            print("\nCurrent grid state:")
            for row in grid:
                print(row)
            
            print("\nSolving puzzle...")            
            solution = self.solve_sliding_puzzle(grid, num_threads=4)
            
            if solution:
                print(f"Solution found! Moves required: {len(solution)}")
                self.execute_moves(positions, solution)
            else:
                print("No solution found. Retrying...")

if __name__ == "__main__":
    game = HullBracing()
    game.repair()