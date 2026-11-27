# legends
A variety of scripts for automating different tasks in the game "The Legends of Pirates Online" (TLOPO for short)

## Overview
There is a ton of stuff inside this repository, most of which was just made on the fly as I was playing the game. Some of the most notable scripts are as follows:
  - overlay.pyw (a floating panel with hotkeys to start, stop and configure the repair and potion bots)
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

## Overlay
A small always-on-top panel for starting, stopping and configuring the bots without a console. Double-click `overlay.pyw`, or run:

```
.venv\Scripts\python -m overlay
```

Default hotkeys (rebind them in the panel's settings):

| Key | Action |
| --- | --- |
| F6 | show / hide the panel |
| F7 | start / stop the repair bot |
| F8 | start / stop the potion bot |
| F10 | start / stop the fishing bot |
| F11 | start / stop the poker bot |
| `` ` `` | stop a running bot (the bots' own kill switch) |

F9 is reserved for saving a debug frame. Each bot runs as its own process, using `.venv` if there is one. Only one bot runs at a time, since both drive the same game window, so starting one stops the other first. The panel shows each bot's last log line and its ship or brew count. Settings (input mode, time limit, ship/brew limits, recipes, `--record`/`--debug`) apply on the next start and are saved to `overlay/settings.json` (git-ignored). Closing the panel stops any running bot, and terminates it if it hasn't exited a few seconds later. Every bot run's output is also saved to `logs/<bot>-<date>-<time>.log` (git-ignored, kept 14 days).

### Filling up every potion
Tick **Fill up all potions** in the panel's Potions settings (or run `.venv\Scripts\python potions\brew_bot.py --fill`) and start the potion bot from the potion table. It works down the recipe list from the top:

- Each recipe is brewed again and again until the game says you hold the most of that potion ("Potions Maxed Out ... Continue anyway?"); the bot answers **No** and moves to the next recipe.
- Greyed-out recipes (locked, or for another island) are skipped, and so are recipes the planner expects to lose often (tick **Any tier** to play them anyway) and ones lost three times in a row.
- A lost game starts the same recipe again: the bot finds it in the list by its name.
- Once every recipe is full or skipped, it brews the first recipe in the list until stopped, answering **OK** to keep going for the reputation.

## Fishing bot
Stand on a fishing dock with the rod out (the fishing skill bar showing), then press F10 in the overlay or run:

```
.venv\Scripts\python fishing\fish_bot.py            (--max-fish N, --debug, --record)
```

It works in the background (the game can sit behind other windows) and only on normal fish; it won't click at all unless the fishing skill bar is up. Each catch:

- Cast at full power (one click starts the power bar, a second sets it near the top) and Sink Lure (5) to the bottom, where the better fish are.
- Wait there, then work the lure up a step at a time, held with Stall Lure (1) at each depth: fish swim side to side at their own depth and only bite a lure held still in front of them. A bite (the green "!") is hooked at once.
- Reel by holding the button and let go the moment the line loses any health (the gauge is read about every 10 ms while holding) or the red "!" shows, with Tug Line (4) to stop the fish; Heal Line (3) when the line runs low, Pull Fish (2) while it's healthy.
- On a catch: OK, "Take Small Items", then the trash can (Yes when it asks about binning a "specially dyed" item). A picture of each catch panel goes to `debug_frames/fishing_catches/`.

The game ends a fishing session after about a minute on the dock without a cast, so the bot casts again straight away. A stolen lure stops it (re-equip one from Lures); legendary fish aren't handled.

## Poker bot
Sit down at a Texas Hold'em table, then press F11 in the overlay or run:

```
.venv\Scripts\python poker\poker_bot.py            (--max-hands N, --debug, --record)
```

It works in the background and only acts on its own turn (when the action buttons show at the bottom). It reads its two cards and the board, deals the unseen cards at random several hundred times against the players still holding cards to estimate its chance of winning, then bets or raises with a strong hand, checks or calls with at least a fair share (1 in the number of players), and otherwise checks if that's free or folds. It never presses the Swap Card buttons (that's cheating). A card it can't read gets a debug frame and a check or fold. Every decision is logged to `logs/poker-<date>.jsonl`.

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
