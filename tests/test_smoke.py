"""Smoke tests: build a real app in a temporary folder and actually start it.

The tests that matter most are not "does this endpoint return 200". They are:

* a fake nano-style app is written to a temporary folder, discovered, started
  as a real subprocess on a port nanoDesk chose, fetched over HTTP, and then
  stopped — which is the whole promise of the app in one test;
* a folder full of junk produces nothing at all, however strange the files in
  it are;
* a folder nanoDesk did not discover cannot be started, even by someone who
  knows exactly what to send to the address that starts things.

The app keeps its settings in the user's home folder. For tests it must not, so
the folder is redirected before anything is imported.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the app at a throwaway folder before it is imported.
_TEMPORARY_HOME = tempfile.mkdtemp(prefix="nanodesk-tests-")
os.environ["NANODESK_HOME"] = _TEMPORARY_HOME

from nanodesk import apps, httpbase, machine, runner, server, store  # noqa: E402
from nanodesk.httpbase import free_port  # noqa: E402


# --------------------------------------------------------------------------
# A pretend member of the family, built file by file
# --------------------------------------------------------------------------
FAKE_START = '''#!/usr/bin/env python3
"""A pretend app, used by the tests. It is a real HTTP server and nothing else."""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MIN_PYTHON = (3, 9)


def main():
    port = 8999
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body><h1>fake app</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    print("fake app listening on %d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


main()
'''

FAKE_PACKAGE_INIT = '"""The pretend package inside the pretend app."""\n'

FAKE_PACKAGE_MAIN = '''"""Start the pretend app."""

from . import hello


def main():
    return hello()


if __name__ == "__main__":
    main()
'''

FAKE_README = """<div align="center">

# FakeApp

**A pretend app that exists so nanoDesk can be tested properly.**

It is a real HTTP server, it is written by the test suite, and it prints one
line when it starts. Nothing here is a mock: the process is real.

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

## More

Anything else in this README must never end up on a card.
"""

QUIET_START = '''"""A pretend app that exits immediately, the way a broken one would."""

import sys

print("I need a model file before I can run.", flush=True)
print("Nothing to do, giving up.", flush=True)
sys.exit(3)
'''


def build_fake_app(folder, name="fakeapp", readme=FAKE_README, start=FAKE_START, manifest=None):
    """Write an app shaped like the rest of the family into `folder`."""
    path = folder / name
    package = path / name.replace("-", "_").lower()
    package.mkdir(parents=True, exist_ok=True)
    (path / "start.py").write_text(start, encoding="utf-8")
    (package / "__init__.py").write_text(FAKE_PACKAGE_INIT, encoding="utf-8")
    (package / "__main__.py").write_text(FAKE_PACKAGE_MAIN, encoding="utf-8")
    (package / "hello.py").write_text('def hello():\n    return "hello"\n', encoding="utf-8")
    if readme is not None:
        (path / "README.md").write_text(readme, encoding="utf-8")
    if manifest is not None:
        (path / ".nano.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def build_junk(folder):
    """A folder of the sort of thing that is actually in somebody's Downloads."""
    junk = folder / "junk"
    junk.mkdir(parents=True, exist_ok=True)
    (junk / "notes.txt").write_text("not an app\n", encoding="utf-8")
    (junk / "holiday.JPG").write_bytes(b"\xff\xd8\xff\xe0 not really a photo")
    (junk / "README.md").write_bytes(b"\xff\xfe\x00bad bytes, not utf-8 \xc3\x28")
    (junk / "empty-folder").mkdir(exist_ok=True)
    half = junk / "half-an-app"
    half.mkdir(exist_ok=True)
    (half / "start.py").write_text("print('no package here')\n", encoding="utf-8")
    package_only = junk / "package-only"
    (package_only / "mypackage").mkdir(parents=True, exist_ok=True)
    (package_only / "mypackage" / "__init__.py").write_text("", encoding="utf-8")
    deep = junk / "apps" / "somewhere" / "deeper"
    deep.mkdir(parents=True, exist_ok=True)
    (deep / "start.py").write_text("print('too deep to be found')\n", encoding="utf-8")
    return junk


