import collections
from window_input import Window, Key
import mouse
import keyboard
import time

Point = collections.namedtuple("Point", "x y")
Color = collections.namedtuple("Color", "r g b")

import collections

Point = collections.namedtuple("Point", "x y")
Color = collections.namedtuple("Color", "r g b")

RED = "Red"
GREEN = "Green"
BLUE = "Blue"
PURPLE = "Purple"
GOLD = "Gold"
ORANGE = "Orange"
COLORS = [RED, GREEN, BLUE, PURPLE, GOLD, ORANGE]


class Piece:
    up_next = [None, None]
    column_results = [None, None, None, None, None, None, None, None]

    def __init__(self, color):
        self.color = color

    def __str__(self):
        return self.color

    def __eq__(self, other):
        if isinstance(other, Piece):
            return self.color == other.color
        else:
            return self.color == other


class Red(Piece):
    up_next = [
        Color(r=131, g=38, b=35),
        Color(r=150, g=22, b=19)
    ]
    column_results = [
        Color(r=142, g=32, b=27),
        Color(r=149, g=26, b=11),
        Color(r=157, g=25, b=19),
        Color(r=152, g=25, b=9),
        Color(r=151, g=26, b=8),
        Color(r=156, g=24, b=8),
        Color(r=155, g=24, b=1),
        Color(r=141, g=30, b=34)
    ]

    def __init__(self):
        super().__init__(RED)


class Green(Piece):
    up_next = [
        Color(r=79, g=115, b=39),
        Color(r=105, g=149, b=64)
    ]
    column_results = [
        Color(r=86, g=118, b=47),
        Color(r=100, g=141, b=58),
        Color(r=112, g=153, b=78),
        Color(r=100, g=141, b=58),
        Color(r=88, g=124, b=40),
        Color(r=100, g=141, b=58),
        Color(r=86, g=123, b=40),
        Color(r=102, g=142, b=51)
    ]

    def __init__(self):
        super().__init__(GREEN)


class Blue(Piece):
    up_next = [
        Color(r=70, g=113, b=184),
        Color(r=57, g=74, b=115)
    ]
    column_results = [
        Color(r=82, g=121, b=198),
        Color(r=78, g=106, b=169),
        Color(r=55, g=78, b=119),
        Color(r=77, g=105, b=170),
        Color(r=88, g=119, b=190),
        Color(r=77, g=105, b=170),
        Color(r=86, g=116, b=188),
        Color(r=83, g=111, b=166)
    ]

    def __init__(self):
        super().__init__(BLUE)


class Purple(Piece):
    def __init__(self):
        super().__init__(PURPLE)


class Gold(Piece):
    def __init__(self):
        super().__init__(GOLD)


class Orange(Piece):
    def __init__(self):
        super().__init__(ORANGE)


COLOR_CLASSES = [Red, Green, Blue, Purple, Gold, Orange]



