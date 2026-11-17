# steam-doctor

A Claude Code plugin that gets games running through Steam on Linux (Proton), Steam Deck and Windows. Tell Claude "get X running" and it checks ProtonDB, launches the game with logging, reads the logs and screenshots, and applies fixes until it works. It can also fix a Steam Deck or another PC over SSH.

## Install

In Claude Code:

```
/plugin marketplace add StephenDMay/steam-doctor
/plugin install steam-doctor@steam-doctor
```

Or from a shell: `claude plugin install steam-doctor --marketplace StephenDMay/steam-doctor`

Update later with `/plugin marketplace update steam-doctor`.

### Per platform

- **Linux:** Python 3 is already there. Optional: `protontricks` (distro package or Flatpak).
- **Steam Deck:** install Claude Code in Desktop Mode (`curl -fsSL https://claude.ai/install.sh | bash`). Or skip that and use remote mode from your PC: see `skills/steam-doctor/references/remote.md`.
- **Windows:** Python 3 (`winget install -e --id Python.Python.3.12`).

## steamctl

The helper works on its own too:

```
python3 skills/steam-doctor/scripts/steamctl.py --help
```

`info`, `list`, `show`, `tools`, `set-proton`, `set-launch`, `add`, `remove`, `run`, `logs`, `screenshot`, `steam-shots`, `reset-prefix`, `tricks`, `get-ge`, `steam start|stop|restart`.

Edits close Steam, back up the config file (`*.steamctl-bak-<time>`), then reopen Steam.

## Status and feedback

Tested on Arch Linux (Hyprland). Steam Deck and Windows support is written but not yet tested on real hardware. If something breaks, or a fix worked for a game, please open an issue with the output of `steamctl info` and the game's name.

## License

MIT