def fetch(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read()


# --------------------------------------------------------------------------
# Reading a README
# --------------------------------------------------------------------------
class TestMarkdown(unittest.TestCase):
    def test_bold_comes_out_plain(self):
        self.assertEqual(apps.strip_markdown("**Tidy this up.**"), "Tidy this up.")

    def test_code_comes_out_plain(self):
        self.assertEqual(apps.strip_markdown("Run `start.py` now"), "Run start.py now")

    def test_links_keep_their_words(self):
        self.assertEqual(apps.strip_markdown("See [the manual](https://example.com/x) first"),
                         "See the manual first")

    def test_badges_and_images_disappear(self):
        self.assertEqual(apps.strip_markdown("[![build](https://img.shields.io/badge/x-y.svg)](LICENSE)"), "")

    def test_html_tags_disappear(self):
        self.assertEqual(apps.strip_markdown('<div align="center">hello</div>'), "hello")

    def test_blurb_prefers_the_bolded_sentence(self):
        self.assertEqual(apps.blurb_from_readme(FAKE_README),
                         "A pretend app that exists so nanoDesk can be tested properly.")

    def test_blurb_falls_back_to_the_first_paragraph(self):
        text = "# Something\n\nIt does one job well. Then more words follow.\n\n## Later\n\nNo.\n"
        self.assertEqual(apps.blurb_from_readme(text), "It does one job well. Then more words follow.")

    def test_blurb_is_kept_short(self):
        text = "# Long\n\n**%s**\n" % ("word " * 80)
        self.assertLessEqual(len(apps.blurb_from_readme(text)), apps.MAX_BLURB + 1)
        self.assertTrue(apps.blurb_from_readme(text).endswith("…"))

    def test_heading_becomes_the_name(self):
        text = "<div>\n\n# nanoThing — a thing for testing\n\n**Blurb here.**\n"
        self.assertEqual(apps._heading(text), "nanoThing — a thing for testing")
        self.assertEqual(apps._tidy_name(apps._heading(text)), "nanoThing")


# --------------------------------------------------------------------------
# Finding apps
# --------------------------------------------------------------------------
class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nanodesk-discovery-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)

    def test_a_fake_app_is_found(self):
        build_fake_app(self.root)
        found = apps.find_apps([str(self.root)])
        self.assertEqual([item["name"] for item in found], ["FakeApp"])
        self.assertEqual(found[0]["kind"], "nano app")
        self.assertEqual(found[0]["emoji"], "🧩")
        self.assertIsNone(found[0]["port"])
        self.assertFalse(found[0]["has_manifest"])
        self.assertEqual(found[0]["problems"], [])

    def test_an_app_in_a_folder_you_added_is_found(self):
        # This is the shape people actually have: one folder holding several
        # apps, so they add that folder rather than each app one at a time.
        build_fake_app(self.root / "apps")
        found = apps.find_apps([str(self.root / "apps")])
        self.assertEqual([item["name"] for item in found], ["FakeApp"])

    def test_junk_produces_nothing_and_does_not_raise(self):
        build_junk(self.root)
        self.assertEqual(apps.find_apps([str(self.root)]), [])

    def test_undecodable_readme_is_survived(self):
        path = build_fake_app(self.root, readme=None)
        (path / "README.md").write_bytes(b"\xff\xfe\x00\x00 broken \xc3\x28")
        found = apps.find_apps([str(self.root)])
        self.assertEqual(len(found), 1)
        self.assertIsInstance(found[0]["blurb"], str)
        self.assertTrue(found[0]["blurb"])

    def test_an_app_with_no_readme_says_so_plainly(self):
        build_fake_app(self.root, name="quietapp", readme=None)
        found = apps.find_apps([str(self.root)])
        self.assertEqual(found[0]["name"], "quietapp")
        self.assertEqual(found[0]["blurb"], "An app in quietapp.")

    def test_manifest_overrides_name_emoji_blurb_and_port(self):
        build_fake_app(self.root, manifest={
            "name": "My Own Name",
            "emoji": "🎈",
            "blurb": "Said by the manifest, not the README.",
            "port": 9123,
        })
        found = apps.find_apps([str(self.root)])
        self.assertEqual(found[0]["name"], "My Own Name")
        self.assertEqual(found[0]["emoji"], "🎈")
        self.assertEqual(found[0]["blurb"], "Said by the manifest, not the README.")
        self.assertEqual(found[0]["port"], 9123)
        self.assertTrue(found[0]["has_manifest"])

    def test_a_manifest_alone_is_enough_to_be_an_app(self):
        folder = self.root / "odd-one"
        folder.mkdir()
        (folder / ".nano.json").write_text(json.dumps({"name": "Manifest Only", "port": 9200}), encoding="utf-8")
        found = apps.find_apps([str(self.root)])
        self.assertEqual([item["name"] for item in found], ["Manifest Only"])
        self.assertTrue(found[0]["problems"])
        self.assertIn("start.py", found[0]["problems"][0])

    def test_a_broken_manifest_is_reported_not_fatal(self):
        path = build_fake_app(self.root)
        (path / ".nano.json").write_text("{not json at all", encoding="utf-8")
        found = apps.find_apps([str(self.root)])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "FakeApp")
        self.assertFalse(found[0]["has_manifest"])
        self.assertIn(".nano.json", found[0]["problems"][0])

    def test_an_app_wanting_a_newer_python_says_so(self):
        start = QUIET_START.replace('import sys', 'import sys\n\nMIN_PYTHON = (3, 99)')
        build_fake_app(self.root, start=start)
        found = apps.find_apps([str(self.root)])
        self.assertTrue(any("Python 3.99" in problem for problem in found[0]["problems"]))

    def test_compiled_leftovers_without_source_are_reported(self):
        folder = self.root / "leftovers"
        folder.mkdir()
        (folder / ".nano.json").write_text(json.dumps({"name": "Leftovers"}), encoding="utf-8")
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00\x01")
        found = apps.find_apps([str(self.root)])
        self.assertTrue(any("__pycache__" in problem for problem in found[0]["problems"]))

    def test_ids_are_stable_and_path_based(self):
        path = build_fake_app(self.root)
        first = apps.find_apps([str(self.root)])[0]
        second = apps.find_apps([str(self.root)])[0]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], apps.app_id(path))
        self.assertNotEqual(first["id"], apps.app_id(str(self.root)))

    def test_find_apps_never_returns_two_cards_for_one_folder(self):
        build_fake_app(self.root / "apps")
        found = apps.find_apps([str(self.root), str(self.root / "apps")])
        self.assertEqual(len(found), 1)

    def test_an_empty_folder_list_finds_nothing(self):
        self.assertEqual(apps.find_apps([]), [])

    def test_candidates_are_one_level_deep_and_no_deeper(self):
        build_junk(self.root)
        names = [item.name for item in apps.candidate_folders([str(self.root)])]
        self.assertIn("junk", names)              # a folder you point at is looked in
        self.assertNotIn("half-an-app", names)    # …and one level inside it, no further
        self.assertNotIn("deeper", names)
        self.assertNotIn("__pycache__", names)

    def test_nanodesk_never_lists_itself(self):
        checkout = apps.family_home() / "nanodesk"
        if not checkout.is_dir():  # pragma: no cover - only when copied out of the family
            self.skipTest("this checkout does not sit beside the others")
        found = apps.find_apps([str(apps.family_home())])
        self.assertNotIn("nanodesk", [item["name"].lower() for item in found])
        self.assertNotIn(str(checkout.resolve()), [item["path"] for item in found])

    def test_a_folder_that_is_not_there_produces_nothing(self):
        found = apps.find_apps([str(self.root / "not-here" / "nor-here")])
        self.assertEqual(found, [])


