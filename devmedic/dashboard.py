"""The big colourful overview (`devmedic`) and the live view (`devmedic watch`)."""
import getpass
import os
import platform
import re
import socket
import subprocess
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import psutil
from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import caches, junk, ports, procs, repos
from .util import (HOME, console, dir_size, have, human_age, human_size, run,
                   short_path)

# ───────────────────────── visuals ─────────────────────────

GLYPHS = {
    "d": ["     _ ", "  __| |", " / _` |", "| (_| |", " \\__,_|"],
    "e": ["      ", "  ___ ", " / _ \\", "|  __/", " \\___|"],
    "v": ["       ", "__   __", "\\ \\ / /", " \\ V / ", "  \\_/  "],
    "o": ["       ", "  ___  ", " / _ \\ ", "| (_) |", " \\___/ "],
    "c": ["      ", "  ___ ", " / __|", "| (__ ", " \\___|"],
    "t": [" _   ", "| |_ ", "| __|", "| |_ ", " \\__|"],
    "r": ["      ", " _ __ ", "| '__|", "| |   ", "|_|   "],
    "m": ["           ", " _ __ ___  ", "| '_ ` _ \\ ", "| | | | | |", "|_| |_| |_|"],
    "i": [" _ ", "(_)", "| |", "| |", "|_|"],
}
GRADIENT = [(0, 215, 255), (95, 135, 255), (175, 95, 255), (255, 95, 215)]
SPARK = "▁▂▃▄▅▆▇█"


