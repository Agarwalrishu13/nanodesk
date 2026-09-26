"""Finding the other nano apps that are already on this computer.

This is the file the whole app rests on. Everything here is deliberately
forgiving: it is handed real folders belonging to real people, so a folder
full of unrelated files must produce nothing at all rather than an exception,
and a folder that almost looks like an app must produce a card that says what
is missing rather than silence.

A folder is looked at, never written to. Nothing here reads anything outside
the folder it was pointed at, and nothing follows a link out of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import store

MANIFEST_NAME = ".nano.json"
KIND = "nano app"
DEFAULT_EMOJI = "🧩"
MAX_BLURB = 200

# Folders that are never an app and never hold one worth listing: leftovers,
# caches and version-control internals. Skipping them keeps a scan of a big
# folder quick and quiet.
SKIP_DIRS = {
    "__pycache__", ".git", ".github", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "venv", ".venv", "env", ".env", "site-packages",
}

# The emoji a card gets when the app does not say which one it wants. Checked
# in order, against the name, the README heading and the blurb together. The
# words are deliberately narrow: "ask" and "answer" appear in half the family's
# READMEs and would give every card the same face.
_EMOJI_HINTS = (
    (("document", "doc", "pdf", "paper", "note", "reader", "readme"), "📄"),
    (("chat", "talk", "llama", "laama", "conversation"), "💬"),
    (("forge", "build", "make", "studio", "tool", "ide"), "🔧"),
    (("learn", "train", "brain", "teach", "predict", "reinforce", "tuning"), "🧠"),
    (("chart", "data", "spreadsheet", "csv", "table", "sheet"), "📊"),
    (("image", "photo", "picture", "draw", "paint", "art"), "🖼️"),
    (("game", "play", "fun"), "🎲"),
    (("engine", "native", "compile", "code", "server"), "⚙️"),
)

# A README heading is often "nanorl — Alignment for tiny models". The part
# before the dash is the name; the rest is a subtitle that belongs on the card
# as part of the description, not in the title.
_TITLE_SPLIT = re.compile(r"\s+[—–|·:]\s+")
_MIN_PYTHON = re.compile(r"MIN_PYTHON\s*=\s*\(\s*(\d+)\s*,\s*(\d+)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_CODE = re.compile(r"`([^`]*)`")
_TAG = re.compile(r"<[^>]*>")


# --------------------------------------------------------------------------
# Where to look
# --------------------------------------------------------------------------
def family_home() -> Path:
    """The folder that normally holds the whole family, side by side.

    ``nanodesk/apps.py`` → the package → the checkout → the folder the
    checkout sits in, which is where someone who downloads several of these
    apps ends up with all of them.
    """
    return Path(__file__).resolve().parents[2]


def _self_dirs() -> set:
    """The folders belonging to nanoDesk itself, which never appear as cards."""
    package = Path(__file__).resolve().parent
    return {package, package.parent}


def _is_hidden(path: Path) -> bool:
    return path.name.startswith(".") or path.name in SKIP_DIRS


def candidate_folders(folders=None) -> list:
    """Every folder worth looking in, in the order it is looked in.

    Folders you added come first, because you pointed at them on purpose. Then
    the folder the checkout lives in, so an app downloaded next to this one is
    found with no setup at all. Each of those is looked in one level deep,
    because that is how people unzip things: ``Downloads/nanolaama/`` is the
    shape, and so is ``Downloads/apps/nanolaama/``.

    Passing ``folders`` explicitly is how the tests stay away from whatever
    happens to be on the machine.
    """
    if folders is None:
        roots = store.load_folders()
        roots.append(str(family_home()))
    else:
        roots = [str(item) for item in folders]

    found: list = []
    seen: set = set()

    def remember(folder: Path) -> None:
        try:
            resolved = folder.resolve()
        except OSError:
            return
        key = os.path.normcase(str(resolved))
        if key in seen or resolved in _self_dirs():
            return
        seen.add(key)
        found.append(resolved)

    for root in roots:
        try:
            base = Path(root).expanduser()
        except (OSError, ValueError):
            continue
        if not base.is_dir():
            continue
        remember(base)
        try:
            children = sorted((child for child in base.iterdir() if child.is_dir()),
                              key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children:
            if _is_hidden(child):
                continue
            remember(child)
    return found


# --------------------------------------------------------------------------
# Describing one folder
# --------------------------------------------------------------------------
def app_id(path) -> str:
    """A short, stable name for an app, so the page never sends a path back."""
    key = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def is_known(app) -> bool:
    """True only for a description this module wrote itself.

    Starting a process on behalf of a web page is the one thing this app must
    never get wrong, so the runner asks this before it launches anything: an
    app is only real if its id matches the folder it claims to be in.
    """
    if not isinstance(app, dict):
        return False
    path = str(app.get("path") or "")
    if not path or app.get("kind") != KIND or not app.get("id"):
        return False
    try:
        return app["id"] == app_id(path)
    except OSError:
        return False


def find_apps(folders=None) -> list:
    """One dictionary per app found. Never raises, however odd the folders are."""
    apps: list = []
    seen: set = set()
    for folder in candidate_folders(folders):
        described = describe(folder)
        if described is None:
            continue
        key = os.path.normcase(str(folder))
        if key in seen:
            continue
        seen.add(key)
        apps.append(described)
    return apps


def describe(folder) -> dict:
    """Everything the page needs about one folder, or ``None`` if it is not an app.

    The two things that make a folder an app are the family's own shape — a
    ``start.py`` beside a package with ``__init__.py`` and ``__main__.py`` — or
    a ``.nano.json`` manifest saying so. A third, narrow case is allowed
    through as well: a folder named like the family (nano…, nono…) that holds a
    Python package but no ``start.py``. Those are shown as cards that explain
    why they cannot be started, which is more useful than hiding them.
    """
    try:
        path = Path(folder).resolve()
    except OSError:
        return None
    if not path.is_dir() or path in _self_dirs():
        return None

    manifest, manifest_error = _read_manifest(path)
    has_start = (path / "start.py").is_file()
    package = _package_in(path)
    title, readme = _read_readme(path)

    readable_name = _tidy_name(title) or path.name
    family_shaped = has_start and package is not None and package["has_main"]
    if manifest is None and not family_shaped:
        if package is None or not _looks_like_family(readable_name + " " + path.name):
            return None

    name = str(manifest.get("name") or "").strip() if manifest else ""
    name = name or readable_name
    blurb = str(manifest.get("blurb") or "").strip() if manifest else ""
    blurb = blurb or blurb_from_readme(readme) or "An app in %s." % path.name
    emoji = str(manifest.get("emoji") or "").strip() if manifest else ""
    emoji = emoji or _emoji_for(name, title, blurb)

    described = {
        "id": app_id(path),
        "name": name,
        "blurb": _clip(blurb, MAX_BLURB),
        "emoji": emoji,
        "port": _manifest_port(manifest),
        "path": str(path),
        "has_manifest": manifest is not None,
        "kind": KIND,
        "readme_title": title,
        "problems": [],
        "notes": [],
    }
    described["problems"], described["notes"] = _troubles(
        path, has_start=has_start, package=package, manifest_error=manifest_error
    )
    return described


def _troubles(path: Path, has_start: bool, package, manifest_error: str) -> tuple:
    """What will stop this app working, in plain words, plus anything worth knowing."""
    problems: list = []
    notes: list = []

    if manifest_error:
        problems.append(manifest_error)
    if not has_start:
        problems.append(
            "There is no start.py in this folder, so there is nothing for me to start. "
            "This one is run from a terminal, not from here."
        )
    elif package is not None and not package["has_main"]:
        problems.append(
            "The app inside this folder has no %s/__main__.py, so start.py may not have anything "
            "to run." % package["name"]
        )

    wanted = _min_python(path / "start.py")
    if wanted and wanted > (sys.version_info.major, sys.version_info.minor):
        problems.append(
            "It says it needs Python %d.%d, and this computer has %d.%d." % (*wanted, *sys.version_info[:2])
        )

    if _only_compiled_leftovers(path):
        problems.append(
            "Only compiled leftovers (__pycache__) are in this folder — the .py files they were "
            "made from are missing, so there is nothing to run."
        )

    wanted_packages = _required_packages(path)
    if wanted_packages:
        notes.append(
            "Its requirements.txt asks for extra packages (%s). I do not install anything, so if it "
            "needs them it will say so when it starts." % ", ".join(wanted_packages[:3])
        )
    return problems, notes


# --------------------------------------------------------------------------
# Reading a folder without being fooled by it
# --------------------------------------------------------------------------
def _read_text(path: Path) -> str:
    """Read a text file, or give up quietly.

    A README written on Windows in a legacy codepage is not valid UTF-8. It is
    still readable, and a person would rather see slightly odd characters than
    no description at all, so the fallback keeps the text.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except OSError:
            return ""
    except OSError:
        return ""


