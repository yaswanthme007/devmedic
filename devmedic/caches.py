"""Global tool caches (pip, npm, cargo, …) plus system things like journald/apt/docker."""
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from rich.table import Table

from .util import (HOME, WINDOWS, confirm, console, dir_size, have, human_size,
                   is_real_dir, remove_tree, run, run_full, short_path, size_style)

h = lambda *p: os.path.join(HOME, *p)
LOCAL = os.environ.get("LOCALAPPDATA") or h("AppData", "Local")
ROAMING = os.environ.get("APPDATA") or h("AppData", "Roaming")
lo = lambda *p: os.path.join(LOCAL, *p)
ro = lambda *p: os.path.join(ROAMING, *p)

# key, label, path, clean command (None -> delete dir contents), note
WINDOWS_CACHES = [
    ("pip",      "pip cache",            lo("pip", "Cache"),          ["pip", "cache", "purge"], ""),
    ("uv",       "uv cache",             lo("uv", "cache"),           ["uv", "cache", "clean"], ""),
    ("npm",      "npm cache",            lo("npm-cache", "_cacache"), ["npm", "cache", "clean", "--force"], ""),
    ("yarn",     "yarn cache",           lo("Yarn", "Cache"),         ["yarn", "cache", "clean"], ""),
    ("pnpm",     "pnpm store",           lo("pnpm", "store"),         ["pnpm", "store", "prune"], "prunes unused only"),
    ("cargo",    "cargo registry",       h(".cargo", "registry"),     None, ""),
    ("go",       "Go module cache",      h("go", "pkg", "mod"),       ["go", "clean", "-modcache"], ""),
    ("gobuild",  "Go build cache",       lo("go-build"),              ["go", "clean", "-cache"], ""),
    ("gradle",   "Gradle caches",        h(".gradle", "caches"),      None, ""),
    ("maven",    "Maven repo",           h(".m2", "repository"),      None, ""),
    ("nuget",    "NuGet packages",       h(".nuget", "packages"),     ["dotnet", "nuget", "locals", "all", "--clear"], ""),
    ("hf",       "HuggingFace models",   h(".cache", "huggingface"),  None, "re-download is big!"),
    ("temp",     "Temp files",           os.environ.get("TEMP") or lo("Temp"), None, "files in use are skipped"),
    ("vscode",   "VS Code cache",        ro("Code", "Cache"),         None, "close VS Code first"),
    ("chrome",   "Chrome cache",         lo("Google", "Chrome", "User Data", "Default", "Cache"), None, "close Chrome first"),
    ("edge",     "Edge cache",           lo("Microsoft", "Edge", "User Data", "Default", "Cache"), None, "close Edge first"),
]
LINUX_CACHES = [
    ("pip",      "pip cache",            h(".cache", "pip"),          ["pip", "cache", "purge"], ""),
    ("uv",       "uv cache",             h(".cache", "uv"),           ["uv", "cache", "clean"], ""),
    ("npm",      "npm cache",            h(".npm", "_cacache"),       ["npm", "cache", "clean", "--force"], ""),
    ("yarn",     "yarn cache",           h(".cache", "yarn"),         ["yarn", "cache", "clean"], ""),
    ("pnpm",     "pnpm store",           h(".local", "share", "pnpm", "store"), ["pnpm", "store", "prune"], "prunes unused only"),
    ("cargo",    "cargo registry",       h(".cargo", "registry"),     None, ""),
    ("go",       "Go module cache",      h("go", "pkg", "mod"),       ["go", "clean", "-modcache"], ""),
    ("gobuild",  "Go build cache",       h(".cache", "go-build"),     ["go", "clean", "-cache"], ""),
    ("gradle",   "Gradle caches",        h(".gradle", "caches"),      None, ""),
    ("maven",    "Maven repo",           h(".m2", "repository"),      None, ""),
    ("hf",       "HuggingFace models",   h(".cache", "huggingface"),  None, "re-download is big!"),
    ("thumbs",   "Thumbnails",           h(".cache", "thumbnails"),   None, ""),
    ("trash",    "Trash",                h(".local", "share", "Trash"), None, "permanent!"),
    ("vscode",   "VS Code cache",        h(".config", "Code", "Cache"), None, "close VS Code first"),
    ("chrome",   "Chrome cache",         h(".cache", "google-chrome"), None, "close Chrome first"),
    ("firefox",  "Firefox cache",        h(".cache", "mozilla"),      None, "close Firefox first"),
]
CACHES = WINDOWS_CACHES if WINDOWS else LINUX_CACHES


def recycle_bin_usage():
    """Bytes in the Windows Recycle Bin (all drives), or None."""
    if not WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                    ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    return info.i64Size or None


def journal_usage():
    out = run(["journalctl", "--disk-usage"])
    m = re.search(r"take up ([\d.]+)([KMGT])", out)
    if not m:
        return None
    mult = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2)]
    return int(float(m.group(1)) * mult)


def apt_usage():
    path = "/var/cache/apt/archives"
    return dir_size(path) if os.path.isdir(path) else None


def snap_disabled():
    """Old disabled snap revisions: list of (name, rev, bytes)."""
    out = run(["snap", "list", "--all"]) if have("snap") else ""
    res = []
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 3 and "disabled" in line:
            snap_file = f"/var/lib/snapd/snaps/{cols[0]}_{cols[2]}.snap"
            try:
                size = os.stat(snap_file).st_blocks * 512
            except OSError:
                size = 0
            res.append((cols[0], cols[2], size))
    return res


