"""
PC Optimizer — local rule-based handler.

Matches voice commands to Windows actions without any AI/API call.
Only unrecognised queries fall through to the LLM.

Add new tasks by appending to TASKS at the bottom of this file.
"""
import os, re, subprocess, shutil, json, platform
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import psutil

PROFILE_PATH = os.path.join(
    os.environ.get("APPDATA") or str(Path.home()),
    "STASIS", "pc_profile.json"
)

# ── Individual actions ────────────────────────────────────────────────────────

def clean_temp() -> str:
    removed = 0
    failed  = 0
    folders = [
        os.environ.get("TEMP", ""),
        os.environ.get("TMP",  ""),
        r"C:\Windows\Temp",
    ]
    for folder in dict.fromkeys(folders):   # deduplicate
        if not folder or not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            try:
                if entry.is_file(follow_symlinks=False):
                    os.unlink(entry.path)
                else:
                    shutil.rmtree(entry.path, ignore_errors=False)
                removed += 1
            except Exception:
                failed += 1
    return (
        f"Cleared {removed} items from temp folders"
        + (f", {failed} skipped (in use)." if failed else ".")
    )

def disk_space() -> str:
    lines = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            free  = u.free  / 1e9
            total = u.total / 1e9
            lines.append(
                f"{p.device}  {free:.1f} GB free of {total:.1f} GB  ({u.percent}% used)"
            )
        except PermissionError:
            pass
    return "\n".join(lines) if lines else "Could not read disk info."

def ram_usage() -> str:
    m = psutil.virtual_memory()
    s = psutil.swap_memory()
    used  = m.used  / 1e9
    total = m.total / 1e9
    swap  = s.used  / 1e9
    return (
        f"RAM: {used:.1f} of {total:.1f} GB used ({m.percent}%). "
        f"Swap: {swap:.1f} GB."
    )

def top_processes() -> str:
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x.get("memory_percent") or 0, reverse=True)
    lines = [
        f"{p['name']:30s}  {p.get('memory_percent', 0):.1f}% RAM"
        for p in procs[:8] if p.get("name")
    ]
    return "Top processes by RAM:\n" + "\n".join(lines)

def cpu_usage() -> str:
    pct = psutil.cpu_percent(interval=1)
    freq = psutil.cpu_freq()
    cores = psutil.cpu_count(logical=False)
    threads = psutil.cpu_count(logical=True)
    freq_str = f"  {freq.current:.0f} MHz" if freq else ""
    return (
        f"CPU: {pct}% usage{freq_str}  |  {cores} cores / {threads} threads."
    )

def flush_dns() -> str:
    r = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True)
    return "DNS cache flushed." if r.returncode == 0 else f"Failed: {r.stderr.strip()}"

def empty_recycle_bin() -> str:
    r = subprocess.run(
        ["PowerShell", "-NoProfile", "-Command",
         "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
        capture_output=True, text=True,
    )
    return "Recycle bin emptied." if r.returncode == 0 else "Could not empty recycle bin."

def high_performance_mode() -> str:
    # GUID for Windows built-in High Performance plan
    r = subprocess.run(
        ["powercfg", "/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"],
        capture_output=True, text=True,
    )
    return "Switched to High Performance power plan." if r.returncode == 0 else (
        "Could not change power plan — try running as administrator."
    )

def balanced_mode() -> str:
    r = subprocess.run(
        ["powercfg", "/setactive", "381b4222-f694-41f0-9685-ff5bb260df2e"],
        capture_output=True, text=True,
    )
    return "Switched to Balanced power plan." if r.returncode == 0 else (
        "Could not change power plan."
    )

def network_reset() -> str:
    for cmd in [
        ["netsh", "winsock", "reset"],
        ["netsh", "int", "ip", "reset"],
        ["ipconfig", "/flushdns"],
    ]:
        subprocess.run(cmd, capture_output=True)
    return "Network stack reset. Restart may be needed for full effect."

def list_startup() -> str:
    r = subprocess.run(
        ["PowerShell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_StartupCommand | Select-Object Name,Command | Format-Table -AutoSize"],
        capture_output=True, text=True,
    )
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    return "\n".join(lines[:20]) if lines else "Could not read startup programs."

def kill_high_ram() -> str:
    """Kills the single non-system process consuming the most RAM (>500 MB)."""
    SAFE = {"system", "svchost.exe", "lsass.exe", "csrss.exe", "wininit.exe",
            "services.exe", "explorer.exe", "python.exe", "pythonw.exe"}
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if p.info["name"].lower() not in SAFE:
                procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda p: p.info["memory_info"].rss if p.info.get("memory_info") else 0, reverse=True)
    if not procs:
        return "No killable processes found."
    target = procs[0]
    mb = (target.info["memory_info"].rss / 1e6) if target.info.get("memory_info") else 0
    if mb < 500:
        return f"Largest non-system process is {target.info['name']} at {mb:.0f} MB — not worth killing."
    try:
        target.kill()
        return f"Killed {target.info['name']} ({mb:.0f} MB freed)."
    except Exception as e:
        return f"Could not kill {target.info['name']}: {e}"

