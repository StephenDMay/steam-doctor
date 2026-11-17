# Linux and Steam Deck playbook

Work down the list. Re-run the game after each change. Undo a change that didn't help before trying the next, so you know what fixed it.

## 1. Proton version

- Native Linux game failing? Try forcing Proton anyway (`set-proton <game> proton_experimental`). Native ports are often worse than the Windows build.
- Default order to try: **Proton Experimental**, then **GE-Proton** (`get-ge`, then `set-proton <game> GE-Proton<x>`, restarting Steam so it sees the new tool), then the previous stable Proton (`proton_9`, `proton_10`...). Use `tools` for names valid on this machine.
- GE-Proton includes media codecs (cutscenes that show black or test patterns) and many game-specific fixes. Reach for it early for video problems and for launchers.
- Switching between major Proton versions can corrupt a prefix. If a game broke right after a switch, `reset-prefix`.

## 2. Launch options

Always keep `%command%`. Combine variables: `PROTON_LOG=1 DXVK_ASYNC=1 %command%`.

| Symptom | Try |
| --- | --- |
| Crash at start, DX12 game | `VKD3D_CONFIG=dxr11 %command%`, or force DX11 with `%command% -dx11` (game-specific flag) |
| Crash at start, DX11 game | `PROTON_USE_WINED3D=1 %command%` (slow, but a good diagnostic) |
| Launcher window black or blank (CEF/Chromium launchers: Battle.net, EA app, Rockstar, Ubisoft) | `PROTON_USE_WINED3D=1 %command%`, or GE-Proton |
| Black screen with sound | `PROTON_ENABLE_NVAPI=1` (NVIDIA), `-windowed` / `-fullscreen` flags, or run in gamescope: `gamescope -W 1920 -H 1080 -f -- %command%` |
| No sound or crackling | `PULSE_LATENCY_MSEC=60 %command%`, or `WINE_AUDIODRIVER=pulse` |
| Stutter while shaders compile | Let Steam's shader pre-cache run, `DXVK_ASYNC=1` (GE-Proton) |
| Game needs ray tracing or DLSS on NVIDIA | `PROTON_ENABLE_NVAPI=1 %command%` |
| Overlay causes a crash | Turn off the Steam overlay for the game; for non-Steam launchers it injects into Chromium |
| Wrong GPU on a hybrid laptop | `DRI_PRIME=1 %command%` (AMD/Intel), `prime-run %command%` (NVIDIA) |
| Performance overlay wanted | `mangohud %command%` |
| Want feral gamemode | `gamemoderun %command%` |

## 3. Missing Windows runtimes (protontricks)

Log hints and the verb to add with `tricks <game> <verb>`:

| Log says | Verb |
| --- | --- |
| `msvcp140.dll` / `vcruntime140.dll` not found | `vcrun2022` |
| `d3dcompiler_43/47` | `d3dcompiler_47` |
| `mscoree.dll`, .NET errors | `dotnet48` (slow, last resort) |
| `xinput1_3` / controller not seen | `xact`, `xinput` |
| Missing fonts, boxes for text | `corefonts` |
| `mfplat.dll`, video or cutscene failure | use GE-Proton first; `mf` as fallback |

If protontricks isn't installed, Flatpak works everywhere, including Deck: `flatpak install -y --user flathub com.github.Matoking.protontricks`.

## 4. Reading Proton logs

`logs <game> --errors` dedupes the noise. What matters:

- `err:module:import_dll Library X not found` → a runtime is missing (table above).
- `wine: Unhandled page fault` / `Unhandled exception` near the end → real crash. Look at the lines just above it for the DLL involved.
- `err:vulkan` / `VK_ERROR_*` / `vkd3d` errors → GPU driver or VKD3D issue. Check `vulkaninfo --summary` works, update the drivers, try another Proton.
- `fixme:` lines are almost always harmless. Ignore them.
- No Proton log at all → Steam never reached Proton. Check `steamctl show` (is a tool forced? does the exe exist?) and the Steam console log lines `logs` prints.

## 5. Non-Steam games and launchers

- **Add the installer first, then the launcher.** `add Setup.exe --proton proton_experimental`, run it, install. Then point the shortcut at the installed exe inside its prefix (`<steam>/steamapps/compatdata/<appid>/pfx/drive_c/...`). Simplest is `remove` the installer shortcut and `add` the real exe, but **the new shortcut gets a new appid and so a new, empty prefix**. To keep the installed files, `add` the exe with the same `--name` and exe path the prefix belongs to, or move the prefix folder to the new appid's compatdata path before first launch.
- Never wrap system `wine` in a Steam shortcut. Steam injects its overlay and runtime libraries (`LD_PRELOAD`, `LD_LIBRARY_PATH`), and Chromium-based launchers crash on them. Use Proton, or a wrapper script that unsets those first.
- Launchers that run games inside their own prefix (Battle.net, EA app, Ubisoft): the games inherit the launcher's Proton and prefix. Fix the launcher's shortcut, not the game.
- Alternatives worth naming when Steam is the wrong tool: Heroic (Epic, GOG, Amazon), Lutris (install scripts for Battle.net and others). Both work on the Deck through Flatpak.

## 6. Steam Deck specifics

- Desktop Mode is KDE Plasma on Wayland. `screenshot` uses spectacle. In Game Mode, use `steam-shots` after the user presses Steam + R1.
- Storage: games on the microSD card show up as an extra library. steamctl reads `libraryfolders.vdf`, so `show` finds them.
- Per-game performance settings (FPS cap, TDP, scaling) live in the Quick Access menu, not in files. Tell the user where to tap.
- `steamos-readonly`: leave it on. Everything here works from the home folder.

## 7. Last resorts

1. `reset-prefix` (after telling the user about saves) and run again with plain Proton Experimental and no launch options.
2. Steam → game Properties → Installed Files → Verify integrity (ask the user, or `steam://validate/<appid>`).
3. Check the game's ProtonDB page for a matching distro or GPU report and copy its exact fix.
