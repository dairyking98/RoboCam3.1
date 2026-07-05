# History

RoboCam 3.1 is the current stage of a platform that started as someone else's project and has been rebuilt twice since, each time for a specific reason rather than just for its own sake.

1. **[FlyCam](https://github.com/E-Lab-SFSU/FlyCam)** (Esquerra Lab, SFSU) — the original idea: use a 3D printer as an imaging stage. Not part of this codebase, but the inspiration for treating a 3D printer's motion system as a general-purpose positioning stage for a camera.

2. **[screamuch/RoboCam](https://github.com/screamuch/RoboCam)** — the base implementation this project was built on top of. Development continued here rather than on the original repo because that repo's author was no longer around to keep working on it.

3. **[RoboCam-Suite](https://github.com/dairyking98/RoboCam-Suite)** — the first working suite built from that base: a Tkinter GUI, Raspberry Pi + Picamera2 imaging, GPIO laser control, and calibrate/preview/experiment applications. It was designed with the intent of generalizing across different devices and experiments, but in practice was only ever built out and used for **StentorCam** (well-plate behavioral imaging of *Stentor coeruleus*).

   Partway through, **Player One monochrome astrophotography camera support was added** (Feb 2026) alongside the existing Picamera2 path. The Raspberry Pi camera's monochrome sensitivity wasn't good enough for the imaging this needed, so a dedicated monochrome astro camera was brought in to fix that — accepting a slower USB-based capture path as the tradeoff. (There was also an apparent FPS bottleneck on the Pi camera that motivated part of this, though in hindsight the Pi camera likely wasn't actually the limiting factor there — either way, sensitivity was the real goal, not FPS.)

4. **[RoboCam-Suite2.0](https://github.com/dairyking98/RoboCam-Suite2.0)** — a complete modular rewrite: cross-platform (Windows/macOS/Linux), moved off Tkinter to a PySide6 GUI, pluggable camera/motion drivers (carrying Player One, Picamera2, and OpenCV forward from v1), and a full hardware simulation mode.

5. **RoboCam3.1** (this repository) — a clean rewrite on top of 2.0's foundation, driven by three things: a fresh GUI implementation, expanded camera support, and **Klipper support** — the ability to drive the laser/stimulus output through the 3D printer's own control board via G-code (`SET_PIN`) instead of wiring a separate Raspberry Pi GPIO output, in preparation for hardware built around Klipper-based printer controllers rather than Marlin-only boards.

See `CHANGELOG.md` for the version history within this repository specifically.
