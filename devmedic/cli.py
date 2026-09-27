"""devmedic — a health check for your developer machine."""
import argparse
import os
import sys

from . import __version__, caches, dashboard, junk, menu, ports, procs, repos
from .util import console


def cmd_overview(args):
    # Interactive menu in a real terminal; full dashboard when piped/redirected.
    if sys.stdin.isatty() and sys.stdout.isatty():
        menu.run()
    else:
        dashboard.show()
    return 0


def cmd_all(args):
    dashboard.show()
    return 0


def cmd_section(args):
    menu.show_section(args.cmd)
    return 0


def cmd_watch(args):
    dashboard.watch(args.interval)
    return 0


def cmd_ports(args):
    ports.show(args.all)
    return 0


def cmd_kill(args):
    return ports.kill(args.port, args.force, args.yes)


def cmd_junk(args):
    items = junk.scan(args.path, args.older)
    junk.show(items, args.limit, args.path)
    if args.clean:
        junk.clean(items, args.yes, args.limit)
    elif items:
        console.print("[dim]Run again with --clean to pick folders to delete. "
                      "--older 30 shows only projects untouched for 30 days.[/]")
    return 0


def cmd_caches(args):
    user, system = caches.collect()
    caches.show(user, system)
    if args.clean:
        console.print()
        caches.clean(user, args.clean, args.yes)
    return 0


def cmd_repos(args):
    repos.show(repos.scan(args.path), args.all, args.path)
    return 0


def cmd_procs(args):
    procs.show(procs.collect(), args.verbose)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="devmedic", description=__doc__)
    p.add_argument("--version", action="version", version=f"devmedic {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("menu", help="interactive section picker (default)")
    s.set_defaults(func=cmd_overview)

    s = sub.add_parser("all", aliases=["check"], help="full dashboard on one screen")
    s.set_defaults(func=cmd_all)

    for key, icon, title, *_ in menu.SECTIONS:
        s = sub.add_parser(key, help=f"{title} section")
        s.set_defaults(func=cmd_section)

    s = sub.add_parser("watch", help="live full-screen dashboard (Ctrl+C to exit)")
    s.add_argument("-n", "--interval", type=float, default=1.0, metavar="SEC")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("ports", help="what is listening on which port")
    s.add_argument("-a", "--all", action="store_true", help="include system services")
    s.set_defaults(func=cmd_ports)

    s = sub.add_parser("kill", help="kill whatever holds a port")
    s.add_argument("port", type=int)
    s.add_argument("-f", "--force", action="store_true", help="SIGKILL immediately")
    s.add_argument("-y", "--yes", action="store_true", help="don't ask")
    s.set_defaults(func=cmd_kill)

    s = sub.add_parser("junk", help="find node_modules, .venv, target/ … and their sizes")
    s.add_argument("path", nargs="?", default=os.path.expanduser("~"))
    s.add_argument("--older", type=int, default=0, metavar="DAYS",
                   help="only projects untouched for DAYS days")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--clean", action="store_true", help="choose folders to delete")
    s.add_argument("-y", "--yes", action="store_true", help="delete all listed without asking")
    s.set_defaults(func=cmd_junk)

    s = sub.add_parser("caches", help="tool & system caches (pip, npm, docker, journal …)")
    s.add_argument("--clean", nargs="+", metavar="KEY", help="cache keys to clean, or 'all'")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_caches)

    s = sub.add_parser("repos", help="git repos with uncommitted / unpushed work")
    s.add_argument("path", nargs="?", default=os.path.expanduser("~"))
    s.add_argument("-a", "--all", action="store_true", help="show clean repos too")
    s.set_defaults(func=cmd_repos)

    s = sub.add_parser("procs", help="dev processes grouped by project")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_procs)
    return p


WSL_URL = "https://learn.microsoft.com/windows/wsl/install"


def main(argv=None):
    args = build_parser().parse_args(argv)
    if sys.platform == "win32":
        console.print("[bold yellow]devmedic doesn't support native Windows yet.[/]\n"
                      "It works great inside WSL (Ubuntu on Windows):\n"
                      f"  1. Install WSL: [cyan]{WSL_URL}[/]\n"
                      "  2. In the Ubuntu terminal: [bold]pipx install devmedic[/]\n"
                      "Native Windows support is coming in a future release.")
        return 1
    if not sys.platform.startswith("linux"):
        console.print("[yellow]devmedic is built for Linux; some checks may be "
                      "missing on this system.[/]")
    if not getattr(args, "func", None):
        args.func = cmd_overview
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
