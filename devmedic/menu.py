"""Interactive section picker: `devmedic` with no arguments in a terminal."""
import os
import select
import sys

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import caches, dashboard as db, ports, procs, repos
from .util import WINDOWS, console, human_size, short_path

# ───────────────────────── section details ─────────────────────────


def detail_system(d):
    console.print(db.system_panel(d))
    t = Table(title="Per-core usage", title_justify="left", box=None, padding=(0, 1))
    t.add_column("Core", style="bold grey70", justify="right")
    t.add_column("Usage")
    for i, pct in enumerate(d["cpu"]):
        t.add_row(str(i), db.bar(pct, 30))
    console.print(t)


def detail_memory(d):
    console.print(db.memory_panel(d, limit=12))
    console.print("[dim]Processes are grouped by app (e.g. all browser tabs together).[/]")


def detail_storage(d):
    console.print(db.storage_panel(d))
    console.print("[dim]Free space fast: devmedic junk --clean · devmedic caches[/]")


def detail_network(d):
    console.print(db.network_panel(d))
    ports.show()


def detail_tools(d):
    console.print(db.tools_panel(d))
    dk = d["docker"]
    if dk["state"] == "ok" and dk["containers"]:
        t = Table(title="Running containers", title_justify="left")
        for col in ("Name", "Image", "Status"):
            t.add_column(col)
        for row in dk["containers"]:
            t.add_row(*row[:3])
        console.print(t)
    elif dk["state"] == "denied":
        console.print(db.DOCKER_FIX)


def detail_projects(d):
    procs.show(d["procs"])
    console.print()
    repos.show(d["git"]["infos"], show_all=True)


def detail_cleanup(d):
    user_c, sys_c = d["caches"]
    caches.show(user_c, sys_c)
    j = d["junk"]
    console.print()
    if j["count"]:
        console.print(f"[bold]{j['count']} junk folders[/] ({human_size(j['total'])}), biggest:")
        for size, path in j["top"]:
            console.print(f"  {human_size(size):>9}  {short_path(path)}")
        console.print("[dim]Review & delete: devmedic junk --clean[/]")
    else:
        console.print("[green]No node_modules / .venv / target junk found in ~ ✨[/]")


def detail_notes(d):
    _, tips = db.health(d)
    console.print(db.tips_panel(tips))


# ───────────────────────── menu summaries ─────────────────────────


def _c(text, color):
    return f"[{color}]{text}[/]"


def sum_system(d):
    s, cores = d["sys"], d["cpu"]
    total = sum(cores) / len(cores) if cores else 0
    parts = [_c(f"CPU {total:.0f}%", db.level_color(total))]
    if s["temp"]:
        parts.append(_c(f"{s['temp']:.0f}°C", db.level_color(s["temp"], 70, 85)))
    if s["battery"]:
        b = s["battery"]
        parts.append(_c(f"battery {b.percent:.0f}%{' ⚡' if b.power_plugged else ''}",
                        db.level_color(100 - b.percent, 70, 85)))
    return " · ".join(parts)


def sum_memory(d):
    m = d["mem"]
    top = d["hogs"][0] if d["hogs"] else None
    return (_c(f"RAM {m.percent:.0f}%", db.level_color(m.percent, 75, 90))
            + (f" · {top[0]} {human_size(top[1])}" if top else ""))


def sum_storage(d):
    return " · ".join(_c(f"{x['mount']} {x['pct']:.0f}%", db.level_color(x["pct"]))
                      for x in d["disks"][:3])


def sum_network(d):
    n = d["net"]
    where = n["wifi"] or (n["ips"][0][0] if n["ips"] else None)
    ip = n["ips"][0][1] if n["ips"] else None
    head = f"{where} {ip}" if ip else _c("offline", "yellow")
    return f"{head} · {db.plural(len(d['ports']), 'port')} listening"


def sum_tools(d):
    installed = sum(1 for _, v in d["tools"] if v)
    dk = {"ok": _c("docker ✔", "green"), "denied": _c("docker needs permission", "yellow"),
          "down": _c("docker stopped", "grey62"), "missing": ""}[d["docker"]["state"]]
    return " · ".join(x for x in (f"{installed} tools installed", dk) if x)


def sum_projects(d):
    g = d["git"]
    risky = len(g["noremote"]) + len(g["unpushed"])
    parts = [f"{db.plural(g['total'], 'repo')}"]
    parts.append(_c(f"{risky} not backed up", "red") if risky else _c("all backed up", "green"))
    parts.append(f"{db.plural(len(d['procs']), 'dev process', 'dev processes')}")
    return " · ".join(parts)


def sum_cleanup(d):
    user_c, sys_c = d["caches"]
    total = (sum(c["size"] for c in user_c) + sum(s["size"] or 0 for s in sys_c)
             + d["junk"]["total"])
    color = "red" if total > 5 * 1024**3 else "yellow" if total > 300 * 1024**2 else "green"
    return _c(f"{human_size(total)} reclaimable", color)


def sum_notes(d):
    _, tips = db.health(d)
    return _c(f"{db.plural(len(tips), 'suggestion')}", "yellow") if tips else _c("all good ✨", "green")


