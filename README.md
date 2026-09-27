# 🩺 devmedic

**A health check for your developer machine.** Find what's hogging your ports, RAM and disk,
spot git work that isn't backed up, and clean up safely — all from one friendly terminal UI.
Works **100% offline**. No accounts, no telemetry, nothing leaves your machine.

```
     _                                    _  _
  __| |  ___ __   __ _ __ ___    ___   __| |(_)  ___
 / _` | / _ \\ \ / /| '_ ` _ \  / _ \ / _` || | / __|
| (_| ||  __/ \ V / | | | | | ||  __/| (_| || || (__
 \__,_| \___|  \_/  |_| |_| |_| \___| \__,_||_| \___|      HEALTH 92/100  A
```

## Why?

Every developer hits these, over and over:

- 😤 `Error: listen EADDRINUSE: address already in use :::3000`
- 💾 Disk full — from 40 forgotten `node_modules`, `.venv`s and `target/` folders
- 🐢 Machine slow — three dev servers you started last week are still running
- 😱 "Did I ever push that project?"

devmedic answers all of them in seconds.

## Install

Works on **Linux** (built on Ubuntu) and **Windows 10/11**, with **Python 3.9+**.

**With pipx (recommended)**

Linux:

```bash
sudo apt install pipx && pipx ensurepath        # once
pipx install devmedic
```

Windows (PowerShell or Windows Terminal):

```powershell
py -m pip install --user pipx; py -m pipx ensurepath   # once, then open a new terminal
pipx install devmedic
```

**With pip**

```bash
pip install devmedic        # inside a virtualenv, or: pip install --user devmedic
```

**From a clone**

```bash
git clone https://github.com/yaswanthme007/devmedic.git
cd devmedic
./install.sh          # uses pipx if present, otherwise a dev-mode link in ~/.local/bin
```

Dev mode needs `sudo apt install python3-psutil python3-rich` (already present on most Ubuntu
desktops).

## Usage

```bash
devmedic                 # interactive menu — pick a section with ↑↓ / 1-8 / Enter
devmedic all             # everything on one screen (also used when output is piped)
devmedic watch           # live full-screen monitor: CPU/RAM graphs, net speed, ports
```

**Sections** (open from the menu, or directly):

| Command              | Shows                                                         |
|----------------------|---------------------------------------------------------------|
| `devmedic system`   | CPU model, per-core usage, load, temperature, battery         |
| `devmedic memory`   | RAM/swap and the biggest apps (browser tabs grouped together) |
| `devmedic storage`  | Usage of every real partition                                 |
| `devmedic network`  | IPs, Wi-Fi, traffic, listening ports                          |
| `devmedic tools`    | Installed versions of git, python, node, go, rust, docker …   |
| `devmedic projects` | Dev processes by project + every git repo's status            |
| `devmedic cleanup`  | How much space caches and junk folders are wasting            |
| `devmedic notes`    | The doctor's recommendations, with exact commands             |

**Fix things:**

```bash
devmedic ports [-a]                 # what's listening, and from which project folder
devmedic kill 3000                  # free port 3000 (SIGTERM, then SIGKILL if needed)
devmedic junk [PATH]                # node_modules, .venv, target/, .next … with sizes
devmedic junk --older 30 --clean    # pick stale ones to delete: 1,3,5-8 / all
devmedic caches                     # pip npm yarn pnpm cargo go gradle maven … + system
devmedic caches --clean pip npm     # or --clean all (skips risky ones like Trash)
devmedic repos [PATH]               # uncommitted, unpushed, stashed, or no remote at all
devmedic procs [-v]                 # dev processes grouped by project, RAM/CPU/ports
```

## Safety

devmedic is careful by design:

- **Nothing is deleted without confirmation** (unless you pass `-y`).
- **Conservative junk detection:** `target/` only next to `Cargo.toml`/`pom.xml`, venvs only if
  they contain `pyvenv.cfg`. Generic `build/` and `dist/` folders are **never** touched.
- **Links are never followed:** symlinks and Windows junctions (used by pnpm) are skipped when
  scanning and removed as links, so nothing outside the folder is ever deleted.
- **Never runs `sudo` or Administrator commands.** System fixes (journal, apt, snap, Recycle Bin,
  docker) are printed for you to copy.
- **Only kills what you confirm**, and can't touch other users' processes.
- **Offline and private:** no network requests, no data collection.

## Development

```bash
./install.sh --dev                        # edits take effect immediately
python3 -m unittest discover -s tests -v  # run the tests
```

Ideas and pull requests are welcome! Please open an issue first for big changes.

## License

[MIT](LICENSE)