def gradient_color(frac):
    frac = min(max(frac, 0), 1) * (len(GRADIENT) - 1)
    i = min(int(frac), len(GRADIENT) - 2)
    f = frac - i
    a, b = GRADIENT[i], GRADIENT[i + 1]
    r, g, bl = (int(a[k] + (b[k] - a[k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def banner(word="devmedic"):
    rows = ["".join(GLYPHS[ch][i] for ch in word) for i in range(5)]
    width = max(len(r) for r in rows)
    text = Text()
    for row in rows:
        for x, ch in enumerate(row):
            text.append(ch, style=f"bold {gradient_color(x / width)}")
        text.append("\n")
    text.rstrip()
    return text


def level_color(pct, warn=75, bad=90):
    return "red" if pct >= bad else "yellow" if pct >= warn else "green"


def bar(pct, width=22, warn=75, bad=90):
    filled = round(pct / 100 * width)
    color = level_color(pct, warn, bad)
    return Text.assemble(("█" * filled, color), ("░" * (width - filled), "grey35"),
                         (f" {pct:3.0f}%", f"bold {color}"))


def spark(values, maximum=100):
    return "".join(SPARK[min(int(v / maximum * (len(SPARK) - 1) + 0.5), len(SPARK) - 1)]
                   for v in values)


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else many or word + 's'}"


def kv_table():
    t = Table.grid(padding=(0, 1))
    t.add_column(style="bold grey70", no_wrap=True)
    t.add_column()
    return t


def panel(body, title, color):
    return Panel(body, title=f"[bold {color}]{title}[/]", title_align="left",
                 border_style=color, padding=(0, 1))


# ───────────────────────── collectors ─────────────────────────

def os_name():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    name = re.sub(r"\((R|TM)\)|CPU|Processor|\s@.*", "", name)
                    return " ".join(name.split())
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def temperature():
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        readings = [t.current for t in temps.get(key, []) if t.current]
        if readings:
            return max(readings)
    return None


def battery():
    try:
        return psutil.sensors_battery()
    except (AttributeError, OSError, RuntimeError):
        return None


def system_info():
    return {
        "user": getpass.getuser(), "host": socket.gethostname(), "os": os_name(),
        "kernel": platform.release(), "cpu": cpu_model(),
        "cores": psutil.cpu_count(logical=True),
        "phys": psutil.cpu_count(logical=False),
        "boot": psutil.boot_time(), "load": os.getloadavg(),
        "temp": temperature(), "battery": battery(),
        "shell": os.path.basename(os.environ.get("SHELL", "")),
    }


def memory_hogs(limit=5):
    by_name = defaultdict(lambda: [0, 0])
    for p in psutil.process_iter(["name", "memory_info"]):
        mi = p.info["memory_info"]
        if mi and p.info["name"]:
            name = p.info["name"]
            # Group chrome/chromium helpers, "Web Content", etc. under the app.
            name = re.sub(r"^(chrome|chromium|firefox|code|electron)\b.*", r"\1", name,
                          flags=re.I)
            by_name[name][0] += mi.rss
            by_name[name][1] += 1
    return sorted(((n, rss, c) for n, (rss, c) in by_name.items()),
                  key=lambda x: x[1], reverse=True)[:limit]


def disks():
    seen, out = set(), []
    for p in psutil.disk_partitions(all=False):
        if p.fstype in ("squashfs", "tmpfs", "overlay") or p.device in seen:
            continue
        if p.mountpoint.startswith(("/snap", "/var/snap", "/run")):
            continue
        seen.add(p.device)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        out.append({"mount": p.mountpoint, "device": p.device, "fs": p.fstype,
                    "used": u.used, "total": u.total, "pct": u.percent})
    return out


TOOLS = [  # label, version command
    ("git", ["git", "--version"]), ("python", ["python3", "--version"]),
    ("pip", ["pip3", "--version"]), ("node", ["node", "--version"]),
    ("npm", ["npm", "--version"]), ("pnpm", ["pnpm", "--version"]),
    ("yarn", ["yarn", "--version"]), ("bun", ["bun", "--version"]),
    ("deno", ["deno", "--version"]), ("go", ["go", "version"]),
    ("rust", ["rustc", "--version"]), ("java", ["java", "-version"]),
    ("gcc", ["gcc", "--version"]), ("make", ["make", "--version"]),
    ("docker", ["docker", "--version"]), ("kubectl", ["kubectl", "version", "--client"]),
    ("gh", ["gh", "--version"]), ("code", ["code", "--version"]),
    ("nvim", ["nvim", "--version"]), ("vim", ["vim", "--version"]),
]
VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def tool_version(cmd):
    if not have(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return "?"
    m = VERSION_RE.search(r.stdout + r.stderr)
    return m.group(1) if m else "?"


def dev_tools():
    with ThreadPoolExecutor(max_workers=10) as pool:
        versions = list(pool.map(tool_version, [c for _, c in TOOLS]))
    return [(label, v) for (label, _), v in zip(TOOLS, versions)]


def docker_status():
    if not have("docker"):
        return {"state": "missing"}
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {"state": "down"}
    if "permission denied" in r.stderr.lower():
        return {"state": "denied"}
    if r.returncode != 0:
        return {"state": "down"}
    rows = [line.split("\t") for line in r.stdout.splitlines() if line.strip()]
    return {"state": "ok", "containers": rows}


def network():
    ips = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo" or name.startswith(("docker", "br-", "veth", "virbr")):
            continue
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET:
                ips.append((name, a.address))
    wifi = None
    if have("nmcli"):
        for line in run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], timeout=3).splitlines():
            if line.startswith("yes:"):
                wifi = line[4:] or None
                break
    io = psutil.net_io_counters()
    return {"ips": ips, "wifi": wifi, "sent": io.bytes_sent, "recv": io.bytes_recv}


def git_summary():
    infos = [repos.inspect(r) for r in repos.find(HOME)]
    return {"total": len(infos), "infos": infos,
            "dirty": [i for i in infos if i["changed"] or i["untracked"]],
            "unpushed": [i for i in infos if i["ahead"] or i["unpushed_branches"]],
            "noremote": [i for i in infos if i["no_remote"]],
            "stash": [i for i in infos if i["stashes"]],
            "flagged": [i for i in infos if repos.needs_attention(i)]}


def junk_summary():
    found = list(junk.find(HOME))
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(dir_size, [p for p, _ in found]))
    items = sorted(zip(sizes, (p for p, _ in found)), reverse=True)
    return {"total": sum(sizes), "count": len(items), "top": items[:3]}


def collect_all():
    with ThreadPoolExecutor(max_workers=12) as pool:
        f = {
            "cpu": pool.submit(psutil.cpu_percent, 0.5, True),
            "sys": pool.submit(system_info),
            "hogs": pool.submit(memory_hogs, 12),
            "disks": pool.submit(disks),
            "tools": pool.submit(dev_tools),
            "docker": pool.submit(docker_status),
            "net": pool.submit(network),
            "ports": pool.submit(ports.listening),
            "procs": pool.submit(procs.collect),
            "caches": pool.submit(caches.collect),
            "git": pool.submit(git_summary),
            "junk": pool.submit(junk_summary),
        }
        data = {k: v.result() for k, v in f.items()}
    data["mem"] = psutil.virtual_memory()
    data["swap"] = psutil.swap_memory()
    return data


# ───────────────────────── health score ─────────────────────────

def health(d):
    score, tips = 100, []

    def hit(points, tip):
        nonlocal score
        score -= points
        tips.append(tip)

    worst_disk = max((x["pct"] for x in d["disks"]), default=0)
    if worst_disk >= 90:
        hit(25, "[red]Disk almost full[/] → [bold]devmedic junk --clean[/], [bold]devmedic caches[/]")
    elif worst_disk >= 75:
        hit(10, "Disk filling up → [bold]devmedic junk[/] / [bold]devmedic caches[/]")
    if d["mem"].percent >= 90:
        hit(15, "[red]RAM nearly exhausted[/] → check [bold]devmedic procs[/]")
    elif d["mem"].percent >= 80:
        hit(7, "RAM usage is high → check the Memory panel")
    if d["swap"].total and d["swap"].percent >= 50:
        hit(5, "Heavy swap use — machine will feel slow")
    temp = d["sys"]["temp"]
    if temp and temp >= 85:
        hit(10, f"[red]CPU is hot ({temp:.0f}°C)[/] — check fans / heavy processes")
    elif temp and temp >= 75:
        hit(4, f"CPU is warm ({temp:.0f}°C)")
    user_c, sys_c = d["caches"]
    reclaim = sum(c["size"] for c in user_c) + sum(s["size"] or 0 for s in sys_c) + d["junk"]["total"]
    if reclaim >= 5 * 1024**3:
        hit(10, f"{human_size(reclaim)} reclaimable → [bold]devmedic caches[/] + [bold]devmedic junk[/]")
    elif reclaim >= 300 * 1024**2:
        hit(3, f"{human_size(reclaim)} reclaimable → [bold]devmedic caches[/]")
    g = d["git"]
    risky = len(g["noremote"]) + len(g["unpushed"])
    if risky:
        hit(min(10, 3 * risky), f"{plural(risky, 'repo')} with work that isn't backed up → [bold]devmedic repos[/]")
    stale = [x for x in procs.group(d["procs"]) if x["ports"] and time.time() - x["oldest"] > 86400]
    if stale:
        hit(3 * len(stale), f"{len(stale)} dev server(s) running > 1 day → [bold]devmedic ports[/]")
    if d["docker"]["state"] == "denied":
        hit(2, "Docker needs sudo → [bold]sudo usermod -aG docker $USER[/] then log out/in")
    bat = d["sys"]["battery"]
    if bat and not bat.power_plugged and bat.percent < 20:
        tips.append(f"[yellow]Battery low ({bat.percent:.0f}%)[/] — plug in before long builds")
    return max(score, 0), tips


def grade(score):
    for limit, g, color in ((90, "A", "green"), (75, "B", "cyan"), (60, "C", "yellow")):
        if score >= limit:
            return g, color
    return "D", "red"


# ───────────────────────── panels ─────────────────────────

def header(d, score):
    s = d["sys"]
    g, color = grade(score)
    info = Text.assemble(
        (f"{s['user']}", "bold cyan"), ("@", "grey50"), (f"{s['host']}\n", "bold magenta"),
        (f"{s['os']}", "bold"), (f"  ·  kernel {s['kernel']}\n", "grey62"),
        (f"up {human_age(s['boot']).replace(' ago', '')}", "green"),
        (f"  ·  {s['shell'] or 'shell ?'}  ·  {time.strftime('%a %d %b %H:%M')}", "grey62"),
    )
    score_txt = Text.assemble(("HEALTH ", "bold grey70"), (f"{score}", f"bold {color}"),
                              ("/100  ", "grey50"), (f" {g} ", f"bold black on {color}"))
    right = Group(info, Text(""), score_txt, score_bar(score))
    if console.width < 100:
        return Group(banner(), Text(""), right)
    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1)
    grid.add_row(banner(), right)
    return grid


