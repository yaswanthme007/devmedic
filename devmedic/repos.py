"""Find git repos with work that isn't safely pushed."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .junk import PATTERNS, SKIP_AT_TOP, SKIP_DIRS
from .util import HOME, console, display_path, human_age, short_path


def find(root, max_depth=6):
    root = os.path.abspath(root)
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if os.path.exists(os.path.join(current, ".git")):
            yield current
            # keep going: nested repos / submodules-as-dirs are common
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        at_top = current in (HOME, "/")
        for e in entries:
            if (depth < max_depth and e.is_dir(follow_symlinks=False)
                    and e.name not in SKIP_DIRS and e.name not in PATTERNS
                    and not (at_top and (e.name.startswith(".") or e.name in SKIP_AT_TOP))):
                stack.append((e.path, depth + 1))


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                           timeout=15)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def inspect(repo):
    info = {"path": repo, "branch": "?", "ahead": 0, "behind": 0, "upstream": False,
            "changed": 0, "untracked": 0, "stashes": 0, "last": 0, "no_remote": False,
            "unpushed_branches": 0}
    for line in git(repo, "status", "--porcelain=v2", "--branch").splitlines():
        if line.startswith("# branch.head "):
            info["branch"] = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            info["upstream"] = True
        elif line.startswith("# branch.ab "):
            a, b = line.split()[2:4]
            info["ahead"], info["behind"] = int(a), -int(b)
        elif line.startswith("?"):
            info["untracked"] += 1
        elif line[:1] in ("1", "2", "u"):
            info["changed"] += 1
    info["stashes"] = len(git(repo, "stash", "list").splitlines())
    last = git(repo, "log", "-1", "--format=%ct").strip()
    info["last"] = int(last) if last.isdigit() else 0
    info["no_remote"] = not git(repo, "remote").strip()
    # Local branches with commits not on any remote.
    if not info["no_remote"]:
        out = git(repo, "for-each-ref", "--format=%(refname:short)|%(upstream:track)",
                  "refs/heads")
        for line in out.splitlines():
            name, _, track = line.partition("|")
            if name == info["branch"]:
                continue  # already covered by ahead/upstream
            if "ahead" in track or (not track and
                                    git(repo, "log", "--oneline", "-1", name, "--not",
                                        "--remotes").strip()):
                info["unpushed_branches"] += 1
    return info


def needs_attention(i):
    return (i["changed"] or i["untracked"] or i["ahead"] or i["stashes"]
            or i["unpushed_branches"] or i["no_remote"]
            or (i["upstream"] is False and i["branch"] != "(detached)" and i["last"]))


def scan(root):
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  console=console, transient=True) as prog:
        task = prog.add_task(f"Looking for git repos in {short_path(os.path.abspath(root))} …")
        repos = list(find(root))
        prog.update(task, description=f"Inspecting {len(repos)} repos …")
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(inspect, repos))
    return sorted(infos, key=lambda i: i["last"], reverse=True)


def show(infos, show_all=False, root=None):
    if not infos:
        console.print("[dim]No git repositories found.[/]")
        return
    flagged = [i for i in infos if needs_attention(i)]
    rows = infos if show_all else flagged
    if not rows:
        console.print(f"[green]All {len(infos)} repos are clean and pushed. ✨[/]")
        return
    t = Table(title=f"Git repos — {len(flagged)} of {len(infos)} need attention",
              title_justify="left")
    t.add_column("Repo", overflow="fold")
    t.add_column("Branch", style="cyan")
    t.add_column("Problems")
    t.add_column("Last commit", justify="right")
    for i in rows:
        p = []
        if i["changed"]:
            p.append(f"[yellow]{i['changed']} modified[/]")
        if i["untracked"]:
            p.append(f"[yellow]{i['untracked']} untracked[/]")
        if i["ahead"]:
            p.append(f"[red]{i['ahead']} unpushed[/]")
        if i["behind"]:
            p.append(f"[blue]{i['behind']} behind[/]")
        if i["stashes"]:
            p.append(f"[magenta]{i['stashes']} stash[/]")
        if i["unpushed_branches"]:
            p.append(f"[red]{i['unpushed_branches']} other branch(es) unpushed[/]")
        if i["no_remote"]:
            p.append("[red]no remote (not backed up!)[/]")
        elif not i["upstream"] and i["last"]:
            p.append("[yellow]branch has no upstream[/]")
        t.add_row(display_path(i["path"], root), i["branch"], ", ".join(p) or "[green]clean[/]",
                  human_age(i["last"]) if i["last"] else "no commits")
    console.print(t)
