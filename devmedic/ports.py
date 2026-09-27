"""Who is listening on which port, and killing them."""
import os
import signal
import time

import psutil
from rich.table import Table

from .util import confirm, console, short_path


# Friendly names for common root-owned services we can't inspect.
WELL_KNOWN = {22: "sshd", 25: "smtp", 53: "DNS (systemd-resolved)", 80: "http",
              443: "https", 631: "CUPS printing", 3306: "MySQL", 5432: "PostgreSQL",
              6379: "Redis", 27017: "MongoDB", 5353: "mDNS (avahi)", 2375: "Docker",
              11434: "Ollama"}


def listening(include_system=False):
    """List of dicts describing TCP listeners (deduped across IPv4/IPv6)."""
    rows = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        conns = []  # locked-down containers / non-Linux without root
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        port = c.laddr.port
        key = (port, c.pid)
        if key in rows:
            rows[key]["addrs"].add(c.laddr.ip)
            continue
        info = {"port": port, "pid": c.pid, "addrs": {c.laddr.ip},
                "name": WELL_KNOWN.get(port, "?"), "cmd": "", "cwd": "", "user": "", "mine": False}
        if c.pid:
            try:
                p = psutil.Process(c.pid)
                info["name"] = p.name()
                info["cmd"] = " ".join(" ".join(p.cmdline()).split())
                info["user"] = p.username()
                info["mine"] = p.uids().real == os.getuid()
                info["cwd"] = p.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
        rows[key] = info
    result = sorted(rows.values(), key=lambda r: r["port"])
    if not include_system:
        result = [r for r in result if r["mine"]]
    return result


def exposure(addrs):
    if all(a.startswith("127.") or a == "::1" for a in addrs):
        return "[green]local only[/]"
    return "[yellow]network[/]"


def show(include_system=False):
    rows = listening(include_system)
    if not rows:
        console.print("[green]No listening ports owned by you.[/] "
                      "[dim](use --all to include system services)[/]")
        return
    t = Table(title="Listening ports", title_justify="left")
    t.add_column("Port", style="bold cyan", justify="right")
    t.add_column("PID", justify="right")
    t.add_column("Process")
    t.add_column("Exposed")
    t.add_column("Project dir", style="dim")
    t.add_column("Command", overflow="ellipsis", max_width=60, no_wrap=True)
    for r in rows:
        t.add_row(str(r["port"]), str(r["pid"] or "?"), r["name"],
                  exposure(r["addrs"]), short_path(r["cwd"]) if r["cwd"] else "",
                  r["cmd"])
    console.print(t)
    if not include_system:
        console.print("[dim]Showing your processes only. --all for system services.[/]")


def kill(port, force=False, assume_yes=False):
    targets = [r for r in listening(include_system=True) if r["port"] == port]
    if not targets:
        console.print(f"[green]Nothing is listening on port {port}.[/]")
        return 0
    status = 0
    for r in targets:
        if not r["pid"]:
            console.print(f"[red]Port {port} is held by a process you can't see "
                          f"(probably root).[/] Try: [bold]sudo ss -ltnp 'sport = :{port}'[/]")
            status = 1
            continue
        console.print(f"Port [bold cyan]{port}[/] → PID {r['pid']} "
                      f"[bold]{r['name']}[/] [dim]{r['cmd'][:100]}[/]")
        if not r["mine"]:
            console.print(f"[yellow]Owned by {r['user'] or 'another user'}; "
                          f"you may need sudo.[/]")
        if not confirm("Kill it?", assume_yes):
            continue
        status |= terminate(r["pid"], force)
    return status


def terminate(pid, force=False):
    try:
        p = psutil.Process(pid)
        if force:
            p.kill()
        else:
            p.terminate()
            try:
                p.wait(timeout=3)
            except psutil.TimeoutExpired:
                console.print("[yellow]Didn't exit after SIGTERM, sending SIGKILL…[/]")
                p.kill()
        p.wait(timeout=3)
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        console.print(f"[red]Permission denied.[/] Try: [bold]sudo kill {pid}[/]")
        return 1
    except psutil.TimeoutExpired:
        console.print(f"[red]PID {pid} is still alive.[/]")
        return 1
    console.print(f"[green]✔ Killed PID {pid}[/]")
    return 0