def _read_manifest(path: Path) -> tuple:
    """The manifest, if there is a readable one. Returns (manifest, problem)."""
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.is_file():
        return None, ""
    try:
        data = json.loads(_read_text(manifest_path) or "{}")
    except ValueError:
        return None, (
            "Its .nano.json could not be read, so I used the README and the folder name instead."
        )
    if not isinstance(data, dict):
        return None, (
            "Its .nano.json is not in the shape I expect, so I used the README and the folder name instead."
        )
    return data, ""


def _package_in(path: Path):
    """The Python package inside an app folder, if there is one.

    Preferring a package that has ``__main__.py`` matters: an app folder also
    holds a ``tests`` package, and the tests are not the app.
    """
    fallback = None
    try:
        children = sorted((child for child in path.iterdir() if child.is_dir()),
                          key=lambda item: item.name.lower())
    except OSError:
        return None
    for child in children:
        if _is_hidden(child):
            continue
        if not (child / "__init__.py").is_file():
            continue
        found = {"name": child.name, "has_main": (child / "__main__.py").is_file()}
        if found["has_main"]:
            return found
        fallback = fallback or found
    return fallback


def _read_readme(path: Path) -> tuple:
    """The README's heading and its text. Either may be empty."""
    for name in ("README.md", "readme.md", "README.MD", "README.txt", "README"):
        candidate = path / name
        if candidate.is_file():
            text = _read_text(candidate)
            return _heading(text), text
    return "", ""


