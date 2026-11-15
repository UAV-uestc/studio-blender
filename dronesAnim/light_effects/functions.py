"""Opt-in loading and adapters for custom light-effect functions.

The calling conventions are adapted from Skybrush Studio (GPL-3.0-or-later).
Approved Python runs with full Blender process privileges and is not sandboxed.
"""

import ast
import hashlib
import inspect
from pathlib import Path
from types import ModuleType

import numpy as np


class FunctionError(RuntimeError):
    pass


class TrustError(FunctionError):
    pass


def discover_functions(path):
    """List top-level function declarations without executing the file."""
    resolved = Path(path).expanduser().resolve()
    tree = ast.parse(resolved.read_bytes(), filename=str(resolved))
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]


class TrustedFunctions:
    """Session-only consent and content-verified module cache."""

    def __init__(self):
        self._approved = {}
        self._modules = {}

    @staticmethod
    def _read(path):
        resolved = Path(path).expanduser().resolve(strict=True)
        data = resolved.read_bytes()
        return resolved, data, hashlib.sha256(data).digest()

    def approve(self, path):
        resolved, _data, digest = self._read(path)
        self._approved[resolved] = digest
        self._modules.pop(resolved, None)

    def revoke(self, path):
        resolved = Path(path).expanduser().resolve()
        self._approved.pop(resolved, None)
        self._modules.pop(resolved, None)

    def clear(self):
        self._approved.clear()
        self._modules.clear()

    def is_trusted(self, path):
        try:
            resolved, _data, digest = self._read(path)
            return self._approved.get(resolved) == digest
        except OSError:
            return False

    def load(self, path, name):
        try:
            resolved, data, digest = self._read(path)
        except OSError as exc:
            raise FunctionError(f"Cannot read custom function file: {exc}") from exc
        if self._approved.get(resolved) != digest:
            self._modules.pop(resolved, None)
            raise TrustError(f"Custom function file requires approval: {resolved}")
        module = self._modules.get(resolved)
        if module is None:
            module = ModuleType(f"_droneanim_custom_{digest.hex()}")
            module.__file__ = str(resolved)
            try:
                exec(compile(data, str(resolved), "exec"), module.__dict__)
            except Exception as exc:
                raise FunctionError(f"Cannot load custom function file {resolved}: {exc}") from exc
            self._modules[resolved] = module
        result = getattr(module, name, None)
        if not callable(result):
            raise FunctionError(f"Custom function {name!r} not found in {resolved}")
        return result


def _validate_signature(function, version):
    signature = inspect.signature(function)
    if version == 1:
        signature.bind(
            frame=0,
            time_fraction=0,
            drone_index=0,
            formation_index=0,
            position=(0, 0, 0),
            drone_count=1,
        )
    elif version == 2:
        signature.bind(None, None, 0, out=None)
    else:
        raise ValueError(f"Unsupported custom function version: {version}")


def color_function_version(function):
    """Use the source convention for distinguishing legacy and vectorized colors."""
    return 1 if len(inspect.signature(function).parameters) == 6 else 2


def _legacy_args(effect, context, frame, index):
    mapping = context.mapping
    return {
        "frame": frame,
        "time_fraction": effect.get_time_fraction_for_frame(frame),
        "drone_index": index,
        "formation_index": mapping[index] if mapping is not None else None,
        "position": tuple(context.positions.as_array[index]),
        "drone_count": context.num_drones,
    }


def evaluate_custom(function, effect, context, frame, *, version, color):
    """Evaluate an adapter into a fresh array, isolating failures from the stack."""
    shape = (context.num_drones, 4) if color else (context.num_drones,)
    output = np.zeros(shape, dtype=np.float32)
    try:
        _validate_signature(function, version)
        if version == 1:
            indices = context.active_drones if color else range(context.num_drones)
            for index in indices:
                output[index] = function(**_legacy_args(effect, context, frame, index))
        else:
            common = function(effect, context, frame, out=output)
            if common is not None:
                if color:
                    output[context.active_drones] = common
                else:
                    output[:] = common
    except Exception as exc:
        raise FunctionError(f"Effect {effect.name!r}: custom function failed: {exc}") from exc
    return output
