"""An opt-in, session-wide stack watchdog. Not loaded by default.

    python -m pytest tests -p tests.watchdog_plugin -s

Why this exists, and why pytest's own `faulthandler_timeout` was not enough.

The suite intermittently BLOCKS on this project's dev VM rather than failing:
`/proc/loadavg` reads 0.00 while one test sits unfinished, and the location
moves between runs (five distinct tests observed, all of them doing database
or TestClient work, which says the blocked resource is shared rather than
being a property of any one test). Three instruments were tried first:

* `timeout -s ABRT` -- no dump. A faulthandler dump triggered by a signal
  needs the interpreter to run the handler, and the main thread never gets
  back to the eval loop.
* pytest's `faulthandler_timeout` -- no dump either, and the reason is worth
  knowing: it is armed and cancelled **per test**, so it is not watching
  during setup, teardown or anything between tests; and its output goes to
  the captured stderr, which pytest never flushes for a test that does not
  finish. `-s` fixes the second half only.
* The same option with `-s`, verified working on a deliberately sleeping test
  with a two-second timeout -- and still silent on a real block.

So this arms ONE watchdog thread for the whole session, against a file
descriptor nothing else owns, with `repeat=True` so it keeps reporting and
`exit=False` so the run continues. If it is silent while the suite is
demonstrably blocked, that is itself a measurement: the watchdog thread is a
C thread that does not need the GIL, so its silence would mean the process is
stopped rather than waiting, which is a different search.

The dump path is deliberately NOT inside the repository: a traceback written
into a connected folder is a file somebody has to notice and remove.
"""

from __future__ import annotations

import faulthandler
import os

#: Seconds a single stretch may take before every thread's stack is dumped.
#: Overridable so a slow machine does not drown the file in false alarms.
TIMEOUT_SECONDS = float(os.environ.get("WATCHDOG_TIMEOUT_SECONDS", "55"))
DUMP_PATH = os.environ.get("WATCHDOG_DUMP_PATH", "/tmp/pytest-watchdog.dump")

_handle = None


def pytest_configure(config) -> None:
    global _handle
    _handle = open(DUMP_PATH, "w", buffering=1)  # noqa: SIM115 - lives for the session
    faulthandler.enable(file=_handle, all_threads=True)
    faulthandler.dump_traceback_later(
        TIMEOUT_SECONDS, repeat=True, exit=False, file=_handle
    )


def pytest_unconfigure(config) -> None:
    faulthandler.cancel_dump_traceback_later()
    if _handle is not None:
        _handle.close()
