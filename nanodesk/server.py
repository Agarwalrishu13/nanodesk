"""Every address the page can ask for, each one commented.

The page is three static files; this file is the whole back end. Requests
arrive only from 127.0.0.1, because the server binds the loopback address and
nothing else.

Two things worth knowing before reading:

* **A start is a stream.** ``/api/start/stream`` reports each step the moment
  it happens — choosing a port, starting it, waiting for it to answer — so the
  card narrates instead of spinning. ``/api/start`` does the same work in one
  reply, because a page is not the only caller.
* **The page sends an id, never a path.** Starting a process is the one thing
  here that could be abused, so an app is only ever the card nanoDesk itself
  discovered: the request names an id, the id is looked up in the list of
  apps found on this computer, and :mod:`nanodesk.runner` refuses anything
  whose id does not match the folder it claims to be in.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

from . import APP_NAME, __version__, apps, machine, runner, store
from .httpbase import App, Error, Json, Stream

WEB_DIR = Path(__file__).resolve().parent / "web"

_scan_lock = threading.Lock()
_scan_cache: dict = {"apps": [], "when": 0.0}


# --------------------------------------------------------------------------
# The list of apps, and the two rules that keep it honest
# --------------------------------------------------------------------------
def scan(refresh: bool = False) -> list:
    """Every app on this computer, looked up fresh at least once a second.

    Kept because the page asks twice on load (state, then a card's log), and
    because a folder can be added between two questions.
    """
    with _scan_lock:
        fresh_enough = _scan_cache["apps"] and not refresh and (time.time() - _scan_cache["when"]) < 1.0
        if not fresh_enough:
            _scan_cache["apps"] = apps.find_apps()
            _scan_cache["when"] = time.time()
        return list(_scan_cache["apps"])


def lookup(app_id: str):
    """The app with that id, or ``None``. The only way a path ever reaches the runner."""
    wanted = str(app_id or "")
    if not wanted:
        return None
    for app in scan():
        if app["id"] == wanted:
            return app
    return None


def folders_watched() -> list:
    """The folders being watched, in the order they are looked in."""
    entries = []
    for folder in store.load_folders():
        entries.append({"path": folder, "kind": "yours", "exists": Path(folder).expanduser().is_dir()})
    home = apps.family_home()
    entries.append({"path": str(home), "kind": "beside", "exists": home.is_dir()})
    for entry in entries:
        entry["apps"] = _count_under(entry["path"])
    return entries


def _count_under(folder: str) -> int:
    try:
        base = os.path.normcase(str(Path(folder).expanduser().resolve()))
    except OSError:
        return 0
    total = 0
    for app in scan():
        candidate = os.path.normcase(str(Path(app["path"]).resolve()))
        if candidate == base or candidate.startswith(base + os.sep):
            total += 1
    return total


def nonoforge_nearby() -> bool:
    """True when a nonoForge folder is somewhere nanoDesk looks.

    Checked against the folders themselves rather than the cards, because the
    point of mentioning it is the case where *nothing* was found: someone with
    no apps yet is exactly who nonoForge is for.
    """
    for folder in apps.candidate_folders():
        if folder.name.lower() in ("nonoforge", "nono-forge"):
            return True
    return False


def apps_for_page(refresh: bool = False) -> list:
    """The cards, with what is running folded in."""
    up = {item["id"]: item for item in runner.running()}
    cards = []
    for app in scan(refresh):
        shown = dict(app)
        shown["running"] = up.get(app["id"])
        shown["can_start"] = not app["problems"]
        cards.append(shown)
    return cards


def _must_find(app_id: str):
    """Shared by start and stop: an id that is not a card here is a plain 404."""
    app = lookup(app_id)
    if app is None:
        return None, Error(
            "I do not know an app with that name. Press “Look again” and pick one of the cards.", 404
        )
    return app, None


def _from_local_page(request, require_json: bool = False) -> bool:
    """A cheap guard against another website poking this app in your browser.

    Anyone can make a browser send a request to localhost, so: if an ``Origin``
    is present it must be this machine, and for the addresses that take JSON
    the body must actually be JSON — which forces a browser to ask permission
    first, and that permission is never granted because this app sends no CORS
    headers. Requests with no Origin at all are allowed: that is the page's own
    fetch, curl, and the test suite.
    """
    origin = request.header("Origin") or ""
    if origin:
        host = origin.split("//")[-1].split("/")[0].split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
    if require_json:
        kind = (request.header("Content-Type") or "").split(";")[0].strip().lower()
        if kind and kind != "application/json":
            return False
    return True


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------
def create_app() -> App:
    app = App(APP_NAME, WEB_DIR, version=__version__)

    # -- what this is ------------------------------------------------------
    @app.get("/api/health")
    def health(_request):
        return Json({"ok": True, "app": APP_NAME, "version": __version__})

    @app.get("/api/state")
    def state(_request):
        """Everything the page needs to draw itself, in one reply."""
        cards = apps_for_page()
        return Json(
            {
                "app": APP_NAME,
                "version": __version__,
                "apps": cards,
                "running": runner.running(),
                "watched": folders_watched(),
                "settings": store.load_settings(),
                "computer": machine.summary(),
                "beside": {"path": str(apps.family_home()), "exists": apps.family_home().is_dir()},
                "nonoforge_here": nonoforge_nearby(),
                "home": str(store.data_dir()),
            }
        )

    @app.post("/api/rescan")
    def rescan(request):
        if not _from_local_page(request, require_json=True):
            return Error("That request did not come from this app.", 403)
        started = time.time()
        cards = apps_for_page(refresh=True)
        store.note_scan(started)
        return Json({"apps": cards, "watched": folders_watched(), "count": len(cards)})

    # -- the folders you point it at ---------------------------------------
    @app.post("/api/folders")
    def add_folder(request):
        """Watch one more folder. Anything that is not a real folder is refused, plainly."""
        if not _from_local_page(request, require_json=True):
            return Error("That request did not come from this app.", 403)
        typed = str(request.json().get("path") or "").strip().strip('"').strip("'")
        if not typed:
            return Error(
                "Type the full path of a folder, for example  C:\\Users\\you\\Downloads\\apps  "
                "— you can copy it from the address bar of your file manager."
            )
        try:
            folder = Path(typed).expanduser()
        except (OSError, ValueError):
            return Error("That path could not be read as a folder name. Try copying it again.")
        if not folder.exists():
            return Error(
                "There is nothing at “%s”. Check the spelling, or copy the path straight from your "
                "file manager." % typed
            )
        if not folder.is_dir():
            return Error(
                "“%s” is a file, not a folder. Point me at the folder that holds your apps." % typed
            )
        resolved = str(folder.resolve())
        already = resolved in [entry["path"] for entry in folders_watched()]
        store.add_folder(resolved)
        cards = apps_for_page(refresh=True)
        inside = _count_under(resolved)
        return Json(
            {
                "ok": True,
                "already_there": already,
                "added": resolved,
                "message": (
                    "“%s” was already on the list." % resolved
                    if already
                    else "Watching “%s” now. I found %d app%s in it." % (resolved, inside, "" if inside == 1 else "s")
                ),
                "apps": cards,
                "watched": folders_watched(),
            }
        )

    @app.delete("/api/folders")
    def forget_folder(request):
        typed = str(request.q("path", "") or (request.json() or {}).get("path") or "").strip()
        if not typed:
            return Error("Which folder should I forget?")
        saved = store.load_folders()
        try:
            wanted = str(Path(typed).expanduser().resolve())
        except (OSError, ValueError):
            wanted = typed
        if wanted not in saved:
            return Error("“%s” is not one of the folders I was watching." % typed)
        store.forget_folder(wanted)
        return Json(
            {
                "ok": True,
                "forgotten": wanted,
                "message": "I will not look in “%s” any more. Nothing on your computer was changed." % wanted,
                "apps": apps_for_page(refresh=True),
                "watched": folders_watched(),
            }
        )

    # -- starting ----------------------------------------------------------
    @app.post("/api/start")
    def start_app(request):
        """Start one app and wait for it to answer. One reply, no narration."""
        if not _from_local_page(request, require_json=True):
            return Error("That request did not come from this app.", 403)
        found, failure = _must_find(str(request.json().get("id") or ""))
        if failure:
            return failure
        result = runner.start(found)
        if not result.get("ok"):
            # Not a 404 and not a crash: the app exists, it just would not start.
            return Error(result.get("error", "It did not start."), 400, id=found["id"], said=result.get("said", []))
        result["name"] = found["name"]
        result["emoji"] = found["emoji"]
        return Json(result)

    @app.post("/api/start/stream")
    def start_stream(request):
        """The same start, narrated step by step."""
        if not _from_local_page(request, require_json=True):
            return Error("That request did not come from this app.", 403)
        found, failure = _must_find(str(request.json().get("id") or ""))
        if failure:
            return failure
        return Stream.sse(_start_events(found))

    @app.post("/api/stop")
    def stop_app(request):
        if not _from_local_page(request, require_json=True):
            return Error("That request did not come from this app.", 403)
        found, failure = _must_find(str(request.json().get("id") or ""))
        if failure:
            return failure
        result = runner.stop(found["id"])
        result["id"] = found["id"]
        result["name"] = found["name"]
        if not result.get("stopped"):
            result["message"] = "%s was not running, so there was nothing to stop." % found["name"]
        elif result.get("confirmed"):
            result["message"] = "%s has stopped." % found["name"]
        return Json(result)

    @app.get("/api/log/{app_id}")
    def app_log(request):
        """The last lines the app printed — the most useful thing to show when it fails."""
        found = lookup(request.params["app_id"])
        if found is None:
            return Error("I do not know an app with that name.", 404)
        return Json(
            {
                "id": found["id"],
                "name": found["name"],
                "running": runner.status_for(found["id"]) is not None,
                "log": runner.log_tail(found["id"]),
            }
        )

    return app


def _start_events(app: dict):
    """A start, turned into the sentences a person reads on the card."""
    steps: "queue.Queue" = queue.Queue()
    box: dict = {}

    def work() -> None:
        try:
            box["result"] = runner.start(app, step=lambda message: steps.put({"type": "step", "text": message}))
        except Exception as exc:  # a start must never look like a crash
            box["error"] = str(exc)
        finally:
            steps.put(None)

    yield {"type": "step", "text": "Looking at “%s”." % app["name"]}
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while True:
        item = steps.get()
        if item is None:
            break
        yield item

    if box.get("error"):
        yield {"type": "error", "message": box["error"]}
        return

    result = box.get("result") or {}
    if not result.get("ok"):
        yield {
            "type": "error",
            "message": result.get("error", "It did not start."),
            "said": result.get("said", []),
        }
        return
    yield {
        "type": "running",
        "name": app["name"],
        "url": result["url"],
        "port": result["port"],
        "already_running": bool(result.get("already_running")),
        "said": result.get("said", []),
    }
    yield {"type": "done"}


def doctor_text() -> str:
    """The same information as the page, for the terminal, for when nothing works."""
    cards = apps_for_page()
    computer = machine.summary()
    lines = [
        "",
        "  %s %s — what this computer has" % (APP_NAME, __version__),
        "  " + "-" * 56,
        "  Python %s on %s" % (computer["python"], computer["os"]),
        "  %s" % computer["python_sentence"],
        "  %s" % computer["path_sentence"],
        "  Settings and folders: %s" % store.data_dir(),
        "",
        "  Where I look for apps",
    ]
    for entry in folders_watched():
        label = "you added" if entry["kind"] == "yours" else "beside nanoDesk"
        missing = "" if entry["exists"] else "  (not there any more)"
        lines.append("    %-14s %-3d app(s)  %s%s" % (label, entry["apps"], entry["path"], missing))
    lines.append("")
    lines.append("  Apps found: %d" % len(cards))
    for card in cards:
        state = "running" if card.get("running") else ("cannot start" if card["problems"] else "ready")
        lines.append("    %s  %-14s %-12s %s" % (card["emoji"], card["name"][:14], state, card["path"]))
        for problem in card["problems"]:
            lines.append("        problem: %s" % problem)
    lines.append("")
    lines.append("  Local AI engines")
    for engine in computer["engines"]:
        lines.append(
            "    %-12s port %-6d %s" % (engine["name"], engine["port"], "running" if engine["running"] else "not running")
        )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Kept here as well as in ``__main__`` so the module can be run directly."""
    from .__main__ import main as entry

    return entry(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