def docker_usage():
    """Reclaimable bytes from `docker system df`, or None if unavailable."""
    if not have("docker"):
        return None
    out = run(["docker", "system", "df", "--format", "{{.Reclaimable}}"], timeout=20)
    if not out.strip():
        return None
    total = 0
    for line in out.splitlines():
        m = re.match(r"([\d.]+)\s*([kKMGT]?B)", line.strip())
        if m:
            mult = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2,
                    "GB": 1000**3, "TB": 1000**4}[m.group(2)]
            total += int(float(m.group(1)) * mult)
    return total


def collect():
    present = [c for c in CACHES if os.path.isdir(c[2])]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(dir_size, [c[2] for c in present]))
        j, a, d = pool.submit(journal_usage), pool.submit(apt_usage), pool.submit(docker_usage)
        snaps = pool.submit(snap_disabled)
        rbin = pool.submit(recycle_bin_usage)
    user = [{"key": c[0], "label": c[1], "path": c[2], "cmd": c[3], "note": c[4],
             "size": s} for c, s in zip(present, sizes) if s > 0]
    user.sort(key=lambda r: r["size"], reverse=True)
    system = []
    if j.result() and j.result() > 250 * 1024**2:
        system.append({"key": "journal", "label": "systemd journal logs", "size": j.result(),
                       "fix": "sudo journalctl --vacuum-size=200M"})
    if a.result():
        system.append({"key": "apt", "label": "apt package cache", "size": a.result(),
                       "fix": "sudo apt clean && sudo apt autoremove --purge"})
    if rbin.result():
        system.append({"key": "recyclebin", "label": "Recycle Bin", "size": rbin.result(),
                       "fix": "Clear-RecycleBin -Force   (PowerShell, permanent!)"})
    if d.result():
        system.append({"key": "docker", "label": "Docker (reclaimable)", "size": d.result(),
                       "fix": "docker system prune  (add -a --volumes for everything)"})
    if snaps.result():
        cmds = "; ".join(f"sudo snap remove {n} --revision={r}" for n, r, _ in snaps.result())
        system.append({"key": "snap", "label": f"Old snap revisions ({len(snaps.result())})",
                       "size": sum(sz for *_, sz in snaps.result()) or None, "fix": cmds})
    return user, system


def show(user, system):
    t = Table(title="Caches you can clean", title_justify="left")
    t.add_column("Key", style="bold cyan")
    t.add_column("Size", justify="right")
    t.add_column("What")
    t.add_column("Path", style="dim")
    t.add_column("Note", style="yellow")
    for r in user:
        t.add_row(r["key"], f"[{size_style(r['size'])}]{human_size(r['size'])}[/]",
                  r["label"], short_path(r["path"]), r["note"])
    console.print(t)
    total = sum(r["size"] for r in user)
    console.print(f"[bold]{human_size(total)}[/] in user caches. "
                  f"Clean with: [bold]devmedic caches --clean pip npm …[/] or [bold]--clean all[/]")
    if system:
        needs = "Administrator / PowerShell" if WINDOWS else "sudo / docker"
        s = Table(title=f"System (needs {needs})", title_justify="left")
        s.add_column("Size", justify="right")
        s.add_column("What")
        s.add_column("Fix command", style="bold", overflow="fold")
        for r in system:
            size = f"[{size_style(r['size'])}]{human_size(r['size'])}[/]" if r["size"] else "—"
            s.add_row(size, r["label"], r["fix"])
        console.print(s)
        console.print("[dim]devmedic never runs system-wide commands itself — "
                      "copy the command if you want it.[/]")


def clean(user, keys, assume_yes=False):
    by_key = {r["key"]: r for r in user}
    if "all" in keys:
        # 'all' deliberately excludes the ones with a warning note.
        chosen = [r for r in user if not r["note"]]
    else:
        unknown = [k for k in keys if k not in by_key]
        if unknown:
            console.print(f"[red]Unknown / empty cache(s): {', '.join(unknown)}[/]. "
                          f"Available: {', '.join(by_key) or 'none'}")
        chosen = [by_key[k] for k in keys if k in by_key]
    if not chosen:
        return
    total = sum(r["size"] for r in chosen)
    for r in chosen:
        console.print(f"  • {r['label']:<22} {human_size(r['size']):>10}"
                      + (f"  [yellow]({r['note']})[/]" if r["note"] else ""))
    if not confirm(f"Clean these ({human_size(total)})?", assume_yes):
        return
    for r in chosen:
        ok = False
        if r["cmd"] and have(r["cmd"][0]):
            try:
                ok = run_full(r["cmd"], timeout=300).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        if not ok:
            # Try every entry (a list, not a generator: one locked file must not
            # stop the rest from being cleaned).
            try:
                entries = list(os.scandir(r["path"]))
            except OSError:
                entries = []
            ok = all([_remove_entry(e) for e in entries])
        after = dir_size(r["path"]) if os.path.isdir(r["path"]) else 0
        mark = "[green]✔[/]" if ok else "[yellow]~[/]"
        console.print(f"{mark} {r['label']}: freed {human_size(max(r['size'] - after, 0))}")


def _remove_entry(entry):
    if is_real_dir(entry):
        return remove_tree(entry.path)
    try:
        if entry.is_dir(follow_symlinks=False):
            os.rmdir(entry.path)  # Windows junction: remove the link, never the target
        else:
            os.unlink(entry.path)
        return True
    except OSError:
        return False