def clear_clipboard() -> str:
    r = subprocess.run(
        ["PowerShell", "-NoProfile", "-Command", "Set-Clipboard -Value $null"],
        capture_output=True,
    )
    return "Clipboard cleared." if r.returncode == 0 else "Could not clear clipboard."

def open_task_manager() -> str:
    subprocess.Popen(["taskmgr.exe"])
    return "Task Manager opened."

def open_disk_cleanup() -> str:
    subprocess.Popen(["cleanmgr.exe"])
    return "Disk Cleanup opened."

def open_app(name: str) -> str:
    """
    Launch an application by name. Tries URI schemes first (most reliable
    for store apps like Spotify/Discord), then falls back to Windows `start`.
    """
    name = name.strip()
    key  = name.lower()

    # URI schemes — bypass PATH lookups entirely, work even for MS Store apps
    URI: dict[str, str] = {
        "spotify":    "spotify:",
        "steam":      "steam://open/main",
        "discord":    "discord:",
        "slack":      "slack:",
        "whatsapp":   "whatsapp:",
        "teams":      "msteams:",
        "zoom":       "zoommtg:",
        "figma":      "figma:",
    }

    # Friendly name → executable / command recognised by Windows `start`
    EXE: dict[str, str] = {
        "notepad":        "notepad.exe",
        "calculator":     "calc.exe",
        "paint":          "mspaint.exe",
        "file explorer":  "explorer.exe",
        "explorer":       "explorer.exe",
        "chrome":         "chrome",
        "google chrome":  "chrome",
        "firefox":        "firefox",
        "edge":           "msedge",
        "microsoft edge": "msedge",
        "vlc":            "vlc",
        "obs":            "obs64",
        "vs code":        "code",
        "vscode":         "code",
        "visual studio code": "code",
        "word":           "winword",
        "excel":          "excel",
        "powerpoint":     "powerpnt",
        "outlook":        "outlook",
        "snipping tool":  "SnippingTool.exe",
        "settings":       "ms-settings:",
        "store":          "ms-windows-store:",
        "xbox":           "xbox:",
    }

    if key in URI:
        subprocess.Popen(["cmd", "/c", "start", "", URI[key]], shell=False)
        return f"Opening {name}."

    exe = EXE.get(key, name)
    try:
        # `start "" <target>` lets Windows resolve app names, URIs, and paths
        subprocess.Popen(["cmd", "/c", "start", "", exe], shell=False)
        return f"Opening {name}."
    except Exception as e:
        return f"Couldn't open {name}: {e}"


def battery_status() -> str:
    b = psutil.sensors_battery()
    if b is None:
        return "No battery detected — desktop system."
    status = "charging" if b.power_plugged else "discharging"
    mins   = int(b.secsleft / 60) if b.secsleft > 0 else None
    time_  = f"  ~{mins} min remaining." if mins else ""
    return f"Battery: {b.percent:.0f}%  ({status}).{time_}"


# ── Task registry ─────────────────────────────────────────────────────────────

class Task:
    def __init__(self, keywords: list[str], fn: Callable[[], str], label: str):
        self.keywords = keywords
        self.fn       = fn
        self.label    = label

