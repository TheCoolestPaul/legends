# legends
A variety of scripts for automating different tasks in the game "The Legends of Pirates Online" (TLOPO for short)

## Overview
There is a ton of stuff inside this repository, most of which was just made on the fly as I was playing the game. Some of the most notable scripts are as follows:
  - potions/ (automates the potion minigame, which allows for easily leveling new accounts; brew/, brew.py and potions.py are the older versions)
  - twins.py (allows afk farming of enemies for rare items)
  - repair/ (bilge_pumping.py, hull_bracing.py, hull_patching.py, hull_scrubbing.py, hammering.py, and plank_sawing.py) automates the minigames that appear when repairing your ship
  - chat.py overlays the tlopo in game chat over top of your foreground window (this includes over top of full screen games, in my case it was usually polybridge 2)
  - login_pirates.py was used for logging in multiple accounts (for reference the game is kind of buggy and prone to crashes, so if you had multiple accounts running you found yourself logging in way too often)

## Setup
Windows only.

```
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest
```

## Repair bots
Open the repair screen on a ship, then run:

```
.venv\Scripts\python repair\repair_bind.py
```

It finds the game window itself, picks whichever minigame is open (or clicks the next unfinished one), until every button has its green check, then waits for the next repair screen. Press `` ` `` (backtick) to stop at any time. It is a kill switch: the key is watched on its own thread, any held mouse button is released, and if the bot has not stopped 2 seconds later the process is killed. Ctrl+C or moving the mouse into a screen corner also stop it. A single game can be run with e.g. `python repair\hammering.py`.

The window can be any size and anywhere on screen: coordinates in `repair/` are written for a maximised 2560x1440 window and scaled to the live window by `repair/screen.py`.

## Potion bot
Open a potion at the brewing table (any recipe), then run:

```
.venv\Scripts\python potions\brew_bot.py
```

It reads the recipe, the board, the pair in hand and the next pair, simulates every drop (merges, chains, recipe pickups) one pair ahead, and plays the best one. When the potion is made it presses "Brew Again" (and OK if the game says the potion inventory is full) and keeps going; `--once` stops after one. On "Game Over" it presses Continue and stops. Same stop key, F9, `--record` and window handling as the repair bots, whose screen code it shares.

Game rules it plays by (worked out live, Nov 2026): 8 hex columns, odd ones half a cell lower, 9 pieces each (a 10th ends the game); a group of n >= 3 touching pieces of the same colour and tier becomes n - 2 pieces of the next tier, on the members touching the most others; a piece matching an open recipe slot is pulled off the board. After each move the bot compares the board with its prediction and saves a `mismatch` debug frame when they differ.

### Debug frames
The bots save the game area to `debug_frames/` (git-ignored) as a PNG plus a JSON file saying why and what the bot saw:

- automatically when something goes wrong: a game times out, won't open, crashes, finds no nails/leaks/dirt/plank for a while, the bracing solver finds no solution, or a bracing tile can't be read
- when you press **F9**
- about once a second during every game with `--record`, to collect frames for tests:

```
.venv\Scripts\python repair\repair_bind.py --record
```

To see what every detector makes of a saved frame:

```
.venv\Scripts\python repair\replay.py debug_frames\<frame>.png
```

Offline tests (run against screenshots in the repo, at 720p, 1080p and 1440p):

```
.venv\Scripts\python -m pytest tests
```
