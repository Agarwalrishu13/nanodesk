"""Starting and stopping the apps nanoDesk found, and watching what they say.

The shape of a start, in order, because the order is the promise:

1. refuse anything that is not an app this app discovered itself;
2. pick a port here, so the address opened later is the address it is on;
3. launch ``start.py --no-browser --port N`` in the app's own folder;
4. **knock on the address until something answers**, and only then say it
   worked. A page that loads is a stronger promise than a line of output on a
   pipe, and it is the same code on Windows, macOS and Linux.

If it never answers, the last thing the app printed is what a person needs to
see, so that is what comes back with the failure.
"""

from __future__ import annotations

import atexit
import collections
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import apps
from .httpbase import free_port

# Where a start looks for a free door number. Past the ports the family uses by
# default (8760–8782), so a running sibling never has to be moved aside.
FIRST_PORT = 8790
LOG_LINES = 200
START_TIMEOUT = 30.0
STOP_TIMEOUT = 2.0

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class RunningApp:
    """An app nanoDesk started, and the address it is answering on."""

    def __init__(self, app: dict, process: "subprocess.Popen", port: int):
        self.id = str(app.get("id") or "")
        self.name = str(app.get("name") or "app")
        self.path = str(app.get("path") or "")
        self.emoji = str(app.get("emoji") or "")
        self.process = process
        self.port = port
        self.url = "http://127.0.0.1:%d" % port
        self.started = time.time()
        self.log: "collections.deque" = collections.deque(maxlen=LOG_LINES)

    def alive(self) -> bool:
        return self.process.poll() is None

    def say(self, line: str) -> None:
        self.log.append(str(line).rstrip())

    def tail(self, lines: int = 6) -> list:
        return list(self.log)[-lines:]

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "path": self.path,
            "port": self.port,
            "url": self.url,
            "pid": self.process.pid,
            "seconds": round(time.time() - self.started, 1),
        }


_RUNNING: dict = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Refusing to run what we were not asked to run
# --------------------------------------------------------------------------
def refuse_reason(app) -> str:
    """Why this app must not be started, or an empty string if it is fine.

    A web page must not be able to run code on this computer, so this is the
    gate that matters: an app counts only if :mod:`nanodesk.apps` described it
    (its id is the fingerprint of the folder it claims to be in), if that
    folder is still there, and if it still has the ``start.py`` we saw.
    """
    if not apps.is_known(app):
        return (
            "I only start apps I found myself. Press “Look again” and pick one of the cards."
        )
    folder = Path(str(app.get("path") or ""))
    if not folder.is_dir():
        return "%s is not there any more. Press “Look again”." % folder
    if not (folder / "start.py").is_file():
        return "%s has no start.py in it, so there is nothing for me to start." % folder.name
    return ""


# --------------------------------------------------------------------------
# Starting
# --------------------------------------------------------------------------
def start(app, port=None, step=None) -> dict:
    """Start one app and wait until it answers.

    ``step`` is called with a short plain sentence at each stage, which is how
    the page narrates instead of spinning. Returns ``{"ok": True, ...}`` or
    ``{"error": "..."}``; it never raises.
    """
    announce = step or (lambda message: None)

    reason = refuse_reason(app)
    if reason:
        return {"error": reason}

    app_id = str(app["id"])
    folder = Path(str(app["path"]))

    with _LOCK:
        existing = _RUNNING.get(app_id)
        if existing and existing.alive():
            return {
                "ok": True,
                "id": app_id,
                "name": existing.name,
                "url": existing.url,
                "port": existing.port,
                "already_running": True,
                "said": existing.tail(),
            }
        _RUNNING.pop(app_id, None)  # a dead one from a previous run

    announce("Choosing a port.")
    wanted = port or app.get("port") or FIRST_PORT
    chosen = _free_port(wanted)

    announce("Starting it.")
    # -u is Python's own "do not buffer the output" flag. Without it a started
    # app's banner sits in its buffer instead of reaching the card, and the log
    # a person is shown when something goes wrong is emptier than it should be.
    command = [sys.executable, "-u", str(folder / "start.py"), "--no-browser", "--port", str(chosen)]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(folder),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            errors="replace",
            creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        return {"error": "I could not start it: %s" % exc}

    runner = RunningApp(app, process, chosen)
    runner.say("$ %s" % " ".join(command))
    with _LOCK:
        _RUNNING[app_id] = runner
    threading.Thread(target=_watch, args=(runner,), daemon=True).start()

    announce("Waiting for it to answer.")
    if not _knock(runner.url, runner, START_TIMEOUT):
        tail = runner.tail()
        stop(app_id)
        if tail:
            return {
                "error": "It started but never answered on %s. The last thing it said was: %s"
                % (runner.url, " / ".join(tail[-3:])),
                "said": tail,
            }
        return {
            "error": "It started but never answered on %s, and it said nothing at all. "
            "If another copy of it is already running, close that one and try again." % runner.url,
            "said": tail,
        }

    announce("It is up at %s" % runner.url)
    return {
        "ok": True,
        "id": app_id,
        "name": runner.name,
        "url": runner.url,
        "port": runner.port,
        "already_running": False,
        "said": runner.tail(),
    }


