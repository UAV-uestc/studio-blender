"""Endpoint-inclusive animation sampling on the scene timeline."""

from dataclasses import dataclass
from enum import Enum
from math import ceil, isfinite, ulp


class SamplingMode(str, Enum):
    SCENE = "SCENE"              # 场景帧率：制作前设置场景帧率，导出时逐整数帧采样，不另设采样帧率。
    UNIFORM = "UNIFORM"          # 全等间隔：取 n=round(t*k) 个区间并均分首尾范围，允许子帧；实际帧率 k'=n/t。
    END_ALIGNED = "END_ALIGNED"  # 固定帧率：按 1/k 秒固定间隔采样，末帧对齐右边界（末段可能不足 1/k 秒）。


@dataclass(frozen=True)
class SamplingPlan:
    start: int
    end: int
    scene_fps: float
    mode: SamplingMode
    target_fps: float
    intervals: int
    fps: float
    frame_step: float

    @property
    def count(self):
        return self.intervals + 1

    @property
    def duration(self):
        return (self.end - self.start) / self.scene_fps

    def frame_at(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        if index == self.intervals:
            return self.end
        return self.start + index * self.frame_step

    @property
    def last_interval(self):
        if not self.intervals:
            return None
        return (self.end - self.frame_at(self.intervals - 1)) / self.scene_fps

    def __iter__(self):
        for index in range(self.count):
            yield self.frame_at(index)


def sampling_plan(start, end, mode=SamplingMode.SCENE, target_fps=24.0):
    """Build a plan using the current Blender scene's frame rate."""
    import bpy

    scene = bpy.context.scene
    scene_fps = scene.render.fps / scene.render.fps_base
    mode = SamplingMode(mode)
    if end < start:
        raise ValueError("终止帧不能小于起始帧")
    if not isfinite(scene_fps) or scene_fps <= 0:
        raise ValueError("场景帧率必须为正数")
    duration = (end - start) / scene_fps

    if mode == SamplingMode.SCENE:
        intervals = end - start
        fps = scene_fps
        frame_step = 1.0
    elif mode == SamplingMode.UNIFORM:
        if not isfinite(target_fps) or target_fps <= 0:
            raise ValueError("采样帧率必须为正数")
        requested = duration * target_fps
        if not isfinite(requested):
            raise ValueError("采样数量超出范围")
        # Python round uses ties to even; retain both endpoints for short ranges.
        intervals = max(1, round(requested)) if end > start else 0
        fps = intervals / duration if intervals else target_fps
        frame_step = (end - start) / intervals if intervals else 0.0
    elif mode == SamplingMode.END_ALIGNED:
        if not isfinite(target_fps) or target_fps <= 0:
            raise ValueError("采样帧率必须为正数")
        requested = duration * target_fps
        if not isfinite(requested):
            raise ValueError("采样数量超出范围")
        nearest = round(requested)
        if nearest > 0 and abs(requested - nearest) <= 8 * ulp(requested):
            requested = nearest
        intervals = max(1, ceil(requested)) if end > start else 0
        fps = target_fps
        frame_step = scene_fps / target_fps
    return SamplingPlan(start, end, scene_fps, mode, target_fps, intervals, fps, frame_step)
