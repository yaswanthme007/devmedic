"""Shared helpers: sizes, ages, confirmation prompts."""
import os
import shutil
import subprocess
import time

from rich.console import Console

console = Console()
HOME = os.path.expanduser("~")


def dir_size(path):
    """Actual disk usage of a directory tree in bytes (doesn't follow symlinks)."""
    total = 0
    seen = set()
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    # Hardlinked files (pnpm store, etc.) count once.
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen:
                            continue
                        seen.add(key)
                    total += st.st_blocks * 512
        except OSError:
            continue
    return total


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def size_style(n):
    if n >= 1024**3:
        return "bold red"
    if n >= 200 * 1024**2:
        return "yellow"
    return "green"


def human_age(ts):
    days = (time.time() - ts) / 86400
    if days < 1:
        hours = days * 24
        return "just now" if hours < 1 else f"{hours:.0f}h ago"
    if days < 60:
        return f"{days:.0f}d ago"
    if days < 730:
        return f"{days / 30:.0f}mo ago"
    return f"{days / 365:.1f}y ago"


def short_path(path):
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


def display_path(path, root=None):
    """Path relative to the scan root (when that's shorter), else ~-style."""
    if root:
        root = os.path.abspath(root)
        if root != HOME and path.startswith(root + os.sep):
            return os.path.relpath(path, root)
    return short_path(path)


def confirm(question, assume_yes=False):
    if assume_yes:
        return True
    try:
        answer = console.input(f"[bold]{question}[/] [dim](y/N)[/] ")
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return answer.strip().lower() in ("y", "yes")


def run(cmd, timeout=15):
    """Run a command, return stdout ('' on any failure)."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def have(binary):
    return shutil.which(binary) is not None


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)