def score_bar(score, width=26):
    filled = round(score / 100 * width)
    t = Text()
    for i in range(width):
        t.append("█" if i < filled else "░",
                 style=gradient_color(i / width) if i < filled else "grey35")
    return t


def system_panel(d):
    s, cores = d["sys"], d["cpu"]
    t = kv_table()
    t.add_row("CPU", f"{s['cpu']}  [grey62]({s['phys']}c/{s['cores']}t)[/]")
    total = sum(cores) / len(cores) if cores else 0
    t.add_row("Usage", bar(total))
    t.add_row("Cores", Text(spark(cores), style="bold " + level_color(total)))
    l1, l5, l15 = s["load"]
    t.add_row("Load", f"{l1:.2f}  {l5:.2f}  {l15:.2f}  "
                      f"[grey62](per core {l1 / s['cores']:.2f})[/]")
    if s["temp"]:
        t.add_row("Temp", Text(f"{s['temp']:.0f}°C", style="bold " + level_color(s["temp"], 70, 85)))
    b = s["battery"]
    if b:
        state = "⚡ charging" if b.power_plugged else "🔋 on battery"
        left = ""
        if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNKNOWN,
                                                     psutil.POWER_TIME_UNLIMITED):
            left = f", {b.secsleft // 3600}h{b.secsleft % 3600 // 60:02d}m left"
        t.add_row("Battery", Text.assemble(
            (f"{b.percent:.0f}% ", "bold " + level_color(100 - b.percent, 70, 85)),
            (state + left, "grey62")))
    return panel(t, "⚡ System", "cyan")


