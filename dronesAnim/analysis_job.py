"""Scene snapshots and subprocess jobs for motion analysis."""

import json
import os
import pickle
import subprocess
import tempfile
from pathlib import Path

import bpy

from .analysis_math import MotionAnalysis, MotionPlot


class AnalysisJob:
    def __init__(self, scene, view_layer, names):
        base = Path(bpy.context.preferences.filepaths.temporary_directory or bpy.app.tempdir)
        self._temporary = tempfile.TemporaryDirectory(prefix="drone-motion-", dir=base)
        self.directory = Path(self._temporary.name)
        self.process = None
        self._log = None
        self._progress = 0.0
        try:
            snapshot = self.directory / "scene.blend"
            # Write a copy without changing the open file, frame or save state.
            bpy.data.libraries.write(str(snapshot), {scene}, path_remap="ABSOLUTE")
            request = {
                "snapshot": str(snapshot), "scene": scene.name,
                "view_layer": view_layer.name, "names": list(names),
                "start": scene.frame_start, "end": scene.frame_end,
                "fps": scene.render.fps / scene.render.fps_base,
            }
            (self.directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
            self._launch()
        except Exception:
            self.close()
            raise

    def _launch(self):
        worker = Path(__file__).with_name("analysis_worker.py")
        command = [
            bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
            "--python-exit-code", "1", "--python", str(worker),
            "--", str(self.directory / "request.json"),
        ]
        environment = os.environ.copy()
        environment.update(
            PYTHONDONTWRITEBYTECODE="1",
            BLENDER_USER_RESOURCES=str(self.directory / "profile"),
            XDG_CACHE_HOME=str(self.directory / "cache"),
        )
        self._log = (self.directory / "worker.log").open("wb")
        self.process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def poll(self):
        """Read progress; load the result only after the worker has exited."""
        code = self.process.poll()
        status_path = self.directory / "progress.json"
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        self._progress = max(self._progress, status.get("progress", 0.0))
        if code is None:
            return self._progress, None
        if code != 0 or status.get("state") != "done":
            error = status.get("error")
            if not error:
                with (self.directory / "worker.log").open("rb") as stream:
                    stream.seek(max(0, stream.seek(0, 2) - 2000))
                    error = stream.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(error or f"分析进程异常退出（{code}）")
        # This file is produced only by our worker in the private job directory.
        with (self.directory / "result.bin").open("rb") as stream:
            data = pickle.load(stream)
        plots = tuple(MotionPlot(*plot) for plot in data[4])
        result = MotionAnalysis(*data[:4], plots, *data[5:])
        return 1.0, result

    def close(self):
        """Stop any worker and remove its private snapshot and IPC files."""
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None
            self._temporary.cleanup()
