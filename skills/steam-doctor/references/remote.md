# Remote mode: fix another machine over SSH

You run on one machine (usually the user's main PC) and drive Steam on another: a Steam Deck or a Windows PC. steamctl isn't installed there. Pipe it in each time, so the remote side always runs the same version:

```bash
STEAMCTL=<this skill's base directory>/scripts/steamctl.py
ssh deck@steamdeck python3 - list < "$STEAMCTL"
ssh deck@steamdeck python3 - show "Elden Ring" < "$STEAMCTL"
```

Windows target: `ssh user@pc python - list < "$STEAMCTL"`.

Arguments with spaces need two layers of quoting, because ssh joins arguments into one remote shell line: `ssh deck@steamdeck python3 - set-launch 1245620 "'PROTON_LOG=1 %command%'" < "$STEAMCTL"`.

## One-time setup: Steam Deck

The user does this on the Deck, in Desktop Mode, in Konsole:

1. `passwd` to set a password for user `deck` (there's none by default).
2. `sudo systemctl enable --now sshd`
3. `ip -4 addr show wlan0` for the IP. `steamdeck.local` usually works too (mDNS).

Then, from this machine:

- `ssh-copy-id deck@steamdeck.local` so later commands don't prompt. The user types the password once; suggest `! ssh-copy-id deck@steamdeck.local`.
- Optionally add a `Host deck` entry to `~/.ssh/config` so it's `ssh deck`.

sshd survives SteamOS updates. The `deck` password and keys stay in `/home`, which updates don't touch.

## One-time setup: Windows PC

On the Windows PC, admin PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd; Set-Service sshd -StartupType Automatic
```

Python must be on PATH for the user that logs in (`winget install -e --id Python.Python.3.12`). For key login on an admin account, the public key goes in `C:\ProgramData\ssh\administrators_authorized_keys`, not the user profile.

GUI caveat: SSH sessions on Windows run in a separate session, so `screenshot` captures nothing useful, and `run` may start Steam invisibly. Prefer telling the user to launch the game, then read logs and the Event Log over SSH.

## Seeing the remote screen

- **Deck Desktop Mode:** `ssh deck python3 - screenshot --out /tmp/shot.png < "$STEAMCTL"`, then `scp deck:/tmp/shot.png <scratchpad>/` and Read it. steamctl fills in the Wayland and D-Bus variables an SSH session lacks.
- **Deck Game Mode:** ask the user to press Steam + R1 when the problem is on screen, then `steam-shots` to find the file and scp it.
- Pull Proton logs the same way: `scp deck:steam-<appid>.log <scratchpad>/`, or use `logs <game> --errors` remotely.

## Clicking and typing on the Deck

When a dialog needs a click (installer Continue, a launcher popup), drive it yourself instead of handing it to the user. Wine windows are X11 windows under XWayland, and `xdotool` ships with SteamOS:

```bash
ssh deck 'export DISPLAY=:0 XAUTHORITY=$(ls /run/user/1000/xauth_* | head -1)
  xdotool search --onlyvisible --name "." | while read w; do echo "$w $(xdotool getwindowname $w) $(xdotool getwindowgeometry $w | tail -1)"; done'
ssh deck 'export DISPLAY=:0 XAUTHORITY=$(ls /run/user/1000/xauth_* | head -1); xdotool key --window <id> Return'
```

- **Send keys to the window, don't click.** `xdotool key --window <id>` goes straight to that window. Mouse clicks (`mousemove ... click 1`) go through the compositor and often land on the window behind, even after `windowraise`/`windowactivate`. Enter presses a dialog's default button (Battle.net's install Continue, its "Scan For Games" Done). Tab moves between controls.
- Screenshot after every action to confirm it landed.
- Several windows can share a title; pick by size from the listing.
- Logins stay with the user. Never type a password for them.

## Desktop Mode input loss

While a non-Steam Proton app runs in Deck Desktop Mode, the user's trackpads **and** touchscreen can stop working entirely (seen with the Battle.net installer). Holding ☰ doesn't fix it. Holding the Steam button while using the right trackpad gives mouse control back (reported by a user, not confirmed to fix every case). Work around it rather than fighting it:

- Drive dialogs with `xdotool key --window` (above).
- Have the user log in and play in **Game Mode**, where touch, trackpads and the on-screen keyboard behave normally.

## Restarting Steam remotely

- SteamOS kills every process started from an SSH session when the connection closes, even `setsid`/`nohup` ones. steamctl handles this: over SSH it starts Steam and `steam://` launches through `systemd-run --user`, with session variables (`XAUTHORITY` included) copied from the running desktop. If you start a GUI program by hand over SSH, do the same, or it dies when your command returns.
- `wayland init failed` and `XOpenIM() failed` in Steam's console log are normal on the Deck. They aren't the cause of anything.
- Deck Desktop Mode: edits work and Steam reopens on the Deck's screen.
- Deck Game Mode: steamctl refuses to edit, because closing Steam would end the session. Ask the user to switch to Desktop Mode, or walk them through the change in the Steam UI.
- Windows: Steam started from SSH may not appear on the desktop. After editing, ask the user to start Steam themselves, or use `--no-restart` with Steam closed on their side.

## Protontricks and GE-Proton on the Deck

- `get-ge` works remotely as-is: it downloads straight to the Deck.
- protontricks: `ssh deck flatpak install -y --user flathub com.github.Matoking.protontricks`, then `tricks` remotely.
