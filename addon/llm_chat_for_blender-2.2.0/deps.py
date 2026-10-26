"""Finding, installing and signing in to the Claude Code CLI, on Windows, macOS
and Linux, plus the optional modelling extensions.

Claude Code is not bundled: the Install button runs Anthropic's official
installer (the same one-liner their docs give), and Sign in runs the CLI's own
`claude auth login`, which opens the browser like the VS Code extension does.
"""

import glob
import json
import os
import platform
import ssl
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request

import bpy

WINDOWS = platform.system() == "Windows"
MACOS = platform.system() == "Darwin"

CLAUDE_URL = "https://docs.claude.com/en/docs/claude-code/setup"
INSTALLER_URL = "https://claude.ai/install.ps1" if WINDOWS else "https://claude.ai/install.sh"

# Extra Blender extensions worth having, installed from extensions.blender.org.
EXTRAS = {
    "extra_mesh_objects": "Extra Mesh Objects",
    "looptools": "LoopTools",
    "tissue": "Tissue",
}
BUILTIN_EXTRAS = {"rigify": "Rigify"}


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _candidates(name):
    """Where a CLI tool usually lands, per platform."""
    exe = [name + ext for ext in ((".exe", ".cmd", ".bat") if WINDOWS else ("",))]
    dirs = [_home(".local", "bin")]
    if WINDOWS:
        dirs += [_home("AppData", "Roaming", "npm"),
                 _home("AppData", "Local", "Programs", name),
                 _home("AppData", "Local", "Microsoft", "WinGet", "Links")]
    else:
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", _home(".bun", "bin"),
                 _home(".npm-global", "bin"), _home(".volta", "bin")]
        if MACOS:
            dirs.append(_home("Library", "Application Support", "npm", "bin"))
    return [os.path.join(d, e) for d in dirs for e in exe]


def find_claude():
    """The Claude Code CLI: PATH, the usual install places, then the copy inside
    the VS Code extension (or a VS Code fork's)."""
    for exe in (["claude.exe", "claude.cmd", "claude"] if WINDOWS else ["claude"]):
        found = shutil.which(exe)
        if found:
            return found
    found = next((c for c in _candidates("claude") if os.path.isfile(c)), None)
    if found:
        return found
    patterns = [
        _home(ext, "extensions", "anthropic.claude-code-*", "resources", "native-binary",
              "claude*")
        for ext in (".vscode", ".vscode-server", ".cursor", ".windsurf")
    ] + [_home(".claude", "local", "claude*")]
    found = [p for pat in patterns for p in glob.glob(pat) if os.path.isfile(p)]
    return max(found, key=os.path.getmtime) if found else None


def popen_kwargs():
    """Keep Windows from flashing a console window for every run."""
    if not WINDOWS:
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": info, "creationflags": subprocess.CREATE_NO_WINDOW}


def command_for(path, *args):
    """A .cmd/.bat shim can only be run through the shell interpreter. Callers
    pass only fixed arguments; user text always goes through stdin or a file."""
    if WINDOWS and path.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", path, *args]
    return [path, *args]


def run(cmd, timeout=600):
    """Run a command, return (ok, output)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, **popen_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def online_access():
    return getattr(bpy.app, "online_access", True)


# ---------------------------------------------------------------- Claude Code
def ssl_context():
    """Certificates for https: Blender's bundled certifi when there is one (Python on macOS
    often has no system certificates: "CERTIFICATE_VERIFY_FAILED"), else the system's."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()



def install_claude(report):
    """Download Anthropic's official installer and run it (background thread)."""
    report("Downloading the official Claude Code installer…")
    try:
        with urllib.request.urlopen(INSTALLER_URL, timeout=60, context=ssl_context()) as r:
            script = r.read()
    except OSError as e:
        return report(f"Download failed: {e}")
    fd, path = tempfile.mkstemp(suffix=".ps1" if WINDOWS else ".sh", prefix="claude_install_")
    with os.fdopen(fd, "wb") as f:
        f.write(script)
    report("Installing Claude Code (this can take a few minutes)…")
    try:
        if WINDOWS:
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        else:
            cmd = [shutil.which("bash") or "/bin/bash", path]
        ok, out = run(cmd, timeout=900)
    finally:
        os.remove(path)
    if not ok or not find_claude():
        return report(f"Install failed: {out[-300:]}")
    report("Claude Code is installed. Now sign in.")