TASKS: list[Task] = [
    Task(["clean temp", "temp files", "temporary files", "junk files", "clean up", "cleanup", "clear cache"],
         clean_temp, "Clean temp files"),

    Task(["disk space", "storage", "hard drive space", "how much space", "drive space", "free space"],
         disk_space, "Check disk space"),

    Task(["ram", "memory usage", "how much memory", "memory used"],
         ram_usage, "RAM usage"),

    Task(["cpu", "processor", "cpu usage", "cpu load"],
         cpu_usage, "CPU usage"),

    Task(["what's running", "whats running", "top process", "running processes",
          "what is using", "what's using", "hogging", "eating", "memory hog"],
         top_processes, "Top processes"),

    Task(["flush dns", "dns cache", "internet slow", "slow internet", "connection issues",
          "cant connect", "can't connect"],
         flush_dns, "Flush DNS"),

    Task(["recycle bin", "empty recycle", "clear recycle"],
         empty_recycle_bin, "Empty recycle bin"),

    Task(["high performance", "performance mode", "max performance", "speed up", "boost performance",
          "faster mode", "gaming mode"],
         high_performance_mode, "High performance mode"),

    Task(["balanced mode", "normal mode", "save power", "power saving"],
         balanced_mode, "Balanced power mode"),

    Task(["reset network", "network reset", "fix internet", "internet broken", "wifi broken",
          "network not working"],
         network_reset, "Reset network"),

    Task(["startup programs", "startup apps", "what starts", "boot programs", "autostart"],
         list_startup, "Startup programs"),

    Task(["kill", "force quit", "stop process", "free ram", "free up ram", "free memory"],
         kill_high_ram, "Kill highest RAM process"),

    Task(["clear clipboard", "clipboard"],
         clear_clipboard, "Clear clipboard"),

    Task(["task manager", "open task manager"],
         open_task_manager, "Open Task Manager"),

    Task(["disk cleanup", "open disk cleanup", "cleanup wizard"],
         open_disk_cleanup, "Open Disk Cleanup"),

    Task(["battery", "charge", "battery life", "how much battery"],
         battery_status, "Battery status"),
]


_OPEN_RE = re.compile(
    r"(?:open|launch|start|run|load|pull up)\s+(.+)", re.IGNORECASE
)

def match(text: str) -> Optional[str]:
    """
    Try to handle the query locally.
    Returns a result string if matched, None if the LLM should handle it.
    """
    lower = text.lower()

    # Specific PC tasks
    for task in TASKS:
        if any(kw in lower for kw in task.keywords):
            try:
                return task.fn()
            except Exception as e:
                return f"{task.label} failed: {e}"

    # General "open / launch / start X" — no AI needed
    m = _OPEN_RE.search(lower)
    if m:
        return open_app(m.group(1).strip())

    return None


# ── PC profile scan ───────────────────────────────────────────────────────────

def generate_profile() -> dict:
    """
    One-time scan of this machine. Saved to %APPDATA%/STASIS/pc_profile.json
    and injected into the system prompt so the AI knows your hardware.
    """
    profile: dict = {
        "generated_at": datetime.now().isoformat(),
        "os":           platform.platform(),
        "machine":      platform.machine(),
        "processor":    platform.processor(),
        "python":       platform.python_version(),
    }

    mem = psutil.virtual_memory()
    profile["ram_gb"]     = round(mem.total / 1e9, 1)
    profile["cpu_cores"]  = psutil.cpu_count(logical=False)
    profile["cpu_threads"]= psutil.cpu_count(logical=True)

    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            disks.append({
                "device":       p.device,
                "fstype":       p.fstype,
                "total_gb":     round(u.total / 1e9, 1),
                "free_gb":      round(u.free  / 1e9, 1),
                "percent_used": u.percent,
            })
        except PermissionError:
            pass
    profile["disks"] = disks

    # Rough process count
    profile["running_processes"] = len(psutil.pids())

    os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return profile


def load_profile() -> Optional[dict]:
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def profile_summary(profile: dict) -> str:
    """One-line summary injected into the system prompt."""
    disks = profile.get("disks", [])
    disk_str = "  |  ".join(
        f"{d['device']} {d['free_gb']} GB free" for d in disks
    )
    return (
        f"Hardware: {profile.get('ram_gb')} GB RAM  |  "
        f"{profile.get('cpu_cores')} cores / {profile.get('cpu_threads')} threads  |  "
        f"{disk_str}  |  OS: {profile.get('os')}"
    )