# key, icon, title, summary(d), detail(d)
SECTIONS = [
    ("system",   "⚡", "System",         sum_system,   detail_system),
    ("memory",   "🧠", "Memory",         sum_memory,   detail_memory),
    ("storage",  "💾", "Storage",        sum_storage,  detail_storage),
    ("network",  "🌐", "Network",        sum_network,  detail_network),
    ("tools",    "🛠 ", "Dev tools",      sum_tools,    detail_tools),
    ("projects", "📂", "Projects",       sum_projects, detail_projects),
    ("cleanup",  "🧹", "Cleanup",        sum_cleanup,  detail_cleanup),
    ("notes",    "💡", "Doctor's notes", sum_notes,    detail_notes),
]
EXTRAS = [  # key, icon, title, hint
    ("all",     "📊", "Everything",   "full dashboard on one screen"),
    ("watch",   "📡", "Live monitor", "real-time CPU / RAM / network"),
    ("refresh", "🔄", "Refresh",      "re-scan the machine"),
    ("quit",    "🚪", "Quit",         ""),
]
SECTION_KEYS = [s[0] for s in SECTIONS]


def show_section(key, d=None):
    """Print one section (used by `devmedic memory` etc.)."""
    d = d or db.gather()
    for k, icon, title, _, detail in SECTIONS:
        if k == key:
            if key in ("projects", "cleanup"):  # the others open with their own panel
                console.rule(f"[bold cyan]{icon} {title}[/]", align="left", style="cyan")
            detail(d)
            return


# ───────────────────────── keyboard ─────────────────────────

KEYS = {b"\x1b[A": "up", b"\x1bOA": "up", b"k": "up",
        b"\x1b[B": "down", b"\x1bOB": "down", b"j": "down",
        b"\r": "enter", b"\n": "enter", b" ": "enter", b"\x1b[C": "enter", b"l": "enter",
        b"q": "quit", b"Q": "quit", b"\x1b": "back", b"\x1b[D": "back", b"h": "back",
        b"r": "refresh", b"a": "all", b"w": "watch", b"\x1b[H": "home", b"\x1b[F": "end"}


# Windows (msvcrt) sends special keys as a prefix + one letter.
WINDOWS_KEYS = {"H": "up", "P": "down", "M": "enter", "K": "back", "G": "home", "O": "end"}


def decode_windows_key(first, second=""):
    """Map msvcrt.getwch() output to our key names (pure, so it's testable anywhere)."""
    if first in ("\x00", "\xe0"):
        return WINDOWS_KEYS.get(second, "other")
    if first == "\x03":
        raise KeyboardInterrupt
    if first.isdigit():
        return first
    return KEYS.get(first.encode("utf-8", "ignore"), "other")


def read_key():
    if WINDOWS:
        import msvcrt

        first = msvcrt.getwch()
        second = msvcrt.getwch() if first in ("\x00", "\xe0") else ""
        return decode_windows_key(first, second)

    import termios  # POSIX only — imported here so the package loads everywhere
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        data = os.read(fd, 1)
        # Escape sequences arrive as a burst; grab the rest if it's there.
        while data.startswith(b"\x1b") and select.select([fd], [], [], 0.03)[0]:
            data += os.read(fd, 8)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if data.isdigit():
        return data.decode()
    return KEYS.get(data, "other")


def wait_key(msg="Press any key to go back · q to quit"):
    console.print()
    console.print(Text(f"  {msg}", style="grey50"))
    return read_key()


# ───────────────────────── menu screen ─────────────────────────


def menu_body(d, cursor):
    items = [(k, icon, title, Text.from_markup(summ(d))) for k, icon, title, summ, _ in SECTIONS]
    items += [(k, icon, title, Text(hint, style="grey50")) for k, icon, title, hint in EXTRAS]
    t = Table.grid(padding=(0, 1))
    t.add_column(width=2)                           # pointer
    t.add_column(width=3, justify="right", style="grey50")  # shortcut
    t.add_column(no_wrap=True)                      # icon + title
    t.add_column()                                  # summary
    for i, (key, icon, title, summary) in enumerate(items):
        if i == len(SECTIONS):
            t.add_row("", "", Text("─" * 18, style="grey35"), "")
        selected = i == cursor
        shortcut = str(i + 1) if i < len(SECTIONS) else {"all": "a", "watch": "w",
                                                          "refresh": "r", "quit": "q"}[key]
        label = Text(f"{icon} {title}", style="bold reverse cyan" if selected else "bold")
        t.add_row(Text("❯", style="bold magenta") if selected else "", shortcut, label, summary)
    return t, [it[0] for it in items]


def render_menu(d, cursor):
    score, _ = db.health(d)
    body, keys = menu_body(d, cursor)
    footer = Text("↑↓ / j k move · Enter open · 1-8 jump · a all · w live · r refresh · q quit",
                  style="grey50")
    menu = Panel(Group(body, Text(""), footer), title="[bold cyan]Choose a section[/]",
                 title_align="left", border_style="cyan", padding=(1, 2))
    return Group(db.header(d, score), Text(""), menu), keys


def run():
    d = db.gather()
    cursor = 0
    while True:
        console.clear()
        screen, keys = render_menu(d, cursor)
        console.print(screen)
        key = read_key()
        if key.isdigit():
            n = int(key) - 1
            if 0 <= n < len(keys):
                cursor = n
                key = "enter"
            else:
                continue
        if key in ("all", "watch", "refresh"):
            cursor = keys.index(key)
            key = "enter"
        if key == "up":
            cursor = (cursor - 1) % len(keys)
        elif key == "down":
            cursor = (cursor + 1) % len(keys)
        elif key == "home":
            cursor = 0
        elif key == "end":
            cursor = len(keys) - 1
        elif key in ("quit", "back"):
            break
        elif key == "enter":
            choice = keys[cursor]
            if choice == "quit":
                break
            if choice == "refresh":
                console.clear()
                d = db.gather()
                continue
            console.clear()
            if choice == "watch":
                db.watch()
                continue
            if choice == "all":
                db.show(d)
            else:
                show_section(choice, d)
            if wait_key() == "quit":
                break
    console.clear()
    console.print("[bold cyan]🩺 devmedic[/] — stay healthy! 👋")