def _free_port(preferred) -> int:
    """A port that is free *and* not already answering.

    ``httpbase.free_port`` tests a port by binding it, and on Windows a bind
    succeeds even when something else is already listening on it. A start that
    landed on somebody else's port would then knock, be answered by the wrong
    server, and call that a success. So a door that already answers is skipped
    as well as one that will not bind.
    """
    preferred = int(preferred)
    for offset in range(20):
        candidate = free_port(preferred + offset)
        if not _answers("http://127.0.0.1:%d" % candidate, timeout=0.15):
            return candidate
    return free_port(preferred + 20)


def _watch(runner: RunningApp) -> None:
    """Read what the app prints, so its pipe never fills up and we can show it."""
    stream = runner.process.stdout
    if stream is None:
        return
    try:
        for line in stream:
            for piece in str(line).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if piece.strip():
                    runner.say(piece)
    except Exception:
        pass  # the app was stopped out from under us; there is nothing to report


def _knock(url: str, runner: RunningApp, timeout: float) -> bool:
    """Knock on the door until somebody answers.

    HTTPError counts as an answer: a page that says "no such address" is still
    a running server, and arguing with it is not this function's job.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runner.process.poll() is not None:
            return False  # it gave up before it was ready
        try:
            connection = urllib.request.urlopen(url + "/", timeout=2)
            connection.read(1)
            connection.close()
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.2)
    return False


# --------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------
def stop(app_id: str) -> dict:
    """Stop one app and confirm it has gone."""
    with _LOCK:
        runner = _RUNNING.pop(str(app_id), None)
    if runner is None:
        return {"stopped": False, "was_running": False, "reason": "It is not running."}

    _end_process(runner)
    confirmed = _wait_until_gone(runner, STOP_TIMEOUT)
    with _LOCK:
        _RUNNING.pop(str(app_id), None)
    result = {"stopped": True, "was_running": True, "name": runner.name, "confirmed": confirmed}
    if not confirmed:
        result["note"] = (
            "%s was asked to stop and its port is still answering. Close the window it opened, "
            "if it opened one." % runner.name
        )
    return result


def _end_process(runner: RunningApp) -> None:
    """End the app, and on Windows the little tree of processes it may have started."""
    process = runner.process
    if process.poll() is None and os.name == "nt":
        # Asking the tree first is what catches a helper process the app left
        # behind; a plain terminate() would leave it holding the port.
        _taskkill(process.pid, force=False)
    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=4)
    except Exception:
        if os.name == "nt":
            _taskkill(process.pid, force=True)
        try:
            process.kill()
            process.wait(timeout=4)
        except Exception:
            pass
    for stream in (process.stdout, process.stderr, process.stdin):
        try:
            if stream:
                stream.close()
        except Exception:
            pass


def _taskkill(pid: int, force: bool) -> bool:
    """Windows only: take the app and anything it started with it."""
    command = ["taskkill", "/T", "/PID", str(pid)]
    if force:
        command.insert(1, "/F")
    try:
        subprocess.run(command, capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        return True
    except Exception:
        return False


def _wait_until_gone(runner: RunningApp, timeout: float) -> bool:
    """Wait for the process to end and its address to stop answering."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runner.process.poll() is not None and not _answers(runner.url):
            return True
        time.sleep(0.1)
    return runner.process.poll() is not None and not _answers(runner.url)


def _answers(url: str, timeout: float = 0.4) -> bool:
    """True when something is still listening on that address."""
    try:
        connection = urllib.request.urlopen(url + "/", timeout=timeout)
        connection.read(1)
        connection.close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# What is running right now
# --------------------------------------------------------------------------
def running() -> list:
    """Every app that is up right now, oldest first."""
    found = []
    with _LOCK:
        for app_id, runner in list(_RUNNING.items()):
            if runner.alive():
                found.append(runner.snapshot())
            else:
                _RUNNING.pop(app_id, None)
    return sorted(found, key=lambda item: item["seconds"], reverse=True)


def status_for(app_id: str) -> dict:
    """The running details for one app, or ``None`` when it is not up."""
    for item in running():
        if item["id"] == str(app_id):
            return item
    return None


def log_tail(app_id: str, lines: int = 60) -> list:
    with _LOCK:
        runner = _RUNNING.get(str(app_id))
    if runner is None:
        return []
    return list(runner.log)[-lines:]


def stop_all() -> int:
    """Stop everything nanoDesk started. Called on the way out, always."""
    stopped = 0
    for item in running():
        if stop(item["id"]).get("stopped"):
            stopped += 1
    return stopped


atexit.register(stop_all)
