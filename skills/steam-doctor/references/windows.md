# Windows playbook

No Proton here. steamctl still handles launch options, non-Steam shortcuts, logs and screenshots. Run it with `python` or `py`. If neither exists: `winget install -e --id Python.Python.3.12`.

Shell commands below are PowerShell. Claude Code on Windows runs Git Bash by default, so wrap them: `powershell -NoProfile -Command "..."`.

## 1. Find out why it died

- **Event Log** (most useful): `Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2; StartTime=(Get-Date).AddHours(-1)} | Select TimeCreated,ProviderName,Message -First 10 | Format-List`. "Application Error" names the faulting module (`faulting module d3d12.dll`, `vcruntime140.dll`...).
- Game logs and crash dumps usually sit in `%LOCALAPPDATA%\<Game>`, `%USERPROFILE%\Documents\My Games\<Game>`, or `%LOCALAPPDATA%\CrashDumps`.
- `steamctl logs <game>` for Steam's side (stuck "Running", missing files, install script failures).
- `steamctl screenshot` to see error dialogs.

## 2. Fixes, in order

| Clue | Fix |
| --- | --- |
| `MSVCP140.dll` / `VCRUNTIME140.dll` missing | `winget install -e --id Microsoft.VCRedist.2015+.x64` (and `.x86`) |
| DirectX DLL missing (`d3dx9_43`, `xinput1_3`, `XAPOFX`) | DirectX June 2010 runtime: `winget install -e --id Microsoft.DirectX` |
| .NET error on start | `winget install -e --id Microsoft.DotNet.DesktopRuntime.8` (or the version named) |
| Crash in `nvwgf2umx.dll`, `amdxx64.dll`, `igd*` | GPU driver. Update it from the vendor, clean install if needed |
| DX12 crash | Launch option `-dx11` (check the game supports it), or update the driver |
| Instant close, no error | Verify files (`steam://validate/<appid>`), check antivirus quarantine, run once as admin to rule out permissions |
| Overlay crash or stutter | Turn off Steam, Discord, GeForce/Adrenalin, MSI Afterburner/RTSS overlays one by one |
| Anti-cheat error (EAC/BattlEye/Vanguard) | Repair the anti-cheat from the game folder (`EasyAntiCheat\EasyAntiCheat_Setup.exe` → Repair), check Secure Boot/TPM for Vanguard/Javelin |
| Wrong monitor or resolution | Launch options `-windowed -w 1920 -h 1080`, or delete the game's config folder in Documents |
| Controller not detected | Steam → game Properties → Controller → Enable Steam Input (or disable it, if the game supports controllers natively) |

## 3. Admin and installs

winget may raise a UAC prompt: tell the user to accept it. Never turn off antivirus or Defender for the user; add a folder exclusion only if they agree.