def memory_panel(d, limit=5):
    m, sw = d["mem"], d["swap"]
    t = kv_table()
    t.add_row("RAM", bar(m.percent))
    t.add_row("", f"[grey62]{human_size(m.total - m.available)} used · "
                  f"{human_size(m.available)} free of {human_size(m.total)}[/]")
    if sw.total:
        t.add_row("Swap", bar(sw.percent, warn=40, bad=70))
    hogs = Table.grid(padding=(0, 1))
    hogs.add_column(style="bold")
    hogs.add_column(justify="right", style="grey62")
    hogs.add_column(justify="right")
    for name, rss, count in d["hogs"][:limit]:
        hogs.add_row(name[:22], f"×{count}" if count > 1 else "",
                     Text(human_size(rss), style=level_color(rss / m.total * 100, 15, 30)))
    t.add_row("Top", hogs)
    return panel(t, "🧠 Memory", "magenta")


def storage_panel(d):
    t = kv_table()
    for x in d["disks"]:
        t.add_row(x["mount"], bar(x["pct"]))
        t.add_row("", f"[grey62]{human_size(x['total'] - x['used'])} free of "
                      f"{human_size(x['total'])} · {x['fs']} · {x['device']}[/]")
    return panel(t, "💾 Storage", "blue")


def tools_panel(d):
    installed = [(n, v) for n, v in d["tools"] if v]
    missing = [n for n, v in d["tools"] if not v]
    t = Table.grid(padding=(0, 2))
    cols = 2
    for _ in range(cols):
        t.add_column(no_wrap=True)
    cells = [Text.assemble(("✔ ", "green"), (f"{n:<7}", "bold"), (v, "cyan")) for n, v in installed]
    for i in range(0, len(cells), cols):
        t.add_row(*cells[i:i + cols], *[""] * (cols - len(cells[i:i + cols])))
    body = [t]
    dk = d["docker"]
    if dk["state"] == "ok":
        n = len(dk["containers"])
        body.append(Text(f"🐳 {n} container{'s' if n != 1 else ''} running",
                         style="green" if n else "grey62"))
    elif dk["state"] == "denied":
        body.append(Text("🐳 docker: permission denied (not in docker group)", style="yellow"))
    elif dk["state"] == "down":
        body.append(Text("🐳 docker daemon not running", style="grey62"))
    if missing:
        shown = ", ".join(missing[:6]) + (" …" if len(missing) > 6 else "")
        body.append(Text(f"{len(missing)} not installed: {shown}", style="grey50"))
    return panel(Group(*body), "🛠  Dev tools", "green")


