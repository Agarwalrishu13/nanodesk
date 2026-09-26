<div align="center">

# nanoDesk

**Every app you have, one click away.**

No code. No terminal. No account. nanoDesk looks for the little apps already on
your computer, says in plain words what each one does, and starts the one you
point at — telling you what it is doing while it does it.

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9+-58a6ff.svg)]()
[![dependencies](https://img.shields.io/badge/runtime%20deps-0-f0883e.svg)]()
[![tests](https://img.shields.io/badge/tests-57%20passing-3ddc97.svg)]()

</div>

---

## What this is, in one paragraph

Somebody who downloads six of these little apps ends up with six folders, each
with its own `run.bat`, and no clear idea which one does what. nanoDesk is the
front door for all of them. It looks in the folders you point it at, writes one
card per app — an emoji, a name, and one honest sentence taken from that app's
own README — and starts the one you press **Open** on, narrating each step:
choosing a port, starting it, waiting for it to answer. If an app cannot start,
the card says why instead of offering a button that would fail. It never opens
a terminal, never asks for an account, and never touches the internet.

**Zero dependencies.** The entire app is the Python standard library plus three
static files. There is nothing to `pip install`, ever.

---

## Use it

1. Install Python if you do not have it — [python.org/downloads](https://www.python.org/downloads/).
   On Windows, tick **“Add python.exe to PATH”** during setup.
2. Download this repo (green **Code** button → *Download ZIP*) and unzip it.
3. Keep it next to your other nano apps if you can — the folder that holds this
   one is where nanoDesk looks first.
4. **Windows:** double-click `run.bat`. **macOS / Linux:** double-click `run.sh`
   (or `./run.sh` in a terminal).
5. Your browser opens at `http://127.0.0.1:8782`. That is the app.

<details>
<summary>Prefer the command line? (you do not need to)</summary>

```bash
python start.py                  # start and open the browser
python -m nanodesk               # the same thing
python -m nanodesk doctor        # print what this computer has, then exit
python -m nanodesk --port 8792 --no-browser
```

</details>

---

## What it finds

nanoDesk looks in two places, in this order:

1. **the folders you added** to the list on the page (kept in
   `~/.nanodesk/settings.json`, remembered between runs);
2. **the folder this checkout is in**, because that is where the family
   normally sits side by side — plus each of its immediate subfolders.

A folder is one of your apps when it looks like the rest of the family:

```
myapp/
├── start.py            ← so it can be started
├── README.md           ← so it can describe itself
└── myapp/              ← the package
    ├── __init__.py
    └── __main__.py
```

…or when it contains a **`.nano.json`** manifest, which is the quickest way for
any app to describe itself. A folder named like the family (`nano…`, `nono…`)
that holds a Python package but no `start.py` is shown too — as a card that
explains why it cannot be started, which is more useful than saying nothing.

### `.nano.json` — how an app describes itself

Optional, and nanoDesk works perfectly well without it. Put this file in the top
of an app folder:

```json
{
  "name": "nanoThing",
  "emoji": "🔧",
  "blurb": "One sentence about what this does.",
  "port": 8790
}
```

| key | what it is | if you leave it out |
|---|---|---|
| `name` | what the card is called | the README's first heading, else the folder name |
| `emoji` | one character for the card | a sensible guess from the name and the README, else 🧩 |
| `blurb` | one line, about 200 characters | the first bolded sentence or first paragraph of `README.md` |
| `port` | the port it would like to use | a free port is chosen at start time |

Nothing is ever invented. If a folder has no README and no manifest, its card
says exactly that: *“An app in <folder>.”*

---

## What you can do in the window

- **Open an app** with one button, and watch the card narrate every step while
  it happens: *Choosing a port. Starting it. Waiting for it to answer. It is up
  at http://127.0.0.1:8791.*
- **Stop it** from the same card, and the port shown on it stays visible so you
  can find your way back.
- **Read what an app said.** Every app's last 200 lines are kept, so a failure
  comes with the app's own words instead of a shrug.
- **Add a folder** by pasting its path — the folder your apps live in, not each
  app one at a time. Folders are chips you can remove again.
- **See what this computer has:** the Python version, whether `python` is on
  PATH, and which local AI engines are listening (Ollama, LM Studio, llama.cpp,
  nanollama.c), each with a coloured dot and one sentence.

---

## Where your stuff lives

```
~/.nanodesk/
└── settings.json     the folders you added to the list
```

Delete that folder and nanoDesk forgets the folders you added. It writes
nothing anywhere else: no logs, no database, no account. The apps it starts are
left exactly where they are, and stopping one changes nothing on disk.

---

## How it works (for the curious)

```
browser  ──►  httpbase.py    routing, static files, JSON replies, SSE      (stdlib copy)
              server.py      the /api/* addresses
              apps.py        finding apps, reading READMEs, working out what is missing
              runner.py      free port → launch → knock → report, and stopping again
              machine.py     what this computer has, including the AI engines
              store.py       the folders you added, as plain JSON
```

- **Zero dependencies for a reason.** A launcher that needed installing would be
  useless to the people it is for. The whole thing is the standard library, and
  the tests prove it by importing every module with nothing installed.
- **Zero-dependency subprocesses.** Apps are launched with `sys.executable`, so
  an app always runs on the same Python that nanoDesk is running on — the one
  that is definitely there.
- **Starting an app is a subprocess with a handshake.** nanoDesk picks a free
  port, launches the app's own `start.py --no-browser --port N` with the app's
  folder as the working directory, then **knocks on that address until it
  answers** before saying anything worked. A page that loads is a stronger
  promise than a line of output on a pipe, and it behaves the same on Windows,
  macOS and Linux.
- **A port that already answers is skipped.** Testing a port by binding it is
  not enough on Windows, where two servers can share a number: the new app could
  be mistaken for whatever was already there. So a door that answers is passed
  over before anything is launched.
- **A failure comes with the app's own words.** If it never answers, the last
  few lines it printed are shown on the card — for a person who does not use a
  terminal, that is the single most useful thing to hand them.
- **It will not run a folder it did not find.** The page sends an **id**, never
  a path. That id is looked up in the list nanoDesk itself discovered, and the
  runner refuses anything whose id does not match the folder it claims to be in.
  Starting an arbitrary path would be a way to run code through a web page.
- **The page is not drivable by other websites.** A request carrying an `Origin`
  must come from this machine, and the JSON addresses require an actual JSON
  content type — which makes a browser refuse a cross-site request.
- **Nothing from a README is trusted.** Folder names and README text are put on
  the page with `textContent`, never as HTML, so a README containing a `<script>`
  tag is just a README containing a `<script>` tag.

## Tests

```bash
python -m unittest discover tests -v     # 57 tests, no dependencies
```

The interesting ones are not “does this address return 200”. They are: *can a
fake app be built in a temporary folder, discovered, started as a real
subprocess on a port nanoDesk chose, fetched over HTTP and then stopped?* — and
*can a folder full of junk make it crash?* The suite also keeps a listener open
on a port and checks that a start refuses to land on it, and it tries to start a
folder nanoDesk never discovered, which must fail.

---

## Honest limitations

- **It can only start apps it can find.** An app with no `start.py` cannot be
  started from here, whatever else is in the folder. Those apps still get a
  card, and the card says exactly why there is no button.
- **It does not install anything.** If an app needs something you have not
  installed, it will fail, and all nanoDesk can do is show you the last lines it
  printed. When an app lists packages in its `requirements.txt`, the card says
  so — but nothing is fetched for you.
- **Starting one app does not start the others.** Each card is one program;
  there is no "start everything" button.
- **It only knows what the README says.** If an app's README is vague, the card
  is vague. Nothing is invented to fill the gap.
- **It cannot stop an app you started by double-clicking its own `run.bat`.**
  nanoDesk can only stop the apps it started itself, and only while it is still
  running.
- **A port it cannot use is passed over silently.** If an app insists on its own
  hard-coded port and something else already has it, the app will fail and its
  own output is what you will see.
- **It waits up to 30 seconds for an app to answer.** A very slow app — a first
  run that is unpacking models, for instance — can be reported as a failure
  while it is still starting. Start it again a moment later and it will be
  quicker.
- **When it closes, the apps it started close too.** That is deliberate, but it
  means closing the nanoDesk window is not a way to keep an app running in the
  background.
- **Local only.** It listens on `127.0.0.1` and nothing else, so your phone
  cannot see it, and neither can anyone else on the network.
- **Windows can let two servers share a port number.** nanoDesk skips a port
  that is already answering, but a sibling started by hand somewhere else is
  still invisible to it.

## Family

| repo | role |
|---|---|
| [nanollama.c](https://github.com/Agarwalrishu13/nanollama.c) | the inference engine, in dependency-free C |
| [nanobrain](https://github.com/Agarwalrishu13/nanobrain) | the from-scratch trainer + BPE tokenizer + exporter |
| [nanoforge](https://github.com/Agarwalrishu13/nanoforge) | the offline studio for building your own tiny model |
| [nanolaama](https://github.com/Agarwalrishu13/nanolaama) | **talk to an AI** — a friendly window onto local engines |
| [nanolearn](https://github.com/Agarwalrishu13/nanolearn) | **teach an AI** — drop a spreadsheet, get an answer machine |
| [nanodoc](https://github.com/Agarwalrishu13/nanodoc) | **ask a document** — drop a file in, get answers with their sources |
| [nonoForge](https://github.com/Agarwalrishu13/nonoforge) | **build an app** — pick a card, press one button, it exists |
| **nanoDesk** (this repo) | **the front door** — every app you have, one click away |

## License

MIT © Priyanshu Agarwal
