"""Getting an image out of the system clipboard.

Blender's own image.clipboard_paste covers Windows, macOS and Wayland, but reads
nothing on X11, so this adds: a copied file path (works everywhere), the usual
command line clipboard tools, and, as a last resort, a direct X11 read through
libX11 with ctypes.
"""

import ctypes
import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.parse

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"BM", b"GIF8", b"II*\x00", b"MM\x00*")


def path_from_text(text):
    """A file manager copy puts a path or file:// URI on the clipboard."""
    text = (text or "").strip().strip('"').splitlines()[0] if text else ""
    if text.startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
    if (text and len(text) < 4096 and text.lower().endswith(IMAGE_EXTS)
            and os.path.isfile(text)):
        return text
    return None


def from_tools():
    """wl-paste, xclip or xsel, if any of them is installed."""
    tools = [
        ["wl-paste", "--no-newline", "--type", "image/png"],
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        ["xsel", "--clipboard", "--output"],
    ]
    for cmd in tools:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.startswith(MAGIC):
            return r.stdout
    return None


# ---------------------------------------------------------------- X11

_SELECTION_NOTIFY, _PROPERTY_NOTIFY = 31, 28
_PROPERTY_NEW_VALUE = 0
_PROPERTY_CHANGE_MASK = 1 << 22


class _XPropertyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("atom", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("state", ctypes.c_int)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("prop", _XPropertyEvent),
                ("pad", ctypes.c_long * 24)]


def _x11_lib():
    """Every function gets argtypes: without them ctypes passes pointers as C int,
    which truncates them on 64-bit and segfaults the process."""
    try:
        lib = ctypes.CDLL("libX11.so.6")
    except OSError:
        return None
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
    lib.XConvertSelection.argtypes = [ctypes.c_void_p] + [ctypes.c_ulong] * 4 + \
        [ctypes.c_ulong]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XPending.argtypes = [ctypes.c_void_p]
    lib.XPending.restype = ctypes.c_int
    lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XCreateSimpleWindow.restype = ctypes.c_ulong
    # display, parent, x, y, width, height, border_width, border, background
    lib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                        ctypes.c_ulong, ctypes.c_ulong]
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
        ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    return lib


def _read_property(lib, display, window, prop, delete=True):
    """Returns (bytes, type_atom) for a window property."""
    actual_type = ctypes.c_ulong()
    actual_format = ctypes.c_int()
    nitems = ctypes.c_ulong()
    remaining = ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    lib.XGetWindowProperty(display, window, prop, 0, 0x7FFFFFFF, int(delete), 0,
                           ctypes.byref(actual_type), ctypes.byref(actual_format),
                           ctypes.byref(nitems), ctypes.byref(remaining),
                           ctypes.byref(data))
    if not data:
        return b"", actual_type.value
    width = max(1, actual_format.value // 8)
    size = nitems.value * (width if width != 8 else ctypes.sizeof(ctypes.c_ulong))
    out = bytes(bytearray(data[:size]))
    lib.XFree(ctypes.cast(data, ctypes.c_void_p))
    return out, actual_type.value


def from_x11(timeout=3.0):
    """Ask the clipboard owner for image/png directly, INCR transfers included."""
    if sys.platform != "linux" or not os.environ.get("DISPLAY"):
        return None
    lib = _x11_lib()
    if not lib:
        return None
    display = lib.XOpenDisplay(None)
    if not display:
        return None
    try:
        clipboard = lib.XInternAtom(display, b"CLIPBOARD", False)
        incr = lib.XInternAtom(display, b"INCR", False)
        prop = lib.XInternAtom(display, b"BLENDER_CLAUDE_CHAT", False)
        root = lib.XDefaultRootWindow(display)
        window = lib.XCreateSimpleWindow(display, root, 0, 0, 1, 1, 0, 0, 0)
        lib.XSelectInput(display, window, _PROPERTY_CHANGE_MASK)
        for name in (b"image/png", b"image/jpeg", b"image/bmp"):
            target = lib.XInternAtom(display, name, False)
            lib.XConvertSelection(display, clipboard, target, prop, window, 0)
            lib.XFlush(display)
            event = _XEvent()
            deadline = time.time() + timeout
            while time.time() < deadline:
                if lib.XPending(display):
                    lib.XNextEvent(display, ctypes.byref(event))
                    if event.type == _SELECTION_NOTIFY:
                        break
                else:
                    time.sleep(0.01)
            else:
                continue  # owner never answered; try the next target
            data, kind = _read_property(lib, display, window, prop)
            if kind == incr:
                data = _read_incr(lib, display, window, prop, timeout)
            if data.startswith(MAGIC):
                return data
        return None
    finally:
        try:
            lib.XDestroyWindow(display, window)
        except Exception:  # noqa: BLE001
            pass
        lib.XCloseDisplay(display)


def _read_incr(lib, display, window, prop, timeout):
    """Large selections arrive in chunks, each announced by a PropertyNotify."""
    chunks = []
    event = _XEvent()
    deadline = time.time() + timeout * 4
    while time.time() < deadline:
        if not lib.XPending(display):
            time.sleep(0.01)
            continue
        lib.XNextEvent(display, ctypes.byref(event))
        if event.type != _PROPERTY_NOTIFY or event.prop.state != _PROPERTY_NEW_VALUE:
            continue
        chunk, _ = _read_property(lib, display, window, prop)
        if not chunk:
            break  # zero-length chunk ends the transfer
        chunks.append(chunk)
    return b"".join(chunks)


def native_works():
    """Blender's own image.clipboard_paste reads nothing on X11, but is the best
    source on Windows, macOS and Wayland."""
    if sys.platform != "linux":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _python_exe():
    """A Python to run this file in: Blender's bundled one, or the system's."""
    # Blender's bundled interpreter is versioned, e.g. .../python/bin/python3.13
    candidates = sorted(glob.glob(os.path.join(sys.prefix, "bin", "python3*")))
    candidates += glob.glob(os.path.join(sys.prefix, "bin", "python.exe"))
    candidates += glob.glob(os.path.join(sys.prefix, "python.exe"))
    found = next((c for c in candidates if os.path.isfile(c) and os.access(c, os.X_OK)), None)
    return found or shutil.which("python3") or shutil.which("python")


def x11_via_subprocess(folder, timeout=20):
    """Read the X11 clipboard in a child process. Talking to X11 inside Blender
    risks taking Blender down with it, so it never happens in-process."""
    python = _python_exe()
    if not python or sys.platform != "linux":
        return None
    try:
        r = subprocess.run([python, os.path.abspath(__file__), folder],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    path = (r.stdout or "").strip()
    return path if path and os.path.isfile(path) else None


def image_bytes():
    """Clipboard image as bytes, in-process. X11 is handled separately."""
    return from_tools()


def save(data, folder, stem="paste"):
    ext = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp",
           b"BM": ".bmp", b"GIF8": ".gif"}
    suffix = next((v for k, v in ext.items() if data.startswith(k)), ".png")
    path = os.path.join(folder, f"{stem}_{int(time.time())}{suffix}")
    with open(path, "wb") as f:
        f.write(data)
    return path


if __name__ == "__main__":
    # Run as a child process: `python3 clipboard.py <folder>` prints the saved path.
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    blob = from_tools() or from_x11()
    if blob:
        print(save(blob, out_dir))
    else:
        sys.exit(1)
