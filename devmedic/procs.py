"""Dev processes grouped by the project (cwd) they were started from."""
import os
import time
from collections import defaultdict

import psutil
from rich.table import Table

from .util import HOME, console, human_age, human_size, is_mine, short_path, size_style

DEV_NAMES = {"node", "python", "python3", "java", "ruby", "go", "cargo", "rustc",
             "deno", "bun", "php", "dotnet", "gradle", "mvn", "npm", "pnpm", "yarn",
             "vite", "esbuild", "webpack", "next-server", "uvicorn", "gunicorn",
             "flask", "celery", "jupyter", "jupyter-lab", "postgres", "mysqld",
             "mongod", "redis-server", "tsserver", "ng", "air", "nodemon",
             "docker-proxy", "containerd-shim", "gopls", "rust-analyzer", "pyright",
             "language_server", "kotlin", "emulator", "qemu-system-x86_64", "adb"}


def is_dev(name, cmdline):
    base = name.split(".")[0].lower()
    if base in DEV_NAMES or name.lower() in DEV_NAMES:
        return True
    if not cmdline:
        return False
    exe = os.path.basename(cmdline[0].replace("\\", "/")).lower()
    return exe in DEV_NAMES or os.path.splitext(exe)[0] in DEV_NAMES


def collect():
    ports = defaultdict(set)
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.pid:
                ports[c.pid].add(c.laddr.port)
    except (psutil.AccessDenied, OSError):
        pass
    self_pid = os.getpid()
    rows = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "create_time",
                                  "username", "cwd"]):
        info = p.info
        if info["pid"] == self_pid or not is_mine(info["username"]):
            continue
        if not is_dev(info["name"] or "", info["cmdline"] or []):
            continue
        rows.append({"pid": info["pid"], "name": info["name"],
                     "cmd": " ".join(" ".join(info["cmdline"] or []).split()),
                     "rss": info["memory_info"].rss if info["memory_info"] else 0,
                     "started": info["create_time"] or time.time(),
                     "cwd": info["cwd"] or "?", "ports": sorted(ports.get(info["pid"], ())),
                     "proc": p})
    # One CPU sample over a short window for everything at once.
    for r in rows:
        try:
            r["proc"].cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    for r in rows:
        try:
            r["cpu"] = r["proc"].cpu_percent(None)
        except psutil.Error:
            r["cpu"] = 0.0
    return rows


def group(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["cwd"]].append(r)
    out = [{"cwd": cwd, "procs": ps, "rss": sum(p["rss"] for p in ps),
            "cpu": sum(p["cpu"] for p in ps),
            "ports": sorted({x for p in ps for x in p["ports"]}),
            "oldest": min(p["started"] for p in ps)}
           for cwd, ps in groups.items()]
    return sorted(out, key=lambda g: g["rss"], reverse=True)


def show(rows, detail=False):
    if not rows:
        console.print("[green]No dev processes running.[/]")
        return
    vm = psutil.virtual_memory()
    groups = group(rows)
    total = sum(g["rss"] for g in groups)
    t = Table(title=f"Dev processes — {human_size(total)} RAM "
                    f"({total / vm.total * 100:.0f}% of {human_size(vm.total)})",
              title_justify="left")
    t.add_column("Started in", overflow="fold")
    t.add_column("RAM", justify="right")
    t.add_column("CPU", justify="right")
    t.add_column("Procs", justify="right")
    t.add_column("Ports", style="cyan")
    t.add_column("Running for", justify="right")
    t.add_column("What", overflow="ellipsis", no_wrap=True, max_width=50)
    for g in groups:
        names = sorted({p["name"] for p in g["procs"]})
        cwd = g["cwd"]
        label = "~" if cwd == HOME else short_path(cwd)
        t.add_row(label, f"[{size_style(g['rss'] * 4)}]{human_size(g['rss'])}[/]",
                  f"{g['cpu']:.0f}%", str(len(g["procs"])),
                  ", ".join(map(str, g["ports"])),
                  human_age(g["oldest"]).replace(" ago", ""), ", ".join(names))
        if detail:
            for p in sorted(g["procs"], key=lambda p: p["rss"], reverse=True):
                t.add_row(f"  [dim]pid {p['pid']}[/]", f"[dim]{human_size(p['rss'])}[/]",
                          f"[dim]{p['cpu']:.0f}%[/]", "",
                          ", ".join(map(str, p["ports"])), "", f"[dim]{p['cmd']}[/]")
    console.print(t)
    stale = [g for g in groups if time.time() - g["oldest"] > 86400 and g["ports"]]
    if stale:
        console.print(f"[yellow]💡 {len(stale)} server(s) running for over a day — "
                      f"forgotten dev servers? Kill with [bold]devmedic kill <port>[/].[/]")
    if not detail:
        console.print("[dim]Use -v to see individual processes.[/]")