def network_panel(d):
    n = d["net"]
    t = kv_table()
    if n["wifi"]:
        t.add_row("Wi-Fi", f"[bold]{n['wifi']}[/]")
    for name, ip in n["ips"][:3]:
        t.add_row(name, f"[cyan]{ip}[/]")
    if not n["ips"]:
        t.add_row("IP", "[yellow]offline[/]")
    t.add_row("Traffic", f"↑ {human_size(n['sent'])}  ↓ {human_size(n['recv'])} "
                         f"[grey62]since boot[/]")
    listen = d["ports"]
    if listen:
        t.add_row("Ports", ", ".join(f"[bold cyan]{p['port']}[/] {p['name']}" for p in listen[:6]))
    else:
        t.add_row("Ports", "[grey62]no dev servers listening[/]")
    return panel(t, "🌐 Network", "yellow")


def dev_panel(d):
    g, groups = d["git"], procs.group(d["procs"])
    t = kv_table()
    run_ram = sum(x["rss"] for x in groups)
    t.add_row("Procs", f"{plural(len(d['procs']), 'dev process', 'dev processes')} in "
                       f"{plural(len(groups), 'project')} · {human_size(run_ram)}")
    for x in groups[:3]:
        where = "~" if x["cwd"] == HOME else short_path(x["cwd"])
        names = ", ".join(sorted({p["name"] for p in x["procs"]}))
        t.add_row("", f"[grey62]{where[-38:]}[/] {names[:20]} {human_size(x['rss'])}")
    status = []
    if g["dirty"]:
        status.append(f"[yellow]{len(g['dirty'])} dirty[/]")
    if g["unpushed"]:
        status.append(f"[red]{len(g['unpushed'])} unpushed[/]")
    if g["noremote"]:
        status.append(f"[red]{len(g['noremote'])} no remote[/]")
    if g["stash"]:
        status.append(f"[magenta]{len(g['stash'])} stashed[/]")
    t.add_row("Git", f"{g['total']} repos  " + (" · ".join(status) or "[green]all clean ✔[/]"))
    for i in g["flagged"][:3]:
        t.add_row("", f"[grey62]{short_path(i['path'])[-40:]}[/] [cyan]{i['branch']}[/]")
    return panel(t, "📂 Projects", "bright_magenta")


def cleanup_panel(d):
    user_c, sys_c = d["caches"]
    j = d["junk"]
    t = kv_table()
    total = 0
    rows = [(c["label"], c["size"]) for c in user_c]
    rows += [(s["label"], s["size"]) for s in sys_c if s["size"]]
    rows.append((f"Junk folders ({j['count']})", j["total"]))
    rows = [r for r in rows if r[1]]
    rows.sort(key=lambda r: r[1], reverse=True)
    biggest = max((r[1] for r in rows), default=1)
    for label, size in rows[:6]:
        total += size
        w = max(1, round(size / biggest * 14))
        t.add_row(label[:24], Text.assemble(("▇" * w, "bright_red" if size > 1024**3 else "orange3"),
                                            (f" {human_size(size)}", "bold")))
    if not rows:
        t.add_row("", "[green]Nothing to clean ✨[/]")
    else:
        total = sum(r[1] for r in rows)
        t.add_row("Total", f"[bold green]{human_size(total)}[/] [grey62]reclaimable[/]")
    return panel(t, "🧹 Cleanup", "red")


def tips_panel(tips):
    if not tips:
        return panel(Text("Everything looks great — go build something! 🚀", style="bold green"),
                     "💡 Doctor's notes", "green")
    body = Text.from_markup("\n".join(f"• {tip}" for tip in tips))
    return panel(body, "💡 Doctor's notes", "yellow")


def two_columns(pairs, width):
    if width < 100:
        return Group(*[p for pair in pairs for p in pair if p])
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    for left, right in pairs:
        grid.add_row(left, right or "")
    return grid


def render(d):
    score, tips = health(d)
    pairs = [(system_panel(d), memory_panel(d)),
             (storage_panel(d), network_panel(d)),
             (tools_panel(d), dev_panel(d)),
             (cleanup_panel(d), tips_panel(tips))]
    return Group(header(d, score), Text(""), two_columns(pairs, console.width),
                 Align.center(Text("devmedic ports · kill · junk · caches · repos · procs · "
                                   "watch  —  --help for more", style="grey50")))


