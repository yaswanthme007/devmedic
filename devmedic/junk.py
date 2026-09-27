"""Find regenerable build/dependency folders across your projects."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .util import (HOME, confirm, console, dir_size, display_path, human_age, human_size,
                   remove_tree, short_path, size_style)

# name -> (kind, check) ; check(parent_dir, junk_dir) says whether it's really junk
def _sibling(*names):
    return lambda parent, _d: any(os.path.exists(os.path.join(parent, n)) for n in names)

def _always(_parent, _d):
    return True

def _is_venv(_parent, d):
    return os.path.exists(os.path.join(d, "pyvenv.cfg"))

PATTERNS = {
    "node_modules":  ("Node deps",     _always),
    ".venv":         ("Python venv",   _is_venv),
    "venv":          ("Python venv",   _is_venv),
    "env":           ("Python venv",   _is_venv),
    "target":        ("Rust/Maven",    _sibling("Cargo.toml", "pom.xml")),
    ".next":         ("Next.js build", _always),
    ".nuxt":         ("Nuxt build",    _always),
    ".svelte-kit":   ("SvelteKit",     _always),
    ".parcel-cache": ("Parcel cache",  _always),
    ".turbo":        ("Turbo cache",   _always),
    ".angular":      ("Angular cache", _always),
    ".gradle":       ("Gradle cache",  _sibling("build.gradle", "build.gradle.kts",
                                                "settings.gradle", "settings.gradle.kts")),
    ".dart_tool":    ("Dart/Flutter",  _sibling("pubspec.yaml")),
    ".tox":          ("tox envs",      _always),
    ".pytest_cache": ("pytest cache",  _always),
    ".mypy_cache":   ("mypy cache",    _always),
    ".ruff_cache":   ("ruff cache",    _always),
}

# Never descend into these (huge, or not your projects).
SKIP_DIRS = {".git", ".cache", ".local", ".npm", ".cargo", ".rustup", "snap",
             ".mozilla", ".config", ".vscode", ".vscode-server", ".var",
             ".m2", ".gradle", ".nvm", ".pyenv", ".docker", ".Trash"}
# Skipped only directly under $HOME / filesystem root.
SKIP_AT_TOP = {"go", "proc", "sys", "dev", "run", "tmp", "boot", "lost+found"}


def find(root, max_depth=8):
    """Yield (junk_path, kind) — does not descend into junk it found."""
    root = os.path.abspath(root)
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            pattern = PATTERNS.get(e.name)
            if pattern and pattern[1](current, e.path):
                yield e.path, pattern[0]
                continue
            at_top = current in (HOME, "/")
            if depth < max_depth and e.name not in SKIP_DIRS and not (
                    at_top and (e.name.startswith(".") or e.name in SKIP_AT_TOP)):
                stack.append((e.path, depth + 1))


def project_last_touched(junk_path):
    """Newest mtime among the project's own top-level files (not the junk)."""
    parent = os.path.dirname(junk_path)
    newest = 0
    try:
        for e in os.scandir(parent):
            if e.path == junk_path or e.name in PATTERNS:
                continue
            try:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return newest or os.stat(junk_path).st_mtime


def scan(root, older_than_days=0):
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  console=console, transient=True) as prog:
        task = prog.add_task(f"Scanning {short_path(os.path.abspath(root))} …")
        found = []
        for path, kind in find(root):
            found.append((path, kind))
            prog.update(task, description=f"Found {len(found)} — {short_path(path)}")
        prog.update(task, description=f"Measuring {len(found)} folders …")
        with ThreadPoolExecutor(max_workers=8) as pool:
            sizes = list(pool.map(dir_size, [p for p, _ in found]))
    cutoff = time.time() - older_than_days * 86400
    items = []
    for (path, kind), size in zip(found, sizes):
        touched = project_last_touched(path)
        if older_than_days and touched > cutoff:
            continue
        items.append({"path": path, "kind": kind, "size": size, "touched": touched})
    items.sort(key=lambda i: i["size"], reverse=True)
    return items


def show(items, limit=40, root=None):
    if not items:
        console.print("[green]No junk folders found. Clean machine! ✨[/]")
        return
    total = sum(i["size"] for i in items)
    t = Table(title=f"Regenerable folders — {human_size(total)} total",
              title_justify="left")
    t.add_column("#", justify="right", style="dim")
    t.add_column("Size", justify="right")
    t.add_column("Type")
    t.add_column("Project last touched", justify="right")
    t.add_column("Path", overflow="fold")
    for n, i in enumerate(items[:limit], 1):
        t.add_row(str(n), f"[{size_style(i['size'])}]{human_size(i['size'])}[/]",
                  i["kind"], human_age(i["touched"]), display_path(i["path"], root))
    console.print(t)
    if len(items) > limit:
        rest = items[limit:]
        console.print(f"[dim]…and {len(rest)} more "
                      f"({human_size(sum(i['size'] for i in rest))}). Use --limit.[/]")


def parse_selection(text, count):
    """'1,3,5-7' / 'all' -> sorted list of 0-based indices."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return list(range(count))
    picked = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            picked.update(range(int(lo), int(hi) + 1))
        else:
            picked.add(int(part))
    return sorted(i - 1 for i in picked if 1 <= i <= count)


def clean(items, assume_yes=False, limit=40):
    if not items:
        return
    shown = items[:limit]
    if assume_yes:
        chosen = shown
    else:
        try:
            answer = console.input("[bold]Delete which?[/] [dim](e.g. 1,3,5-8 / all / "
                                   "Enter to cancel)[/] ")
            chosen = [shown[i] for i in parse_selection(answer, len(shown))]
        except ValueError:
            console.print("[red]Couldn't parse that selection.[/]")
            return
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
    if not chosen:
        console.print("Nothing deleted.")
        return
    total = sum(i["size"] for i in chosen)
    console.print(f"About to delete [bold]{len(chosen)}[/] folders "
                  f"([bold]{human_size(total)}[/]). They can be regenerated "
                  f"(npm install, pip install, cargo build…).")
    if not confirm("Proceed?", assume_yes):
        return
    freed = 0
    for i in chosen:
        if remove_tree(i["path"]):
            freed += i["size"]
            console.print(f"[green]✔[/] {short_path(i['path'])}")
        else:
            console.print(f"[red]✘ couldn't fully remove[/] {short_path(i['path'])}")
    console.print(f"[bold green]Freed {human_size(freed)}[/]")
