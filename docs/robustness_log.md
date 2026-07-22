# Robustness Log

A running log of hardware issues reported by people setting up RoboCam on
their own rig, what we found when we traced them through the code, and what
was changed as a result. Distinct from `CHANGELOG.md` (notable
user-facing changes per release) — this is the debugging trail: symptom →
investigation → root cause → fix, kept even when a report turns out to be
inconclusive or still open, so the reasoning isn't lost.

---

## 2026-07-21 — Mark: Mars camera not detected, printer not homing

**Reporter**: Mark (external build), Pi 5 rev B, PlayerOne Mars 662M, printer
connected at 115,200 baud.

**Symptoms**:
1. Camera setup dropdown doesn't show the Mars camera, though it works fine
   in AstroDX. Mark had edited a line in some file to get AstroDX to see the
   camera; hadn't yet said which file or where it lives.
2. Printer shows "Connected" at 115,200 baud, but clicking "Home All Axes"
   does nothing — no error, no motion. Manually triggering the limit
   switches during that window also isn't recognized.

**Investigation**:
- Camera detection (`robocam/camera.py`, `ui/setup_panel.py`) is fully
  generic across PlayerOne models — enumeration, udev rule (vendor ID
  `0xA0A0`), and the bundled SDK `.so` are all model-agnostic. Mars is not
  disadvantaged relative to Uranus in this codebase; Mars 662M's mono sensor
  is in fact already special-cased (`camera.py:185-194`). Initial theory
  that Mark's AstroDX file edit touched RoboCam's own `pyPOACamera.py` and
  conflicted with our auto-patch step (`_ensure_pypoa_patched_for_linux`)
  doesn't hold up on reflection — AstroDX almost certainly bundles its own
  SDK copy rather than reaching into RoboCam's venv. Asked Mark to confirm
  which file he actually changed before assuming a conflict.
- Printer connection (`robocam/motion.py`): `is_connected` only reflects
  whether the OS-level serial port is open (`serial_conn.is_open`) — it does
  **not** verify the firmware understood anything at the configured baud.
  A wrong baud rate opens the port fine (shows green "Connected" in the UI)
  while every byte to/from the firmware is garbled at the physical level.
  `home()` sends `G28` and waits up to `home_timeout` (90s default) for an
  `"ok"` line that never legitimately arrives under a baud mismatch, then
  raises `TimeoutError` — this exactly matches "nothing happens" and
  "limit switch presses aren't recognized" (any async status line from the
  board would be garbled too).
- Separately found (not yet confirmed as the cause here, but a real gap):
  that `TimeoutError` was being silently discarded. `_HomeThread` in
  `ui/setup_panel.py` caught it and emitted the message via a `finished`
  signal, but `_on_home_finished` never displayed it — the Home button just
  reset to its idle label with zero feedback.
- Also found: only `MarlinBackend` and `KlipperBackend` exist — no GRBL
  support. If Mark's board actually runs GRBL, `G28` doesn't mean "home" in
  GRBL (that's `$H`), so homing would fail regardless of baud. Rather than
  guess this in the reply to Mark, added instrumentation to settle it from
  evidence instead (see Fixes).
- The GUI entry point (`robocam31.py`) had no `logging` configuration at
  all, so most of the diagnostic detail already present as `logger.info` /
  `logger.debug` calls in `motion.py` and `camera.py` was going nowhere —
  there was nothing useful to ask a remote user to send us.

**Fixes applied**:
- `robocam31.py` — added `logging.basicConfig` (console + `robocam.log`
  file next to the app, already covered by `.gitignore`'s `*.log`).
- `ui/setup_panel.py` — `_on_home_finished` now shows the failure message
  in a red banner under the Home button instead of dropping it.
- `robocam/motion.py`:
  - `send_gcode`'s timeout error now includes the port and baud rate it was
    using, plus a note that GRBL isn't supported — self-diagnostic even
    read out of context (e.g. pasted into an email).
  - `connect()` now captures and logs the firmware's boot banner (Marlin
    prints `start`, GRBL identifies itself as `Grbl ...`) instead of
    discarding it via `reset_input_buffer()` before anyone looks at it, and
    logs a warning if the firmware self-identifies as GRBL. This lets a
    baud mismatch, unsupported firmware, and a genuine wiring/homing issue
    be told apart from the log file rather than guessed at over email.

**Status**: Open — waiting on Mark to reconnect on the updated code and
share `robocam.log`, and to confirm which file he edited for AstroDX.

**Follow-ups if this recurs for other users**:
- Consider validating the boot banner at `connect()` time and refusing (or
  at least warning loudly in the UI, not just the log) to proceed if it
  looks like GRBL rather than Marlin/Klipper.
- Consider an explicit "verify firmware" step (e.g. send `M115` and check
  the response) rather than relying on the boot banner alone, since not
  every Marlin build prints one.
