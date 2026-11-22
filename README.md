# legends
A variety of scripts for automating different tasks in the game "The Legends of Pirates Online" (TLOPO for short)

## Overview
There is a ton of stuff inside this repository, most of which was just made on the fly as I was playing the game. Some of the most notable scripts are as follows:
  - the brew directory (automates the potion minigame which allows for easily leveling new accounts)
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

It finds the game window itself, picks whichever minigame is open (or clicks the next unfinished one), until every button has its green check, then waits for the next repair screen. Press `` ` `` (backtick) to stop at any time, or move the mouse into a screen corner. A single game can be run with e.g. `python repair\hammering.py`.

The window can be any size and anywhere on screen: coordinates in `repair/` are written for a maximised 2560x1440 window and scaled to the live window by `repair/screen.py`.

Offline tests (run against screenshots in the repo, at 720p, 1080p and 1440p):

```
.venv\Scripts\python -m pytest tests
```
