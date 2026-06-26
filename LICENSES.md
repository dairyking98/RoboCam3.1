# Dependency Licenses

A summary of all third-party dependencies used in RoboCam 3.1 and their licenses.

## Open Source Dependencies

| Library | License | Notes |
|---|---|---|
| [numpy](https://numpy.org/) | BSD 3-Clause | Permissive |
| [opencv-python](https://github.com/opencv/opencv-python) (`cv2`) | Apache 2.0 | Permissive |
| [PySide6](https://wiki.qt.io/Qt_for_Python) | **LGPL v3** | See note below |
| [pyserial](https://github.com/pyserial/pyserial) (`serial`) | BSD 3-Clause | Permissive |
| [requests](https://requests.readthedocs.io/) | Apache 2.0 | Permissive |
| [picamera2](https://github.com/raspberrypi/picamera2) | BSD 2-Clause | Permissive |
| Python standard library | PSF License | Permissive |

## Proprietary Components

| Component | Source | Notes |
|---|---|---|
| PlayerOne Camera SDK | [player-one-astronomy.com](https://player-one-astronomy.com/) | Proprietary C library (`.so`) with Python wrapper. Free to use with PlayerOne hardware. Not open source — excluded from or linked externally in any public distribution. |

---

## Notes

### PySide6 (LGPL v3)

PySide6 is the official Python binding for Qt, licensed under the GNU Lesser General Public License v3.

- **Internal / source distribution**: No restrictions. LGPL is fully compatible with private or open-source use.
- **Binary distribution** (e.g., packaged with PyInstaller): LGPL requires that end users can replace the PySide6 shared library. This means static linking into a closed binary is not permitted. Distributing the source or dynamically linking sidesteps this entirely.
- RoboCam is distributed as source, so there are no LGPL compliance concerns in the current setup.

### PlayerOne Camera SDK

The SDK is a proprietary shared library (`libPlayerOneCamera.so`) provided by Player One Astronomy. It is free to use with their hardware but is not open source and may not be redistributed independently. The SDK directory (`PlayerOne_Camera_SDK_Linux_V3.10.0/`) should be excluded from any public repository or obtained directly from the manufacturer.
