"""Tests for devmedic. Run with: python3 -m unittest discover -v"""
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

from devmedic import caches, cli, junk, menu, ports, repos, util

WINDOWS = sys.platform == "win32"


def rel(path, root):
    """Relative path with forward slashes, so expectations work on every OS."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def write(path, size=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))


class UtilTests(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(util.human_size(0), "0 B")
        self.assertEqual(util.human_size(2048), "2 KB")
        self.assertEqual(util.human_size(5 * 1024**3), "5.0 GB")

    def test_parse_selection(self):
        self.assertEqual(junk.parse_selection("1,3,5-7", 10), [0, 2, 4, 5, 6])
        self.assertEqual(junk.parse_selection("all", 3), [0, 1, 2])
        self.assertEqual(junk.parse_selection("", 3), [])
        self.assertEqual(junk.parse_selection("0,99", 3), [])  # out of range ignored
        with self.assertRaises(ValueError):
            junk.parse_selection("abc", 3)

    @unittest.skipIf(WINDOWS, "scandir doesn't report hardlink counts on Windows")
    def test_dir_size_counts_hardlinks_once(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "a"), 100_000)
            os.link(os.path.join(d, "a"), os.path.join(d, "b"))
            single = util.dir_size(d)
            self.assertGreaterEqual(single, 100_000)
            self.assertLess(single, 200_000)


class JunkTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        r = self.root
        write(os.path.join(r, "web", "package.json"))
        write(os.path.join(r, "web", "node_modules", "x", "big"), 50_000)
        write(os.path.join(r, "web", "node_modules", "x", "node_modules", "y", "f"))
        write(os.path.join(r, "rust", "Cargo.toml"))
        write(os.path.join(r, "rust", "target", "debug", "bin"))
        write(os.path.join(r, "notes", "target", "important.txt"))  # not Rust
        write(os.path.join(r, "py", "venv", "file"))                  # not a real venv
        write(os.path.join(r, "py2", ".venv", "pyvenv.cfg"))          # real venv

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def found(self):
        return sorted(rel(p, self.root) for p, _ in junk.find(self.root))

    def test_detects_only_real_junk(self):
        self.assertEqual(self.found(), ["py2/.venv", "rust/target", "web/node_modules"])

    def test_clean_deletes_only_selected(self):
        items = junk.scan(self.root)
        target = [i for i in items if rel(i["path"], self.root) == "rust/target"]
        self.assertEqual(len(target), 1)
        with redirect_stdout(io.StringIO()):
            junk.clean(target, assume_yes=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, "rust", "target")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "rust", "Cargo.toml")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "web", "node_modules")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "notes", "target")))

    def test_older_filter(self):
        old = os.path.join(self.root, "web", "package.json")
        past = time.time() - 90 * 86400
        os.utime(old, (past, past))
        paths = [rel(i["path"], self.root) for i in junk.scan(self.root, 30)]
        self.assertEqual(paths, ["web/node_modules"])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class RepoTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd or self.root, env=self.env, check=True,
                       capture_output=True)

    def make_repo(self, name):
        path = os.path.join(self.root, name)
        self.git("init", "-q", name)
        write(os.path.join(path, "a"))
        self.git("add", ".", cwd=path)
        self.git("commit", "-qm", "init", cwd=path)
        return path

    def test_states(self):
        remote = os.path.join(self.root, "remote.git")
        self.git("init", "-q", "--bare", remote)
        clean = self.make_repo("clean")
        self.git("remote", "add", "origin", remote, cwd=clean)
        self.git("push", "-qu", "origin", "HEAD", cwd=clean)

        dirty = os.path.join(self.root, "dirty")
        self.git("clone", "-q", remote, dirty)
        write(os.path.join(dirty, "new"))

        ahead = os.path.join(self.root, "ahead")
        self.git("clone", "-q", remote, ahead)
        write(os.path.join(ahead, "b"))
        self.git("add", ".", cwd=ahead)
        self.git("commit", "-qm", "b", cwd=ahead)

        self.make_repo("local")

        info = {os.path.basename(i["path"]): i for i in map(repos.inspect, repos.find(self.root))}
        self.assertFalse(repos.needs_attention(info["clean"]))
        self.assertEqual(info["dirty"]["untracked"], 1)
        self.assertEqual(info["ahead"]["ahead"], 1)
        self.assertEqual(info["ahead"]["unpushed_branches"], 0)  # current branch not double counted
        self.assertTrue(info["local"]["no_remote"])


class PortTests(unittest.TestCase):
    def test_find_and_kill_listener(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        proc = subprocess.Popen([sys.executable, "-c",
                                 "import socket,time;s=socket.socket();"
                                 f"s.bind(('127.0.0.1',{port}));s.listen();time.sleep(60)"])
        try:
            for _ in range(50):
                if any(r["port"] == port for r in ports.listening()):
                    break
                time.sleep(0.1)
            row = next(r for r in ports.listening() if r["port"] == port)
            self.assertEqual(row["pid"], proc.pid)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ports.kill(port, assume_yes=True), 0)
            self.assertIsNotNone(proc.wait(timeout=5))
        finally:
            if proc.poll() is None:
                proc.kill()


class CacheTests(unittest.TestCase):
    def test_clean_all_skips_risky(self):
        user = [
            {"key": "pip", "label": "pip", "path": tempfile.mkdtemp(), "cmd": None,
             "note": "", "size": 10},
            {"key": "trash", "label": "Trash", "path": tempfile.mkdtemp(), "cmd": None,
             "note": "permanent!", "size": 10},
        ]
        for u in user:
            write(os.path.join(u["path"], "f"))
        with redirect_stdout(io.StringIO()):
            caches.clean(user, ["all"], assume_yes=True)
        self.assertEqual(os.listdir(user[0]["path"]), [])
        self.assertEqual(os.listdir(user[1]["path"]), ["f"])
        for u in user:
            shutil.rmtree(u["path"])


class CrossPlatformTests(unittest.TestCase):
    def test_is_mine(self):
        me = util.current_user()
        self.assertTrue(util.is_mine(me))
        self.assertTrue(util.is_mine("SOME-PC\\" + me.upper()))  # Windows DOMAIN\user
        self.assertFalse(util.is_mine("definitely-not-" + me))
        self.assertFalse(util.is_mine(None))

    def test_windows_key_decoding(self):
        d = menu.decode_windows_key
        self.assertEqual(d("\xe0", "H"), "up")
        self.assertEqual(d("\x00", "P"), "down")
        self.assertEqual(d("\r"), "enter")
        self.assertEqual(d("q"), "quit")
        self.assertEqual(d("7"), "7")
        self.assertEqual(d("\xe0", "Z"), "other")
        with self.assertRaises(KeyboardInterrupt):
            d("\x03")

    def test_utf8_output_survives_legacy_encoding(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        old = sys.stdout
        try:
            sys.stdout = buf
            cli.utf8_output()
            print("🩺 ████ ╭─╮")  # would raise UnicodeEncodeError in cp1252
            buf.flush()
        finally:
            sys.stdout = old
        self.assertIn("🩺".encode("utf-8"), buf.buffer.getvalue())


@unittest.skipUnless(WINDOWS, "Windows junctions")
class JunctionTests(unittest.TestCase):
    """pnpm and Windows use junctions; we must never follow or delete through them."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = os.path.join(self.root, "real")
        write(os.path.join(self.target, "node_modules", "pkg", "big"), 200_000)
        write(os.path.join(self.target, "package.json"))
        self.link = os.path.join(self.root, "link")
        subprocess.run(["cmd", "/c", "mklink", "/J", self.link, self.target], check=True,
                       capture_output=True)

    def tearDown(self):
        subprocess.run(["cmd", "/c", "rmdir", self.link], capture_output=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_scan_does_not_follow_junction(self):
        found = sorted(rel(p, self.root) for p, _ in junk.find(self.root))
        self.assertEqual(found, ["real/node_modules"])

    def test_size_ignores_junction(self):
        with_link = util.dir_size(self.root)
        self.assertLess(with_link, 2 * util.dir_size(self.target))

    def test_removing_junction_keeps_target(self):
        with os.scandir(self.root) as it:
            entry = next(e for e in it if e.name == "link")
        self.assertTrue(caches._remove_entry(entry))
        self.assertFalse(os.path.exists(self.link))
        self.assertTrue(os.path.exists(os.path.join(self.target, "node_modules", "pkg", "big")))


class SmokeTests(unittest.TestCase):
    """Every read-only command should run without crashing."""

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_read_only_commands(self):
        tmp = tempfile.mkdtemp()
        try:
            for args in (["all"], ["system"], ["memory"], ["storage"], ["network"],
                         ["tools"], ["projects"], ["cleanup"], ["notes"], ["ports", "-a"],
                         ["procs", "-v"], ["caches"], ["junk", tmp], ["repos", tmp]):
                with self.subTest(args=args):
                    code, _ = self.run_cli(*args)
                    self.assertEqual(code, 0)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
