---
name: steam-doctor
description: Get PC games running through Steam on Linux (Proton), Steam Deck (SteamOS) or Windows, locally or on another machine over SSH. Use when a game won't launch, crashes, shows a Wine/Proton error, runs badly, or needs to be added to Steam as a non-Steam game; when choosing or installing a Proton version (Experimental, GE-Proton); setting launch options; installing runtimes with protontricks; or fixing a game on a Steam Deck or another PC. Triggers: Steam, Proton, Wine, Steam Deck, "game won't start", "get X running", non-Steam game, launcher (Battle.net, EA app, Ubisoft Connect, Epic), anti-cheat, ProtonDB.
---

# Steam Doctor

Goal: the user names a game (or a problem), and you get it running with as little back-and-forth as possible. You drive; the user only does things you physically can't (clicking inside the game, logging into accounts, entering sudo passwords).

## The helper: steamctl

`scripts/steamctl.py` in this skill's base directory. Pure Python stdlib, same commands on every platform. Run it with `python3` on Linux/Deck, `python` or `py` on Windows.

| Command | What it does |
| --- | --- |
| `info [--json]` | Platform, Steam path (native/Flatpak/Windows), running state, Deck Game Mode, installed Proton versions, helper tools |
| `list [--json]` | Every game and non-Steam shortcut with appid, forced Proton, launch options |
| `show <game>` | One game: Proton setting, launch options, prefix path, Proton version the prefix was built with, exe |
| `tools` | Compat tool names valid for `set-proton` |
| `set-proton <game> <tool\|none>` | Force a Proton version (Linux only) |
| `set-launch <game> "<opts>"` | Set launch options (`""` clears) |
| `add <exe> [--name N] [--proton T] [--launch L] [--start-dir D]` | Add a non-Steam game |
| `edit-shortcut <game> [--exe E] [--name N] [--start-dir D]` | Repoint a shortcut (installer → installed launcher) and keep its prefix |
| `remove <game>` | Remove a non-Steam shortcut |
| `run <game>` | Launch through Steam (starts Steam if needed) |
| `logs <game> [--errors] [--lines N]` | Proton log (`~/steam-<appid>.log`) plus Steam log lines about the game |
| `screenshot [--out PATH]` | Screenshot the desktop and print the path, then Read it to see the screen |
| `steam-shots` | Newest screenshots taken with Steam's own key (works in Deck Game Mode) |
| `stop-game <game>` | End everything in the game's Proton prefix, including launcher agents that keep Steam showing "Running" |
| `reset-prefix <game>` | Move the game's Proton prefix to a timestamped backup |
| `tricks <game> <verbs...>` | protontricks in the game's prefix (`vcrun2022`, `d3dcompiler_47`, `dotnet48`...) |
| `get-ge [--version TAG]` | Download and checksum-verify GE-Proton into `compatibilitytools.d` |
| `steam status\|stop\|start\|restart` | Control the Steam client |

## Where you're running

Run `steamctl info` first. Its `platform` field (`linux`, `steamdeck`, `windows`) tells you which playbook applies. All three are first-class: run steamctl locally on whatever machine Claude Code is on.

- **Linux desktop:** `python3 <base>/scripts/steamctl.py ...`
- **Steam Deck, natively:** Claude Code runs in Desktop Mode (Konsole). `python3` is built into SteamOS, and the `steam` command and Steam paths are found on their own. Everything you install goes in the home folder (Flatpak `--user`, `~/.local/bin`), because the system is read-only. If Claude Code was started from Game Mode (Konsole added as a non-Steam app), steamctl refuses to close Steam. Closing Steam would kill this session too.
- **Windows, natively:** the Bash tool is Git Bash. Run `python <base>/scripts/steamctl.py ...` (or `py`). If `python` opens the Microsoft Store or isn't found, ask the user to run `winget install -e --id Python.Python.3.12` and restart Claude Code. steamctl finds Steam through the registry, and Proton-only commands (`set-proton`, `tricks`, `get-ge`) tell you they don't apply.
- **Another machine:** over SSH, see the last section.

`<game>` is an appid or part of the name. Editing commands close Steam, back the file up as `<file>.steamctl-bak-<time>`, and reopen Steam. Steam rewrites its config while running, so never hand-edit `config.vdf`, `localconfig.vdf` or `shortcuts.vdf` while it's up. Use steamctl.

## Workflow

1. **Orient.** `steamctl info`, then `steamctl show <game>`. Note the platform, Proton forced or not, launch options, and which Proton built the prefix.
2. **Rule out dead ends before fixing anything.** Look the game up on ProtonDB (`https://www.protondb.com/api/v1/reports/summaries/<appid>.json` gives the tier; the web page has user reports with fixes) and check anti-cheat at areweanticheatyet.com. If the game's anti-cheat blocks Linux (Valorant, recent Call of Duty, Battlefield with Javelin, Fortnite, most kernel anti-cheat), say so right away and stop. No fix exists on Linux or Deck.
3. **Reproduce with evidence.** On Linux, make sure launch options contain `PROTON_LOG=1` (prepend it, keeping existing options and `%command%`), `steamctl run <game>`, wait for it to fail, then `steamctl logs <game> --errors` and `steamctl screenshot` (Read the PNG). Ask the user what they saw only if the evidence is unclear.
4. **Fix one thing at a time**, cheapest first, re-running after each. The per-platform playbook has the order:
   - Linux and Steam Deck: [references/linux-proton.md](references/linux-proton.md)
   - Windows: [references/windows.md](references/windows.md)
5. **Confirm and clean up.** Once it runs, remove `PROTON_LOG=1` (logs get huge), and tell the user what fixed it in one line.

## Another machine (Steam Deck, Windows PC)

If the user says "on my Deck" or names another PC, run everything over SSH. steamctl needs no install there: `ssh <host> python3 - <command> < steamctl.py`. Setup, screenshots and quirks: [references/remote.md](references/remote.md).

## Rules

- **Say before you close Steam.** Edits restart the Steam client. If a game is running (`steamctl show <game>` has a `running` field), warn first. Never detect a game with `pgrep -f <appid>`: it matches your own shell's command line. Downloads pause and resume on their own.
- **Steam Deck Game Mode:** steamctl refuses to close Steam there, because Steam is the whole session. Ask the user to switch to Desktop Mode (Steam button, Power, Switch to Desktop), or give them the exact in-Steam clicks instead.
- **Prefixes hold saves.** Some games keep saves inside the prefix. `reset-prefix` keeps a backup, but tell the user before running it, and never delete a `.bak` prefix yourself.
- **Don't fight launchers' own updaters.** Battle.net, EA app and similar update themselves inside the prefix. Let them finish before judging a crash.
- **Root access:** pacman, apt or dnf need sudo, so ask the user to run those (`! sudo ...`). On the Deck, the system is read-only: never run `steamos-readonly disable`. Use Flatpak (`flatpak install --user flathub com.github.Matoking.protontricks`) or single-file tools in `~/.local/bin` instead.