def auth_status(claude):
    """(signed_in, account) from `claude auth status`."""
    _, out = run(command_for(claude, "auth", "status"), timeout=30)
    try:
        data = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except ValueError:
        return False, ""
    return bool(data.get("loggedIn")), data.get("email") or data.get("authMethod") or ""


class Login:
    """`claude auth login` in the background. It opens the browser itself when
    it can, and prints the sign-in URL (offered as a button, in case it couldn't); when the browser flow ends on a page with a
    code instead, the user pastes that code back and it goes in on stdin."""

    def __init__(self, claude, report):
        self.report = report
        self.url = ""
        self.waiting_for_code = False
        self.proc = subprocess.Popen(
            command_for(claude, "auth", "login"), text=True, encoding="utf-8",
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            **popen_kwargs())
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        buf = ""
        while True:
            ch = self.proc.stdout.read(1)  # the code prompt has no newline after it
            if not ch:
                break
            buf += ch
            if not self.url:
                m = re.search(r"https://\S+\s", buf)
                if m:
                    self.url = m.group(0).strip()
            if not self.waiting_for_code and buf.rstrip().endswith(">") \
                    and "code" in buf.lower():
                self.waiting_for_code = True
                self.report("Finish signing in in your browser. If it then shows a code, "
                            "paste it below.")
        code = self.proc.wait()
        self.waiting_for_code = False
        last = buf.strip().splitlines()[-1] if buf.strip() else f"exit code {code}"
        self.report("Signed in. You can start chatting." if code == 0 else
                    f"Sign-in didn't finish: {last[-200:]}")

    def send_code(self, code):
        try:
            self.proc.stdin.write(code.strip() + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass

    def cancel(self):
        if self.proc.poll() is None:
            self.proc.terminate()

    @property
    def running(self):
        return self.proc.poll() is None


# ---------------------------------------------------------------- extras

def enable_addon(module):
    """Enable an add-on that is already on disk."""
    if is_enabled(module):
        return True
    for name in (module, f"bl_ext.blender_org.{module}", f"bl_ext.user_default.{module}"):
        try:
            bpy.ops.preferences.addon_enable(module=name)
            return True
        except Exception:  # noqa: BLE001  (not installed under that name)
            continue
    return False


def _blender_org_index():
    repos = list(bpy.context.preferences.extensions.repos)
    return next((i for i, r in enumerate(repos) if r.module == "blender_org"), None)


def install_extras(report):
    """Install the free modelling extensions from extensions.blender.org.
    They keep their own GPL licences; nothing is copied into this add-on."""
    if not online_access():
        return report("Blender's online access is off: turn on "
                      "Preferences > System > Allow Online Access, then try again.")
    index = _blender_org_index()
    if index is None:
        return report("The extensions.blender.org repository is not set up.")
    try:
        bpy.ops.extensions.repo_sync(repo_index=index)
    except Exception as e:  # noqa: BLE001
        report(f"Could not refresh the extensions list: {e}")
    done, failed = [], []
    for pkg, label in EXTRAS.items():
        if is_enabled(pkg):
            continue
        try:
            bpy.ops.extensions.package_install(repo_index=index, pkg_id=pkg,
                                               enable_on_install=True)
            done.append(label)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{label} ({e})")
    for pkg, label in BUILTIN_EXTRAS.items():
        if enable_addon(pkg):
            done.append(label)
    bpy.ops.wm.save_userpref()
    msg = ("Installed: " + ", ".join(done)) if done else "Everything was already installed."
    return report(msg + (f" Failed: {', '.join(failed)}" if failed else ""))


def is_enabled(module):
    keys = bpy.context.preferences.addons.keys()
    return any(k == module or k.endswith("." + module) for k in keys)


def in_thread(fn, report):
    """Run a slow job off the UI thread; report() hops back to the main one."""
    def safe(msg):
        bpy.app.timers.register(lambda: (report(msg), None)[1], first_interval=0.0)

    threading.Thread(target=lambda: fn(safe), daemon=True).start()
