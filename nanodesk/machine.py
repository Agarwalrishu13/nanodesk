"""What this computer has, said in plain words.

Two things are reported, and both are shown on the page:

* the Python running this app, and whether ``python`` and ``python3`` are on
  PATH, because "you will need Python" is the first thing anybody asks;
* which of the well-known local AI engines are **listening right now**, on the
  four ports the family uses. nanoDesk does not start these; it only says
  whether they are there, so somebody deciding what to do next has an answer.

Every probe is capped at a fraction of a second and they all run at the same
time, so the page can never be held up by an engine that is not there.
"""

from __future__ import annotations

import platform
import shutil
import sys
import threading
import urllib.error
import urllib.request

PROBE_TIMEOUT = 0.4
MIN_PYTHON = (3, 9)

# name, port, who listens there, and the two sentences worth saying about it.
LOCAL_ENGINES = (
    {
        "name": "Ollama",
        "port": 11434,
        "about": "the easiest local AI engine to install",
        "here": "Something is answering on port 11434 — that is where Ollama listens, so the "
                "chat apps in the family can use it.",
        "away": "Nothing on port 11434, so Ollama is not running. It is the easiest engine to set "
                "up if you want a chat app.",
    },
    {
        "name": "LM Studio",
        "port": 1234,
        "about": "a point-and-click model browser with an AI server inside",
        "here": "Something is answering on port 1234 — that is where LM Studio listens.",
        "away": "Nothing on port 1234, so LM Studio is not running.",
    },
    {
        "name": "llama.cpp",
        "port": 8080,
        "about": "one model file, maximum speed",
        "here": "Something is answering on port 8080 — that is where a llama.cpp server listens.",
        "away": "Nothing on port 8080, so no llama.cpp server is running.",
    },
    {
        "name": "nanollama.c",
        "port": 8090,
        "about": "the family's own engine, written in C",
        "here": "Something is answering on port 8090 — that is where your own nanollama.c engine "
                "serves the models nanobrain trained.",
        "away": "Nothing on port 8090. That is where nanoLaama starts your own nanollama.c engine.",
    },
)


def _answers(port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when something is listening on that port and speaks HTTP.

    An error page counts. A server that answers "no such address" is still a
    server, and this only ever claims that something is there.
    """
    try:
        connection = urllib.request.urlopen("http://127.0.0.1:%d/" % int(port), timeout=timeout)
        connection.read(1)
        connection.close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def probe_engines(timeout: float = PROBE_TIMEOUT) -> list:
    """All four engines, asked at the same time. Never takes longer than one timeout."""
    results: list = [None] * len(LOCAL_ENGINES)

    def ask(index: int, engine: dict) -> None:
        here = _answers(engine["port"], timeout)
        results[index] = {
            "name": engine["name"],
            "port": engine["port"],
            "about": engine["about"],
            "running": here,
            "sentence": engine["here"] if here else engine["away"],
        }

    threads = []
    for index, engine in enumerate(LOCAL_ENGINES):
        worker = threading.Thread(target=ask, args=(index, engine), daemon=True)
        worker.start()
        threads.append(worker)
    for worker in threads:
        worker.join(timeout + 0.3)
    return [item for item in results if item]


def python_version() -> str:
    return "%d.%d.%d" % sys.version_info[:3]


def python_sentence() -> str:
    if sys.version_info[:2] >= MIN_PYTHON:
        return "This computer runs Python %s, which is new enough for every app in the family." % python_version()
    return (
        "This computer runs Python %s. The apps in the family need %d.%d or newer, so they will "
        "not start until Python is updated." % (python_version(), *MIN_PYTHON)
    )


def path_commands() -> list:
    """Whether the command a person would type in a terminal exists.

    nanoDesk never needs it — that is the point of the app — but knowing this
    answers "do I have Python installed?" without anybody opening a terminal.
    """
    found = []
    for name in ("python", "python3", "py"):
        where = shutil.which(name)
        found.append({"name": name, "found": bool(where), "where": where or ""})
    return found


def path_sentence(commands: list) -> str:
    present = [item["name"] for item in commands if item["found"]]
    if not present:
        return (
            "Neither python nor python3 is on PATH. You do not need it for nanoDesk, but apps you "
            "add later might, and it is what the installer tick box “Add python.exe to PATH” fixes."
        )
    return "%s %s on PATH, so the apps can be started with %s." % (
        " and ".join(present[:2]),
        "are" if len(present[:2]) > 1 else "is",
        "them" if len(present[:2]) > 1 else "it",
    )


def summary() -> dict:
    """The whole panel, in one dictionary."""
    commands = path_commands()
    engines = probe_engines()
    running = [engine for engine in engines if engine["running"]]
    return {
        "os": "%s %s" % (platform.system(), platform.release()),
        "python": python_version(),
        "python_sentence": python_sentence(),
        "commands": commands,
        "path_sentence": path_sentence(commands),
        "engines": engines,
        "engines_running": len(running),
    }