def _heading(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return strip_markdown(stripped[2:])
    return ""


def _min_python(start_py: Path):
    if not start_py.is_file():
        return None
    found = _MIN_PYTHON.search(_read_text(start_py))
    if not found:
        return None
    try:
        return int(found.group(1)), int(found.group(2))
    except ValueError:  # pragma: no cover - the regex only matches digits
        return None


def _only_compiled_leftovers(path: Path) -> bool:
    """True when the only Python left in the folder is compiled bytecode."""
    try:
        entries = list(path.iterdir())
    except OSError:
        return False
    has_cache = any(entry.is_dir() and entry.name == "__pycache__" for entry in entries)
    if not has_cache:
        return False
    return not any(entry.is_file() and entry.suffix == ".py" for entry in entries)


def _required_packages(path: Path) -> list:
    """Real entries in requirements.txt — an app asking for things to install."""
    for name in ("requirements.txt", "requirements-dev.txt"):
        candidate = path / name
        if not candidate.is_file():
            continue
        wanted = []
        for line in _read_text(candidate).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            wanted.append(stripped.split("=")[0].split(">")[0].split("<")[0].split("[")[0].strip())
        return [item for item in wanted if item]
    return []


def _looks_like_family(name: str) -> bool:
    lowered = (name or "").strip().lower()
    return lowered.startswith("nano") or lowered.startswith("nono")


def _manifest_port(manifest) -> int:
    if not manifest:
        return None
    try:
        port = int(manifest.get("port"))
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


# --------------------------------------------------------------------------
# Turning a README into a card
# --------------------------------------------------------------------------
def strip_markdown(text: str) -> str:
    """Markdown out, plain words in.

    Deliberately blunt: a card needs a sentence, not a renderer. Everything
    that could arrive from a README the author never wrote has to come out as
    text, because whatever is left here is shown to a person.
    """
    value = str(text or "").replace("\r\n", "\n")
    value = _IMAGE.sub(" ", value)          # images first: [![badge](x)](y) leaves nothing behind
    value = _LINK.sub(r"\1", value)
    value = _CODE.sub(r"\1", value)
    value = _TAG.sub(" ", value)
    value = value.replace("**", "").replace("__", "")
    value = value.replace("`", "").replace("*", "")
    value = "".join(ch if ch >= " " or ch == "\n" else " " for ch in value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\n-_#>·—–")


def _paragraphs(text: str) -> list:
    blocks = re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n"))
    for block in blocks:
        yield " ".join(line.strip() for line in block.splitlines() if line.strip())


def _looks_like_furniture(paragraph: str) -> bool:
    """Headings, badges and the badge bars under the title say nothing about the app."""
    stripped = paragraph.strip()
    if not stripped or stripped.startswith("#"):
        return True
    if stripped.startswith("!") or "shields.io" in stripped or "[![" in stripped:
        return True
    return not strip_markdown(stripped)


def blurb_from_readme(text: str) -> str:
    """The one line that says what this app is, taken from the README.

    Family READMEs open with a bolded sentence and then a paragraph of detail,
    so the bolded sentence wins when there is one. Failing that, the first
    ordinary paragraph is used, trimmed to its first couple of sentences.
    """
    for paragraph in _paragraphs(text):
        if _looks_like_furniture(paragraph):
            continue
        bolded = re.search(r"\*\*(.+?)\*\*", paragraph, re.S)
        candidate = strip_markdown(bolded.group(1)) if bolded else strip_markdown(paragraph)
        if not candidate:
            continue
        if bolded:
            return _clip(candidate, MAX_BLURB)
        return _clip(_first_sentences(candidate, 2), MAX_BLURB)
    return ""


def _first_sentences(text: str, count: int) -> str:
    """Stop at a sentence end once we have enough, rather than mid-thought."""
    cut = 0
    seen = 0
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        cut = match.end()
        seen += 1
        if seen >= count:
            break
    return text[:cut].strip() if cut else text


def _tidy_name(title: str) -> str:
    if not title:
        return ""
    part = _TITLE_SPLIT.split(title.strip(), 1)[0].strip()
    return part if part and len(part) <= 40 else (title.strip()[:40] if title else "")


def _clip(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut + "…"


def _emoji_for(*parts: str) -> str:
    haystack = " ".join(part for part in parts if part).lower()
    for words, emoji in _EMOJI_HINTS:
        if any(word in haystack for word in words):
            return emoji
    return DEFAULT_EMOJI