class Board:
    up_next_checks = [
        Point(x=726, y=144),
        Point(x=796, y=115)
    ]

    column_checks = [
        Point(x=1002, y=72),
        Point(x=1082, y=114),
        Point(x=1154, y=74),
        Point(x=1224, y=114),
        Point(x=1294, y=72),
        Point(x=1366, y=114),
        Point(x=1436, y=72),
        Point(x=1504, y=114)
    ]

    background_results = [
        Color(r=43, g=21, b=3),
        Color(r=45, g=24, b=5),
        Color(r=59, g=36, b=17),
        Color(r=47, g=25, b=13),
        Color(r=38, g=17, b=0),
        Color(r=48, g=25, b=15),
        Color(r=38, g=19, b=5),
        Color(r=53, g=32, b=20)
    ]

    placements = [
        Point(x=1042, y=304),
        Point(x=1128, y=304),
        Point(x=1186, y=306),
        Point(x=1264, y=312),
        Point(x=1330, y=314),
        Point(x=1398, y=320),
        Point(x=1474, y=340)
    ]

    def __init__(self, win=Window(), pattern=(RED, RED, GREEN, GREEN, BLUE, BLUE, RED, None)):
        self.win = win
        self.pattern = pattern
        self.history = []
        self.start = None
        self.rep = 0
        self.times = []

    def __str__(self):
        return "{}, {}, {}, {}, {}, {}, {}, {}".format(*self.columns)

    @property
    def pair(self):
        pair = []
        columns = self.columns
        if columns:
            for val in columns:
                if val:
                    pair.append(val)
            return pair
        else:
            return False

    @property
    def complete(self):
        if not keyboard.is_pressed("`"):
            return self.win.pixel(1222, 683) == Color(255, 255, 255)
        else:
            time.sleep(999)

    @property
    def columns(self):
        while not self.complete:
            columns = [None, None, None, None, None, None, None, None]
            for i, point in enumerate(self.column_checks):
                px = self.win.pixel(point)
                if px != self.background_results[i]:
                    for color in COLOR_CLASSES:
                        if color.column_results[i] == px:
                            columns[i] = color().color
            else:
                passing = 0
                for val in columns:
                    if val:
                        passing += 1
                if passing == 2:
                    return columns
        else:
            return False

    def place(self, column_nums):
        self.maintain_spot_until_full(column_nums)
        mouse.click()
        time.sleep(.2)
        self.maintain_spot_until_full(column_nums)

    def maintain_spot_until_full(self, column_nums):
        columns = self.columns
        while columns and not(columns[column_nums[0]] and columns[column_nums[1]]):
            mouse.move(*self.placements[column_nums[0]])
            columns = self.columns

    def swap(self):
        keyboard.press("space")
        time.sleep(.1)
        keyboard.release("space")

    def solve(self):
        self.start = time.time()
        while not self.complete:
            placed = False
            pair = self.pair
            if pair:
                for i in range(7):
                    if self.pattern[i] == pair[0] and self.pattern[i+1] == pair[1]:
                        self.place((i, i+1))
                        placed = True
                if not placed:
                    self.swap()
        else:
            self.times.append(time.time()-self.start)
            self.rep += 580
            print("--------------------------------------------------")
            print("Round {} - {}".format(len(self.times), self.times[-1]))
            print("Running Average - {}".format(sum(self.times)/len(self.times)))
            print("Total Rep Gained - {}".format(self.rep))
            mouse.move(1236, 687)
            mouse.click()
            time.sleep(.1)
            mouse.release()
            time.sleep(.5)


def select_swift_three(win):
    for _ in range(2):
        mouse.move(1452, 834)
        mouse.click()
        time.sleep(.1)
        mouse.release()
        time.sleep(.1)
    time.sleep(.1)
    mouse.move(*Point(x=1157, y=840))
    mouse.click()
    time.sleep(.1)
    mouse.release()
    if win.pixel(1156, 493) == (255, 255, 255):
        time.sleep(.5)
        mouse.move(1208, 574)
        mouse.click()
        time.sleep(.1)
        mouse.release()


def select_cannon_three(win):
    for _ in range(2):
        mouse.move(1452, 834)
        mouse.click()
        time.sleep(.1)
        mouse.release()
        time.sleep(.1)
    time.sleep(.1)
    mouse.move(1159, 768)
    mouse.click()
    time.sleep(.1)
    mouse.release()
    if win.pixel(1156, 493) == (255, 255, 255):
        time.sleep(.5)
        mouse.move(1208, 574)
        mouse.click()
        time.sleep(.1)
        mouse.release()


def select_marksman_three(win):
    time.sleep(.2)
    mouse.move(*Point(x=1157, y=840))
    mouse.click()
    if win.pixel(1156, 493) == (255, 255, 255):
        time.sleep(.5)
        mouse.move(1208, 574)
        mouse.click()


if __name__ == "__main__":
    time.sleep(2)
    board = Board(Window())
    while True:
        select_swift_three(board.win)
        board.solve()
