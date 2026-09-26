"""Command line entry point.

Three ways in, and the first one is the only one most people will ever need::

    python start.py             # start the app and open the browser
    python -m nanodesk          # the same thing
    python -m nanodesk doctor   # print what this computer has, and stop
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__
from . import runner


def main(argv=None) -> int:
    # A Windows console on a legacy code page cannot print the cards' emoji, and
    # a crash while printing the doctor summary would be a silly way to fail.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        prog="nanodesk",
        description="%s — %s" % (APP_NAME, "one window that finds your apps and starts them."),
        epilog="Run it with no arguments and a browser window opens. That is the whole idea.",
    )
    parser.add_argument("command", nargs="?", default="run", choices=["run", "doctor", "version"],
                        help="what to do (default: run)")
    parser.add_argument("--port", type=int, default=8782, help="which port to use (default 8782)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1 — this computer only)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--version", action="store_true", help="print the version and stop")
    args = parser.parse_args(argv)

    if args.version or args.command == "version":
        print("%s %s" % (APP_NAME, __version__))
        return 0
    if args.command == "doctor":
        from .server import doctor_text

        print(doctor_text())
        return 0

    from .server import create_app

    app = create_app()
    try:
        app.serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print("\n  Could not start on %s:%d (%s).\n  Try a different port: --port 8792\n"
              % (args.host, args.port, exc))
        return 1
    finally:
        # Closing this window must not leave apps running in the background with
        # no way to reach them.
        runner.stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