def gather():
    with console.status("[bold cyan]Examining your machine…[/]", spinner="dots12"):
        return collect_all()


def show(d=None):
    console.print(render(d or gather()))


# ───────────────────────── live mode ─────────────────────────

def watch(interval=1.0):
    """Live, full-screen dashboard: CPU/RAM/net history, ports and dev procs."""
    cpu_hist = deque(maxlen=48)
    mem_hist = deque(maxlen=48)
    net_prev = psutil.net_io_counters()
    t_prev = time.time()
    s = system_info()
    psutil.cpu_percent(percpu=True)

    def frame():
        nonlocal net_prev, t_prev
        cores = psutil.cpu_percent(percpu=True)
        total = sum(cores) / len(cores)
        mem = psutil.virtual_memory()
        cpu_hist.append(total)
        mem_hist.append(mem.percent)
        io, now = psutil.net_io_counters(), time.time()
        dt = max(now - t_prev, 1e-3)
        up = (io.bytes_sent - net_prev.bytes_sent) / dt
        down = (io.bytes_recv - net_prev.bytes_recv) / dt
        net_prev, t_prev = io, now

        top = Table.grid(expand=True, padding=(0, 2))
        top.add_column(no_wrap=True)
        top.add_column(ratio=1)
        top.add_row(banner(), Text.assemble(
            (f"{s['user']}@{s['host']}\n", "bold cyan"), (f"{s['os']}\n", "bold"),
            (f"{s['cpu']}\n", "grey62"),
            (f"LIVE ● {time.strftime('%H:%M:%S')}", "bold red"), ("   Ctrl+C to exit", "grey50")))

        vitals = kv_table()
        vitals.add_row("CPU", bar(total))
        vitals.add_row("", Text(spark(cpu_hist).rjust(48, " "), style="cyan"))
        vitals.add_row("Cores", Text(" ".join(f"{c:3.0f}" for c in cores), style=level_color(total)))
        vitals.add_row("RAM", bar(mem.percent))
        vitals.add_row("", Text(spark(mem_hist).rjust(48, " "), style="magenta"))
        temp = temperature()
        if temp:
            vitals.add_row("Temp", Text(f"{temp:.0f}°C", style="bold " + level_color(temp, 70, 85)))
        vitals.add_row("Net", f"↑ [green]{human_size(up)}/s[/]  ↓ [cyan]{human_size(down)}/s[/]")

        pt = Table(expand=True, box=None, header_style="bold grey70", padding=(0, 1))
        pt.add_column("Port", style="bold cyan", justify="right")
        pt.add_column("Process")
        pt.add_column("Project", style="grey62", overflow="ellipsis", no_wrap=True)
        for r in ports.listening()[:8]:
            pt.add_row(str(r["port"]), r["name"], short_path(r["cwd"]) if r["cwd"] else "")
        if not pt.row_count:
            pt.add_row("", "[grey62]none[/]", "")

        ht = Table(expand=True, box=None, header_style="bold grey70", padding=(0, 1))
        ht.add_column("Top processes")
        ht.add_column("Count", justify="right", style="grey62")
        ht.add_column("RAM", justify="right")
        for name, rss, count in memory_hogs(8):
            ht.add_row(name[:28], f"×{count}" if count > 1 else "",
                       Text(human_size(rss), style=level_color(rss / mem.total * 100, 15, 30)))

        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(panel(vitals, "⚡ Vitals", "cyan"), panel(ht, "🧠 Memory hogs", "magenta"))
        grid.add_row(panel(pt, "🔌 Listening ports", "yellow"),
                     panel(disk_mini(), "💾 Storage", "blue"))
        return Group(top, Text(""), grid)

    try:
        with Live(frame(), console=console, screen=True, refresh_per_second=4) as live:
            while True:
                time.sleep(interval)
                live.update(frame())
    except KeyboardInterrupt:
        pass


def disk_mini():
    t = kv_table()
    for x in disks():
        t.add_row(x["mount"], bar(x["pct"]))
    return t
