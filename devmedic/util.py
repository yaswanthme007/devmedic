"""Shared helpers: sizes, ages, confirmation prompts, cross-platform bits."""
import getpass
import os
import shutil
import stat
import subprocess
import sys
import time

from rich.console import Console

console = Console()
HOME = os.path.expanduser("~")
WINDOWS = sys.platform == "win32"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def is_real_dir(entry):
    """A directory we may descend into: not a symlink, and on Windows not a
    junction/reparse point (pnpm and Windows itself use junctions)."""
    try:
        if not entry.is_dir(follow_symlinks=False):
            return False
        if WINDOWS:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
            return not attrs & _REPARSE_POINT
    except OSError:
        return False
    return True


def file_bytes(st):
    """Bytes a file really occupies on disk (st_blocks isn't available on Windows)."""
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


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
                    if is_real_dir(entry):
                        stack.append(entry.path)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        continue  # junction: don't follow, don't count
                    # Hardlinked files (pnpm store, etc.) count once. On Windows
                    # scandir doesn't report link counts, so this is Linux/macOS only.
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen:
                            continue
                        seen.add(key)
                    total += file_bytes(st)
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


def resolve(cmd):
    """Full path for cmd[0] so Windows can launch npm.cmd, yarn.cmd, etc."""
    exe = shutil.which(cmd[0])
    return [exe, *cmd[1:]] if exe else list(cmd)


def run_full(cmd, timeout=15):
    """subprocess.run with text output that never fails on odd encodings."""
    return subprocess.run(resolve(cmd), capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def run(cmd, timeout=15):
    """Run a command, return stdout ('' on any failure)."""
    try:
        return run_full(cmd, timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def have(binary):
    return shutil.which(binary) is not None


def current_user():
    try:
        return getpass.getuser().lower()
    except Exception:  # no USER/USERNAME and no pwd entry
        return ""


def is_mine(username):
    """Does a process owner string (e.g. 'alice' or 'LAPTOP\\alice') mean us?"""
    if not username:
        return False
    return username.lower().rsplit("\\", 1)[-1] == current_user()


def _force_remove(func, path, _exc):
    # Windows refuses to delete read-only files (e.g. git objects): clear the flag, retry.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)
    return not os.path.exists(path)
