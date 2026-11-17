#!/usr/bin/env python3
"""steamctl - inspect and change Steam game setup on Linux, Steam Deck and Windows.

Pure standard library, so it runs anywhere Python 3.8+ does, including over SSH:
    ssh deck@steamdeck python3 - list < steamctl.py

Every command that edits a Steam config file closes Steam first (Steam rewrites
these files while running), backs the file up next to itself, and reopens Steam.
"""
import argparse
import binascii
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

IS_WIN = sys.platform == "win32"
HOME = os.path.expanduser("~")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- text VDF

def vdf_loads(text):
    tokens = re.finditer(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)|([^\s"{}]+)', text)
    root = {}
    stack = [root]
    key = None
    for m in tokens:
        quoted, brace, comment, bare = m.groups()
        if comment is not None:
            continue
        if brace == "{":
            new = {}
            stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            stack.pop()
        else:
            tok = quoted if quoted is not None else bare
            if quoted is not None:
                tok = re.sub(r"\\(.)", lambda e: {"n": "\n", "t": "\t"}.get(e.group(1), e.group(1)), tok)
            elif tok.startswith("[$"):  # platform conditional, ignore
                continue
            if key is None:
                key = tok
            else:
                stack[-1][key] = tok
                key = None
    return root


def vdf_dumps(obj, indent=0):
    out = []
    tabs = "\t" * indent
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    for k, v in obj.items():
        if isinstance(v, dict):
            out.append(f'{tabs}"{esc(k)}"\n{tabs}{{\n{vdf_dumps(v, indent + 1)}{tabs}}}\n')
        else:
            out.append(f'{tabs}"{esc(k)}"\t\t"{esc(v)}"\n')
    return "".join(out)


def vdf_load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return vdf_loads(f.read())


def ci_get(d, key, create=False):
    """Case-insensitive dict lookup; Steam is inconsistent about key case."""
    for k in d:
        if k.lower() == key.lower():
            return d[k]
    if create:
        d[key] = {}
        return d[key]
    return None


def ci_path(d, *keys, create=False):
    for k in keys:
        if d is None:
            return None
        d = ci_get(d, k, create=create)
    return d


# ---------------------------------------------------------------- binary VDF (shortcuts.vdf)

def bvdf_loads(data):
    pos = 0

    def cstr():
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        return s

    def read_map():
        nonlocal pos
        d = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 0x08:
                return d
            name = cstr()
            if t == 0x00:
                d[name] = read_map()
            elif t == 0x01:
                d[name] = cstr()
            elif t == 0x02:
                d[name] = struct.unpack_from("<i", data, pos)[0]
                pos += 4
            elif t == 0x03:
                d[name] = struct.unpack_from("<f", data, pos)[0]
                pos += 4
            elif t == 0x07:
                d[name] = ("u64", struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            else:
                raise ValueError(f"unknown binary VDF type {t:#x} at {pos}")

    return read_map() if data else {}


def bvdf_dumps(d):
    out = bytearray()
    for k, v in d.items():
        name = k.encode() + b"\x00"
        if isinstance(v, dict):
            out += b"\x00" + name + bvdf_dumps(v)
        elif isinstance(v, tuple) and v[0] == "u64":
            out += b"\x07" + name + struct.pack("<Q", v[1])
        elif isinstance(v, float):
            out += b"\x03" + name + struct.pack("<f", v)
        elif isinstance(v, int):
            if v > 0x7FFFFFFF:
                v -= 1 << 32
            out += b"\x02" + name + struct.pack("<i", v)
        else:
            out += b"\x01" + name + str(v).encode() + b"\x00"
    return bytes(out + b"\x08")


# ---------------------------------------------------------------- locating Steam

def is_flatpak_root(root):
    return "/.var/app/com.valvesoftware.Steam/" in root


def find_steam_root():
    if os.environ.get("STEAM_ROOT"):
        return os.environ["STEAM_ROOT"]
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                p = os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
                if os.path.isdir(p):
                    return p
        except OSError:
            pass
        for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
            if os.path.isdir(p):
                return p
        return None
    for p in ("~/.local/share/Steam", "~/.steam/steam", "~/.steam/root",
              "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
              "~/.var/app/com.valvesoftware.Steam/data/Steam"):
        p = os.path.realpath(os.path.expanduser(p))
        if os.path.isdir(os.path.join(p, "steamapps")):
            return p
    return None


class Steam:
    def __init__(self):
        self.root = find_steam_root()
        if not self.root:
            die("could not find a Steam install (set STEAM_ROOT to override)")
        self.flatpak = is_flatpak_root(self.root)

    # -- paths
    @property
    def config_vdf(self):
        return os.path.join(self.root, "config", "config.vdf")

    def libraries(self):
        libs = [self.root]
        lf = os.path.join(self.root, "steamapps", "libraryfolders.vdf")
        if os.path.exists(lf):
            for v in (ci_get(vdf_load(lf), "libraryfolders") or {}).values():
                p = v.get("path") if isinstance(v, dict) else v
                if p and os.path.isdir(p) and os.path.realpath(p) not in map(os.path.realpath, libs):
                    libs.append(p)
        return libs

    def user_id(self):
        ud = os.path.join(self.root, "userdata")
        ids = [d for d in os.listdir(ud) if d.isdigit() and d != "0"] if os.path.isdir(ud) else []
        if not ids:
            return None
        lu = os.path.join(self.root, "config", "loginusers.vdf")
        if os.path.exists(lu):
            for sid, info in (ci_get(vdf_load(lu), "users") or {}).items():
                if str(ci_get(info, "MostRecent")) == "1":
                    acc = str(int(sid) & 0xFFFFFFFF)
                    if acc in ids:
                        return acc
        # fall back to most recently modified userdata folder
        return max(ids, key=lambda i: os.path.getmtime(os.path.join(ud, i)))

    def user_file(self, name):
        uid = self.user_id()
        if not uid:
            die("no Steam user found in userdata/ (log in to Steam once)")
        return os.path.join(self.root, "userdata", uid, "config", name)

    # -- games
    def installed_apps(self):
        apps = []
        for lib in self.libraries():
            for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
                st = ci_get(vdf_load(acf), "AppState") or {}
                appid = int(st.get("appid", 0))
                apps.append({
                    "appid": appid, "name": st.get("name", "?"), "kind": "steam",
                    "library": lib,
                    "installdir": os.path.join(lib, "steamapps", "common", st.get("installdir", "")),
                    "compatdata": os.path.join(lib, "steamapps", "compatdata", str(appid)),
                })
        return apps

    def shortcuts_file(self):
        return self.user_file("shortcuts.vdf")

    def load_shortcuts(self):
        p = self.shortcuts_file()
        if not os.path.exists(p):
            return {"shortcuts": {}}
        with open(p, "rb") as f:
            return bvdf_loads(f.read())

    def shortcut_apps(self):
        apps = []
        for idx, s in (self.load_shortcuts().get("shortcuts") or {}).items():
            appid = (ci_get(s, "appid") or 0) & 0xFFFFFFFF
            apps.append({
                "appid": appid, "name": ci_get(s, "AppName") or "?", "kind": "shortcut",
                "index": idx, "exe": ci_get(s, "Exe"), "startdir": ci_get(s, "StartDir"),
                "launch_options": ci_get(s, "LaunchOptions") or "",
                "compatdata": os.path.join(self.root, "steamapps", "compatdata", str(appid)),
            })
        return apps

    def all_games(self):
        tools = {"proton", "steam linux runtime", "steamworks common redistributables"}
        games = [a for a in self.installed_apps() if not any(a["name"].lower().startswith(t) for t in tools)]
        return games + self.shortcut_apps()

    def find(self, query):
        games = self.all_games()
        if str(query).isdigit():
            hits = [g for g in games if g["appid"] == int(query)]
            if hits:
                return hits[0]
            return {"appid": int(query), "name": f"app {query}", "kind": "steam", "compatdata":
                    os.path.join(self.root, "steamapps", "compatdata", str(query))}
        q = query.lower()
        hits = [g for g in games if g["name"].lower() == q] or [g for g in games if q in g["name"].lower()]
        if not hits:
            die(f"no game matching '{query}' (try: steamctl list)")
        if len(hits) > 1:
            die("ambiguous, matches: " + ", ".join(f"{g['name']} ({g['appid']})" for g in hits))
        return hits[0]

    # -- per-game settings
    def compat_mapping(self):
        cfg = vdf_load(self.config_vdf) if os.path.exists(self.config_vdf) else {}
        return ci_path(cfg, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping") or {}

    def launch_options(self, game):
        if game["kind"] == "shortcut":
            return game.get("launch_options", "")
        p = self.user_file("localconfig.vdf")
        if not os.path.exists(p):
            return ""
        apps = ci_path(vdf_load(p), "UserLocalConfigStore", "Software", "Valve", "Steam", "apps") or {}
        return (apps.get(str(game["appid"])) or {}).get("LaunchOptions", "")

    def compat_tools(self):
        """Installed compat tools as {internal_name: display_name}."""
        tools = {}
        for lib in self.libraries():
            for d in sorted(glob.glob(os.path.join(lib, "steamapps", "common", "Proton*"))):
                name = os.path.basename(d)
                if "experimental" in name.lower():
                    tools["proton_experimental"] = name
                elif "hotfix" in name.lower():
                    tools["proton_hotfix"] = name
                else:
                    m = re.match(r"Proton (\d+)", name)
                    if m:
                        tools[f"proton_{m.group(1)}"] = name + " (internal name guessed)"
        dirs = [os.path.join(self.root, "compatibilitytools.d")]
        if not IS_WIN:
            dirs.append("/usr/share/steam/compatibilitytools.d")
        for base in dirs:
            for f in glob.glob(os.path.join(base, "*", "compatibilitytool.vdf")):
                ct = ci_path(vdf_load(f), "compatibilitytools", "compat_tools") or {}
                for internal, info in ct.items():
                    tools[internal] = ci_get(info, "display_name") or internal
        # Names Steam itself has written are the most trustworthy (aliases like proton-stable vary by client)
        seen = {ci_get(m, "name") for m in self.compat_mapping().values() if isinstance(m, dict)}
        log = os.path.join(self.root, "logs", "compat_log.txt")
        if os.path.exists(log):
            seen |= set(re.findall(r'to tool "([^"]+)"', open(log, errors="replace").read()))
        for name in sorted(n for n in seen if n and n not in tools):
            tools[name] = "(name Steam has used on this machine)"
        return tools


# ---------------------------------------------------------------- Steam process control

def linux_session_env():
    """Fill in display/session vars so GUI commands work from SSH or a bare shell."""
    env = dict(os.environ)
    run = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if "WAYLAND_DISPLAY" not in env:
        socks = sorted(s for s in glob.glob(os.path.join(run, "wayland-*")) if not s.endswith(".lock"))
        if socks:
            env["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
    env.setdefault("DISPLAY", ":0")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run}/bus")
    return env


def steam_cmd(steam):
    if IS_WIN:
        return [os.path.join(steam.root, "steam.exe")]
    if steam.flatpak:
        return ["flatpak", "run", "com.valvesoftware.Steam"]
    return [shutil.which("steam") or os.path.join(steam.root, "steam.sh")]


def steam_running():
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"], capture_output=True, text=True).stdout
        return "steam.exe" in out.lower()
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


def in_game_mode():
    """Steam Deck Game Mode: Steam *is* the session, so closing it is unsafe."""
    if IS_WIN:
        return False
    return subprocess.run(["pgrep", "-f", "gamescope-session"], capture_output=True).returncode == 0


def spawn(cmd):
    if IS_WIN:
        subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(cmd, env=linux_session_env(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def stop_steam(steam, timeout=60):
    if not steam_running():
        return False
    if in_game_mode():
        die("Steam Deck is in Game Mode; switch to Desktop Mode before changing settings "
            "(or pass --no-restart and make the change by hand in Steam).")
    print("closing Steam...")
    spawn(steam_cmd(steam) + ["-shutdown"])
    for _ in range(timeout):
        time.sleep(1)
        if not steam_running():
            time.sleep(2)  # let it finish flushing config files
            return True
    die("Steam did not close within a minute; close it by hand and retry")


def start_steam(steam):
    print("starting Steam...")
    spawn(steam_cmd(steam))


class SteamClosed:
    """Context manager: close Steam for an edit, reopen it afterwards if it was running."""

    def __init__(self, steam, restart=True):
        self.steam, self.restart, self.was_running = steam, restart, False

    def __enter__(self):
        if self.restart:
            self.was_running = stop_steam(self.steam)
        elif steam_running():
            die("Steam is running and would overwrite this edit; drop --no-restart or close Steam first")
        return self

    def __exit__(self, exc_type, *_):
        if self.was_running and exc_type is None:
            start_steam(self.steam)


def backup(path):
    if os.path.exists(path):
        base = f"{path}.steamctl-bak-{time.strftime('%Y%m%d-%H%M%S')}"
        dst, n = base, 1
        while os.path.exists(dst):  # several edits in one second must not overwrite the first backup
            dst, n = f"{base}-{n}", n + 1
        shutil.copy2(path, dst)
        print(f"backup: {dst}")


def write_text_vdf(path, data):
    backup(path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(vdf_dumps(data))


def write_shortcuts(steam, data):
    p = steam.shortcuts_file()
    backup(p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(bvdf_dumps(data))


# ---------------------------------------------------------------- commands

def proton_log_path(appid):
    return os.path.join(os.environ.get("PROTON_LOG_DIR", HOME), f"steam-{appid}.log")


def describe(steam, g, mapping):
    m = mapping.get(str(g["appid"])) or {}
    ver_file = os.path.join(g.get("compatdata", ""), "version")
    used = open(ver_file).read().strip() if os.path.exists(ver_file) else None
    return {
        "appid": g["appid"], "name": g["name"], "kind": g["kind"],
        "compat_tool": ci_get(m, "name") or None,
        "launch_options": steam.launch_options(g),
        "prefix": g["compatdata"] if os.path.isdir(g.get("compatdata", "")) else None,
        "prefix_proton_version": used,
        **({"exe": g.get("exe"), "startdir": g.get("startdir")} if g["kind"] == "shortcut" else
           {"installdir": g.get("installdir")}),
    }


def cmd_info(steam, a):
    info = {
        "platform": "windows" if IS_WIN else ("steamdeck" if os.path.exists("/etc/steamos-release") or
                                              "steamos" in open("/etc/os-release").read().lower() else "linux"),
        "steam_root": steam.root, "flatpak_steam": steam.flatpak,
        "steam_running": steam_running(), "game_mode": in_game_mode(),
        "user_id": steam.user_id(), "libraries": steam.libraries(),
        "global_default_compat_tool": ci_get(steam.compat_mapping().get("0") or {}, "name"),
        "compat_tools": steam.compat_tools(),
        "helpers": {t: bool(shutil.which(t)) for t in
                    (["powershell"] if IS_WIN else
                     ["protontricks", "winetricks", "flatpak", "grim", "spectacle", "gamescope", "mangohud", "vulkaninfo"])},
    }
    if not IS_WIN and shutil.which("flatpak"):
        fl = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True).stdout
        info["helpers"]["protontricks_flatpak"] = "com.github.Matoking.protontricks" in fl
    print(json.dumps(info, indent=2) if a.json else "\n".join(f"{k}: {v}" for k, v in info.items()))


def cmd_list(steam, a):
    mapping = steam.compat_mapping()
    rows = [describe(steam, g, mapping) for g in sorted(steam.all_games(), key=lambda g: g["name"].lower())]
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        extra = []
        if r["compat_tool"]:
            extra.append(f"tool={r['compat_tool']}")
        if r["launch_options"]:
            extra.append(f"launch={r['launch_options']!r}")
        print(f"{r['appid']:>10}  {r['kind']:<8} {r['name']}  {' '.join(extra)}")


def cmd_show(steam, a):
    print(json.dumps(describe(steam, steam.find(a.game), steam.compat_mapping()), indent=2))


def cmd_tools(steam, a):
    for internal, name in steam.compat_tools().items():
        print(f"{internal:<28} {name}")


def cmd_set_proton(steam, a):
    if IS_WIN:
        die("Proton does not exist on Windows")
    g = steam.find(a.game)
    tool = a.tool
    if tool not in ("none", "default") and tool not in steam.compat_tools():
        print(f"warning: '{tool}' is not an installed tool I can see (steamctl tools); setting it anyway")
    with SteamClosed(steam, not a.no_restart):
        cfg = vdf_load(steam.config_vdf)
        mapping = ci_path(cfg, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping", create=True)
        if tool in ("none", "default"):
            mapping.pop(str(g["appid"]), None)
        else:
            mapping[str(g["appid"])] = {"name": tool, "config": "", "priority": "250"}
        write_text_vdf(steam.config_vdf, cfg)
    print(f"{g['name']} ({g['appid']}): compat tool -> {tool}")


def cmd_set_launch(steam, a):
    g = steam.find(a.game)
    with SteamClosed(steam, not a.no_restart):
        if g["kind"] == "shortcut":
            data = steam.load_shortcuts()
            data["shortcuts"][g["index"]]["LaunchOptions"] = a.options
            write_shortcuts(steam, data)
        else:
            p = steam.user_file("localconfig.vdf")
            cfg = vdf_load(p)
            apps = ci_path(cfg, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", create=True)
            entry = apps.setdefault(str(g["appid"]), {})
            if a.options:
                entry["LaunchOptions"] = a.options
            else:
                entry.pop("LaunchOptions", None)
            write_text_vdf(p, cfg)
    print(f"{g['name']} ({g['appid']}): launch options -> {a.options!r}")


def cmd_add(steam, a):
    exe = os.path.abspath(a.exe)
    if not os.path.exists(exe):
        die(f"{exe} does not exist")
    name = a.name or os.path.splitext(os.path.basename(exe))[0]
    quoted = f'"{exe}"'
    appid = (binascii.crc32((quoted + name).encode()) & 0xFFFFFFFF) | 0x80000000
    with SteamClosed(steam, not a.no_restart):
        data = steam.load_shortcuts()
        sc = data.setdefault("shortcuts", {})
        if any(((ci_get(s, "appid") or 0) & 0xFFFFFFFF) == appid for s in sc.values()):
            die(f"a shortcut for {name} already exists ({appid})")
        idx = str(max([int(k) for k in sc] + [-1]) + 1)
        sc[idx] = {
            "appid": appid, "AppName": name, "Exe": quoted,
            "StartDir": f'"{a.start_dir or os.path.dirname(exe)}"', "icon": "", "ShortcutPath": "",
            "LaunchOptions": a.launch or "", "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1,
            "OpenVR": 0, "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0,
            "FlatpakAppID": "", "sortas": "", "tags": {},
        }
        write_shortcuts(steam, data)
        if a.proton and not IS_WIN:
            cfg = vdf_load(steam.config_vdf)
            mapping = ci_path(cfg, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping", create=True)
            mapping[str(appid)] = {"name": a.proton, "config": "", "priority": "250"}
            write_text_vdf(steam.config_vdf, cfg)
    print(f"added {name} as non-Steam game, appid {appid}"
          + (f", compat tool {a.proton}" if a.proton and not IS_WIN else ""))
    print(f"its Proton prefix will be: {os.path.join(steam.root, 'steamapps', 'compatdata', str(appid))}")


def cmd_edit_shortcut(steam, a):
    """Repoint a shortcut. Steam keys the prefix on the stored appid, so the prefix survives."""
    g = steam.find(a.game)
    if g["kind"] != "shortcut":
        die("only non-Steam shortcuts can be edited here")
    with SteamClosed(steam, not a.no_restart):
        data = steam.load_shortcuts()
        s = data["shortcuts"][g["index"]]
        if a.exe:
            s["Exe"] = f'"{a.exe}"'
            s["StartDir"] = f'"{a.start_dir or os.path.dirname(a.exe)}"'
        elif a.start_dir:
            s["StartDir"] = f'"{a.start_dir}"'
        if a.name:
            s["AppName"] = a.name
        write_shortcuts(steam, data)
    print(f"{g['appid']}: name={s['AppName']!r} exe={s.get('Exe')} startdir={s.get('StartDir')}")


def cmd_remove(steam, a):
    g = steam.find(a.game)
    if g["kind"] != "shortcut":
        die("only non-Steam shortcuts can be removed here; uninstall Steam games in Steam")
    with SteamClosed(steam, not a.no_restart):
        data = steam.load_shortcuts()
        rest = [v for k, v in data["shortcuts"].items() if k != g["index"]]
        data["shortcuts"] = {str(i): v for i, v in enumerate(rest)}
        write_shortcuts(steam, data)
        cfg = vdf_load(steam.config_vdf) if os.path.exists(steam.config_vdf) else {}
        mapping = ci_path(cfg, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping")
        if mapping and mapping.pop(str(g["appid"]), None):
            write_text_vdf(steam.config_vdf, cfg)
    print(f"removed shortcut {g['name']} ({g['appid']})")


def cmd_run(steam, a):
    g = steam.find(a.game)
    gameid = (g["appid"] << 32) | 0x02000000 if g["kind"] == "shortcut" else g["appid"]
    url = f"steam://rungameid/{gameid}"
    if not steam_running():
        start_steam(steam)
        time.sleep(15)
    if IS_WIN:
        os.startfile(url)
    else:
        spawn(steam_cmd(steam) + [url])
    print(f"launched {g['name']} via {url}")
    if not IS_WIN and "PROTON_LOG=1" not in steam.launch_options(g):
        print("note: Proton logging is off; set launch options to 'PROTON_LOG=1 %command%' for a log")


ERR_PAT = re.compile(r"err:|exception|unhandled|crash|fatal|failed|cannot|not found|wine: |abort", re.I)


def cmd_logs(steam, a):
    g = steam.find(a.game)
    found = False
    pl = proton_log_path(g["appid"])
    if os.path.exists(pl):
        found = True
        age = int(time.time() - os.path.getmtime(pl))
        print(f"== Proton log {pl} (modified {age}s ago)")
        lines = open(pl, errors="replace").read().splitlines()
        if a.errors:
            seen, picked = set(), []
            for ln in lines:
                key = re.sub(r"^[0-9a-f.:]+:", "", ln)[:160]
                if ERR_PAT.search(ln) and key not in seen:
                    seen.add(key)
                    picked.append(ln)
            lines = picked
        print("\n".join(lines[-a.lines:]))
    logdir = os.path.join(steam.root, "logs")
    for name in ("console-linux.txt", "console_log.txt", "compat_log.txt", "content_log.txt"):
        p = os.path.join(logdir, name)
        if os.path.exists(p):
            hits = [ln for ln in open(p, errors="replace") if str(g["appid"]) in ln or
                    (g["kind"] == "shortcut" and str((g["appid"] << 32) | 0x02000000) in ln)]
            if hits:
                found = True
                print(f"== {p} (lines mentioning this game)")
                print("".join(hits[-a.lines:]).rstrip())
    if not found:
        print("no logs found for this game yet. Set launch options to 'PROTON_LOG=1 %command%' and run it.")


def cmd_screenshot(steam, a):
    out = os.path.abspath(a.out or os.path.join(tempfile.gettempdir(), f"steamctl-shot-{int(time.time())}.png"))
    if IS_WIN:
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
              "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
              "$g=[System.Drawing.Graphics]::FromImage($i);$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
              f"$i.Save('{out}')")
        tries = [["powershell", "-NoProfile", "-Command", ps]]
    else:
        tries = [["grim", out], ["spectacle", "-b", "-n", "-f", "-o", out],
                 ["gnome-screenshot", "-f", out], ["import", "-window", "root", out]]
    for cmd in tries:
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, env=None if IS_WIN else linux_session_env(), capture_output=True, timeout=30)
            if r.returncode == 0 and os.path.exists(out):
                print(out)
                return
    die("could not take a screenshot here (Game Mode? try: steamctl steam-shots)")


def cmd_steam_shots(steam, a):
    """Screenshots taken with Steam's own screenshot key, newest first. Works in Deck Game Mode."""
    uid = steam.user_id()
    shots = glob.glob(os.path.join(steam.root, "userdata", uid or "x", "760", "remote", "*", "screenshots", "*.jpg"))
    for p in sorted(shots, key=os.path.getmtime, reverse=True)[:a.count]:
        print(p)
    if not shots:
        print("no Steam screenshots yet (Steam button + R1 on Deck, F12 on PC)")


def cmd_reset_prefix(steam, a):
    g = steam.find(a.game)
    pfx = g["compatdata"]
    if not os.path.isdir(pfx):
        die(f"no prefix at {pfx}")
    dst = f"{pfx}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    os.rename(pfx, dst)
    print(f"moved prefix to {dst}; Steam builds a fresh one on next launch.")
    print("saves stored inside the prefix (drive_c/users/steamuser/...) are in the backup if needed.")


def cmd_tricks(steam, a):
    if IS_WIN:
        die("protontricks is Linux only; on Windows install redistributables directly")
    g = steam.find(a.game)
    if shutil.which("protontricks"):
        cmd = ["protontricks"]
    elif shutil.which("flatpak") and "Matoking" in subprocess.run(
            ["flatpak", "list", "--app"], capture_output=True, text=True).stdout:
        cmd = ["flatpak", "run", "com.github.Matoking.protontricks"]
    else:
        die("protontricks not found. Install: 'flatpak install -y --user flathub com.github.Matoking.protontricks' "
            "(works on Steam Deck) or your distro package")
    full = cmd + [str(g["appid"]), "-q"] + a.verbs
    print("running:", " ".join(full))
    sys.exit(subprocess.run(full, env=linux_session_env()).returncode)


def cmd_get_ge(steam, a):
    if IS_WIN:
        die("GE-Proton is Linux only")
    api = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/" + (
        f"tags/{a.version}" if a.version else "latest")
    rel = json.load(urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "steamctl"})))
    tag = rel["tag_name"]
    dest = os.path.join(steam.root, "compatibilitytools.d")
    if os.path.isdir(os.path.join(dest, tag)):
        print(f"{tag} already installed in {dest}")
        return
    assets = {x["name"]: x["browser_download_url"] for x in rel["assets"]}
    tar_name = next((n for n in assets if n.endswith(".tar.gz")), None)
    if not tar_name:
        die(f"no tarball in release {tag}")
    os.makedirs(dest, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, tar_name)
        print(f"downloading {tag}...")
        urllib.request.urlretrieve(assets[tar_name], tar_path)
        sum_name = next((n for n in assets if n.endswith(".sha512sum")), None)
        if sum_name:
            import hashlib
            expected = urllib.request.urlopen(assets[sum_name]).read().decode().split()[0]
            h = hashlib.sha512()
            with open(tar_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != expected:
                die("checksum mismatch, refusing to install")
            print("checksum ok")
        with tarfile.open(tar_path) as t:
            kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            t.extractall(dest, **kw)
    print(f"installed {tag} to {dest}. Restart Steam to see it; internal name is usually '{tag}'.")


def cmd_steam(steam, a):
    if a.action == "status":
        print("running" if steam_running() else "stopped", "(game mode)" if in_game_mode() else "")
    elif a.action == "stop":
        stop_steam(steam)
    elif a.action == "start":
        start_steam(steam)
    elif a.action == "restart":
        stop_steam(steam)
        start_steam(steam)


def main():
    # Windows consoles default to a legacy codepage; game names with em dashes etc. would crash print()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="steamctl", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, *args, edits=False):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        for a_ in args:
            sp.add_argument(*a_[0], **a_[1])
        if edits:
            sp.add_argument("--no-restart", action="store_true", help="fail instead of closing/reopening Steam")
        return sp

    J = (["--json"], {"action": "store_true"})
    G = (["game"], {"help": "appid or (part of) the game name"})
    add("info", cmd_info, "platform, Steam paths, installed tools", J)
    add("list", cmd_list, "games and shortcuts with their Proton/launch settings", J)
    add("show", cmd_show, "full settings for one game", G)
    add("tools", cmd_tools, "installed compat tools (Proton versions)")
    add("set-proton", cmd_set_proton, "force a compat tool ('none' to clear)", G,
        (["tool"], {"help": "internal name, e.g. proton_experimental, GE-Proton10-1"}), edits=True)
    add("set-launch", cmd_set_launch, "set launch options ('' to clear)", G, (["options"], {}), edits=True)
    add("add", cmd_add, "add a non-Steam game", (["exe"], {}), (["--name"], {}), (["--start-dir"], {}),
        (["--launch"], {"help": "launch options"}), (["--proton"], {"help": "compat tool to force"}), edits=True)
    add("edit-shortcut", cmd_edit_shortcut, "change a shortcut's exe/name/start dir, keeping its prefix", G,
        (["--exe"], {}), (["--name"], {}), (["--start-dir"], {}), edits=True)
    add("remove", cmd_remove, "remove a non-Steam shortcut", G, edits=True)
    add("run", cmd_run, "launch a game through Steam", G)
    add("logs", cmd_logs, "show Proton and Steam logs for a game", G,
        (["--errors"], {"action": "store_true", "help": "only error-looking lines, deduped"}),
        (["--lines"], {"type": int, "default": 80}))
    add("screenshot", cmd_screenshot, "screenshot the desktop, print the file path", (["--out"], {}))
    add("steam-shots", cmd_steam_shots, "list recent Steam-key screenshots", (["--count"], {"type": int, "default": 5}))
    add("reset-prefix", cmd_reset_prefix, "back up and drop a game's Proton prefix", G)
    add("tricks", cmd_tricks, "run protontricks verbs in a game's prefix", G, (["verbs"], {"nargs": "+"}))
    add("get-ge", cmd_get_ge, "download GE-Proton (latest, or --version TAG)", (["--version"], {}))
    add("steam", cmd_steam, "status/stop/start/restart Steam",
        (["action"], {"choices": ["status", "stop", "start", "restart"]}))

    a = p.parse_args()
    a.fn(Steam(), a)


if __name__ == "__main__":
    main()