# --------------------------------------------------------------------------
# Starting and stopping the real thing
# --------------------------------------------------------------------------
class TestRunner(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nanodesk-runner-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = build_fake_app(Path(self.root))
        self.app = apps.describe(self.path)
        self.started = []

    def tearDown(self):
        for app_id in list(self.started):
            runner.stop(app_id)

    def start(self, app=None):
        result = runner.start(app or self.app)
        if result.get("ok"):
            self.started.append(result["id"])
        return result

    def test_starting_a_fake_app_works_end_to_end(self):
        result = self.start()
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result["url"].startswith("http://127.0.0.1:"))
        status, body = fetch(result["url"])
        self.assertEqual(status, 200)
        self.assertIn(b"fake app", body)
        self.assertNotEqual(result["port"], 8782)  # never the desk's own port
        self.assertGreater(result["port"], 1024)

    def test_it_is_then_reported_as_running_with_its_address(self):
        result = self.start()
        up = runner.running()
        mine = [item for item in up if item["id"] == self.app["id"]]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["url"], result["url"])
        self.assertEqual(mine[0]["port"], result["port"])
        self.assertGreaterEqual(mine[0]["seconds"], 0)

    def test_stopping_it_stops_it(self):
        result = self.start()
        stopped = runner.stop(self.app["id"])
        self.assertTrue(stopped["stopped"])
        self.assertTrue(stopped["confirmed"], stopped)
        self.assertEqual([item for item in runner.running() if item["id"] == self.app["id"]], [])
        with self.assertRaises(Exception):
            fetch(result["url"], timeout=3)

    def test_starting_it_twice_reuses_the_same_address(self):
        first = self.start()
        second = self.start()
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_running"])
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(len([item for item in runner.running() if item["id"] == self.app["id"]]), 1)

    def test_it_says_what_it_printed(self):
        result = self.start()
        self.assertTrue(any("listening" in line for line in result["said"]), result["said"])
        self.assertTrue(any("start.py" in line for line in runner.log_tail(self.app["id"])))

    def test_an_app_that_dies_immediately_is_reported_with_its_output(self):
        broken = build_fake_app(Path(self.root), name="brokenapp", start=QUIET_START)
        described = apps.describe(broken)
        result = runner.start(described)
        self.assertFalse(result.get("ok"))
        self.assertIn("never answered", result["error"])
        self.assertTrue(any("model file" in line for line in result.get("said", [])))
        self.assertEqual([item for item in runner.running() if item["id"] == described["id"]], [])

    def test_an_app_we_did_not_discover_is_refused(self):
        """The security test: a path is not enough to make nanoDesk run something."""
        from pathlib import Path

        fake = describe_like_a_stranger(self.path)
        result = runner.start(fake)
        self.assertFalse(result.get("ok"))
        self.assertIn("only start apps I found myself", result["error"])
        self.assertEqual(runner.running(), [])

    def test_an_app_without_start_py_is_refused_with_a_plain_sentence(self):
        path = build_fake_app(Path(self.root), name="noscript")
        described = apps.describe(path)
        described["id"] = apps.app_id(path)  # a real description…
        (path / "start.py").unlink()  # …of a folder that no longer has a start.py
        result = runner.start(described)
        self.assertFalse(result.get("ok"))
        self.assertIn("no start.py", result["error"])

    def test_wrong_shape_and_wrong_kind_are_refused(self):
        for wrong in (None, "a string", 42, {}, {"path": str(self.path)}, {"kind": "nano app"}):
            result = runner.start(wrong)
            self.assertFalse(result.get("ok"), wrong)

    def test_stopping_something_that_is_not_running_is_kind_about_it(self):
        result = runner.stop(apps.app_id(self.path))
        self.assertFalse(result["stopped"])
        self.assertIn("not running", result["reason"].lower())

    def test_a_port_that_is_already_answering_is_skipped(self):
        """A start must never land on somebody else's port and call it a success."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Quiet(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"hi")

            def log_message(self, fmt, *args):
                pass

        busy = free_port(9340)
        squatter = ThreadingHTTPServer(("127.0.0.1", busy), Quiet)
        threading.Thread(target=squatter.serve_forever, daemon=True).start()
        self.addCleanup(squatter.shutdown)
        try:
            self.assertTrue(runner._answers("http://127.0.0.1:%d" % busy))
            chosen = runner._free_port(busy)
            self.assertNotEqual(chosen, busy)
            self.assertFalse(runner._answers("http://127.0.0.1:%d" % chosen))
        finally:
            squatter.server_close()

    def test_a_port_can_be_asked_for(self):
        wanted = free_port(9320)
        result = self.start(dict(self.app, port=wanted))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["port"], wanted)
        self.assertEqual(fetch(result["url"])[0], 200)


def describe_like_a_stranger(path):
    """A description that looks right but was not written by apps.describe."""
    return {
        "id": "not-a-real-id",
        "name": "something a website made up",
        "path": str(path),
        "kind": "nano app",
        "problems": [],
    }


# --------------------------------------------------------------------------
# The page and the addresses behind it
# --------------------------------------------------------------------------
class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = server.create_app()
        cls.port = free_port(8910)
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.thread = threading.Thread(
            target=cls.app.serve,
            kwargs={"host": "127.0.0.1", "port": cls.port, "open_browser": False, "quiet": True},
            daemon=True,
        )
        cls.thread.start()
        for _ in range(100):
            try:
                cls.get("/api/health")
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("the test server never came up")

    @classmethod
    def tearDownClass(cls):
        runner.stop_all()
        cls.app.shutdown()

    # -- helpers ----------------------------------------------------------
    @classmethod
    def get(cls, path, raw=False):
        with urllib.request.urlopen(cls.base + path, timeout=20) as response:
            body = response.read()
        return body if raw else json.loads(body.decode("utf-8"))

    @classmethod
    def post(cls, path, payload=None, raw=False):
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if raw:
                return exc.code, body.decode("utf-8")
            return json.loads(body.decode("utf-8"))
        return body.decode("utf-8") if raw else json.loads(body.decode("utf-8"))

    @classmethod
    def delete(cls, path):
        request = urllib.request.Request(cls.base + path, method="DELETE",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))

    @classmethod
    def get_status(cls, path):
        try:
            with urllib.request.urlopen(cls.base + path, timeout=20) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as exc:
            exc.read()
            return exc.code

    @classmethod
    def status_of(cls, path, payload=None):
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as exc:
            exc.read()
            return exc.code

    @classmethod
    def stream(cls, path, payload=None):
        request = urllib.request.Request(
            cls.base + path,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")


class TestServer(ServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workdir = tempfile.mkdtemp(prefix="nanodesk-server-")
        cls.folder = build_fake_app(Path(cls.workdir))
        cls.app_id = apps.app_id(cls.folder)
        cls.post("/api/folders", {"path": cls.workdir})

    @classmethod
    def tearDownClass(cls):
        try:
            runner.stop_all()
        finally:
            shutil.rmtree(cls.workdir, True)
            super(TestServer, cls).tearDownClass()

    def test_the_page_is_served(self):
        body = self.get("/", raw=True).decode("utf-8")
        self.assertIn("nanoDesk", body)
        self.assertIn("/app.js", body)

    def test_the_three_static_files_are_there(self):
        for path, needle in (("/style.css", "--accent"), ("/app.js", "textContent"), ("/", "appGrid")):
            body = self.get(path, raw=True).decode("utf-8")
            self.assertIn(needle, body, path)

    def test_state_has_everything_the_page_draws(self):
        data = self.get("/api/state")
        for key in ("apps", "running", "watched", "settings", "computer", "beside", "nonoforge_here"):
            self.assertIn(key, data)
        self.assertIsInstance(data["apps"], list)
        self.assertIn("folders", data["settings"])
        self.assertTrue(any(entry["kind"] == "beside" for entry in data["watched"]))

    def test_the_computer_panel_is_plain_language_and_quick(self):
        started = time.time()
        computer = self.get("/api/state")["computer"]
        self.assertLess(time.time() - started, 12)
        self.assertRegex(computer["python"], r"^\d+\.\d+")
        self.assertIn("Python", computer["python_sentence"])
        self.assertTrue(computer["python_sentence"].endswith("."))
        self.assertEqual(len(computer["engines"]), 4)
        self.assertEqual({engine["port"] for engine in computer["engines"]}, {11434, 1234, 8080, 8090})
        for engine in computer["engines"]:
            self.assertIn("sentence", engine)
            self.assertIsInstance(engine["running"], bool)
            self.assertTrue(engine["sentence"].endswith("."))

    def test_probing_engines_never_takes_longer_than_one_timeout(self):
        started = time.time()
        found = machine.probe_engines(timeout=0.4)
        self.assertLess(time.time() - started, 2.0)
        self.assertEqual(len(found), 4)

    def test_the_fake_app_is_on_the_list_with_its_readme_blurb(self):
        data = self.get("/api/state")
        mine = [card for card in data["apps"] if card["id"] == self.app_id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["name"], "FakeApp")
        self.assertEqual(mine[0]["blurb"],
                         "A pretend app that exists so nanoDesk can be tested properly.")
        self.assertEqual(mine[0]["problems"], [])
        self.assertIsNone(mine[0]["running"])
        self.assertTrue(mine[0]["can_start"])

    def test_adding_a_folder_that_is_not_there_is_refused_plainly(self):
        missing = os.path.join(self.workdir, "no-such-folder")
        data = self.post("/api/folders", {"path": missing})
        self.assertIn("error", data)
        self.assertIn(missing, data["error"])
        self.assertNotIn(missing, [entry["path"] for entry in self.get("/api/state")["watched"]])

    def test_adding_a_file_instead_of_a_folder_is_refused(self):
        path = os.path.join(self.workdir, "a-file.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("hello")
        data = self.post("/api/folders", {"path": path})
        self.assertIn("error", data)
        self.assertIn("file, not a folder", data["error"])

    def test_adding_nothing_at_all_is_refused(self):
        data = self.post("/api/folders", {"path": "   "})
        self.assertIn("error", data)
        self.assertIn("full path of a folder", data["error"])

    def test_a_folder_can_be_added_and_forgotten_again(self):
        second = tempfile.mkdtemp(prefix="nanodesk-second-")
        self.addCleanup(shutil.rmtree, second, True)
        added = self.post("/api/folders", {"path": second})
        self.assertTrue(added["ok"], added)
        # Windows hands out 8.3 short names (RUNNER~1); nanoDesk stores the
        # resolved path. Compare resolved on both sides.
        self.assertIn(os.path.realpath(second).lower(),
                      [os.path.realpath(entry["path"]).lower() for entry in added["watched"]])
        # Adding it twice is not an error, it just says so.
        again = self.post("/api/folders", {"path": second})
        self.assertTrue(again["already_there"])
        forgotten = self.delete("/api/folders?path=" + urllib.parse.quote(second))
        self.assertTrue(forgotten["ok"], forgotten)
        self.assertNotIn(second.lower(), [entry["path"].lower() for entry in forgotten["watched"]])
        self.assertIn("Nothing on your computer was changed", forgotten["message"])

    def test_forgetting_a_folder_it_never_watched_is_refused(self):
        data = self.delete("/api/folders?path=" + urllib.parse.quote(self.workdir + "-nope"))
        self.assertIn("error", data)

    def test_rescan_finds_the_same_apps(self):
        data = self.post("/api/rescan", {})
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn(self.app_id, [card["id"] for card in data["apps"]])

    def test_start_returns_the_url_the_port_and_what_it_said(self):
        data = self.post("/api/start", {"id": self.app_id})
        self.assertTrue(data.get("ok"), data)
        self.assertTrue(data["url"].startswith("http://127.0.0.1:"))
        self.assertGreater(data["port"], 1024)
        self.assertEqual(data["name"], "FakeApp")
        self.assertIsInstance(data["said"], list)
        self.assertEqual(fetch(data["url"])[0], 200)

        once = self.get("/api/state")
        mine = [card for card in once["apps"] if card["id"] == self.app_id][0]
        self.assertIsNotNone(mine["running"])
        self.assertEqual(mine["running"]["url"], data["url"])
        self.assertFalse(mine["can_start"] is None)

        log = self.get("/api/log/%s" % self.app_id)
        self.assertTrue(log["running"])
        self.assertTrue(any("listening" in line for line in log["log"]))

        stopped = self.post("/api/stop", {"id": self.app_id})
        self.assertTrue(stopped["stopped"], stopped)
        self.assertTrue(stopped["confirmed"], stopped)
        after = [card for card in self.get("/api/state")["apps"] if card["id"] == self.app_id][0]
        self.assertIsNone(after["running"])

    def test_starting_twice_over_the_api_returns_one_address(self):
        first = self.post("/api/start", {"id": self.app_id})
        second = self.post("/api/start", {"id": self.app_id})
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_running"])
        self.assertEqual(first["url"], second["url"])
        self.post("/api/stop", {"id": self.app_id})

    def test_starting_an_unknown_id_is_a_404(self):
        self.assertEqual(self.status_of("/api/start", {"id": "nonsense"}), 404)
        self.assertEqual(self.status_of("/api/start", {}), 404)
        self.assertEqual(self.status_of("/api/stop", {"id": "nonsense"}), 404)
        self.assertEqual(self.get_status("/api/log/nonsense"), 404)

    def test_a_path_from_the_page_is_ignored_even_when_the_id_is_real(self):
        """The security test: the address that starts things takes an id, not a path."""
        elsewhere = tempfile.mkdtemp(prefix="nanodesk-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, True)
        stranger = build_fake_app(Path(elsewhere), name="stranger")
        # A real card, but with a path pointing somewhere nanoDesk never looked.
        result = self.post("/api/start", {"id": self.app_id, "path": str(stranger)})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("127.0.0.1", result["url"])
        self.assertNotIn(str(stranger), json.dumps(runner.running()))
        # …and an id that was never discovered gets nothing at all.
        made_up = self.post("/api/start", {"id": apps.app_id(stranger), "path": str(stranger)})
        self.assertIn("error", made_up)
        self.assertIn("I do not know an app", made_up["error"])
        self.assertNotIn(str(stranger), json.dumps(runner.running()))
        self.post("/api/stop", {"id": self.app_id})

    def test_the_stream_narrates_every_step_and_finishes(self):
        raw = self.stream("/api/start/stream", {"id": self.app_id})
        self.assertIn("[DONE]", raw)
        events = []
        for line in raw.splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                events.append(json.loads(line[6:]))
        texts = [event.get("text", "") for event in events if event.get("type") == "step"]
        joined = " | ".join(texts)
        self.assertIn("Choosing a port", joined)
        self.assertIn("Starting it", joined)
        self.assertIn("Waiting for it to answer", joined)
        self.assertTrue(any("It is up at http://127.0.0.1:" in text for text in texts), joined)
        running = [event for event in events if event.get("type") == "running"]
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]["url"], self.post("/api/start", {"id": self.app_id})["url"])
        self.assertTrue(any(event.get("type") == "done" for event in events))
        self.assertEqual(self.post("/api/stop", {"id": self.app_id})["stopped"], True)

    def test_the_stream_for_an_unknown_id_is_a_404_before_any_streaming(self):
        self.assertEqual(self.status_of("/api/start/stream", {"id": "nonsense"}), 404)

    def test_another_website_cannot_poke_this_one(self):
        request = urllib.request.Request(
            self.base + "/api/start",
            data=json.dumps({"id": self.app_id}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "https://example.com"},
            method="POST",
        )
        before = [item["id"] for item in runner.running()]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(before, [item["id"] for item in runner.running()])

    def test_doctor_text_lists_where_it_looked_and_what_it_found(self):
        text = server.doctor_text()
        self.assertIn("nanoDesk", text)
        self.assertIn("Python", text)
        self.assertIn("FakeApp", text)
        self.assertIn(os.path.realpath(self.workdir), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
