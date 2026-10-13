# SPDX-License-Identifier: GPL-3.0-or-later
"""Beat-map time warping with Rubber Band (R3 engine, offline mode, time map).

Two interchangeable, sample-accurate backends:
  * "cli":    the ``rubberband`` executable and its ``--timemap`` option. Works everywhere; put it on PATH or
              set ``RUBBERBAND=/path/to/rubberband``.
  * "ctypes": the librubberband C API (Linux/macOS with the shared library installed).
The real-time engine is not used: its latency depends on the current ratio, so beats cannot be placed exactly.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import shutil
import subprocess
import tempfile

import numpy as np
import soundfile as sf


class StretchError(RuntimeError):
    pass


def _load_lib():
    try:
        return C.CDLL(ctypes.util.find_library("rubberband") or "librubberband.so.2")
    except OSError:
        return None


_lib = _load_lib()

if _lib is not None:
    _lib.rubberband_new.restype = C.c_void_p
    _lib.rubberband_new.argtypes = [C.c_uint, C.c_uint, C.c_int, C.c_double, C.c_double]
    _lib.rubberband_available.argtypes = [C.c_void_p]
    _lib.rubberband_available.restype = C.c_int
    _lib.rubberband_retrieve.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint]
    _lib.rubberband_retrieve.restype = C.c_uint
    _lib.rubberband_study.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint, C.c_int]
    _lib.rubberband_process.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint, C.c_int]
    _lib.rubberband_set_expected_input_duration.argtypes = [C.c_void_p, C.c_uint]
    _lib.rubberband_set_key_frame_map.argtypes = [C.c_void_p, C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
    _lib.rubberband_delete.argtypes = [C.c_void_p]

_BLOCK = 8192
# RubberBandOption flags (rubberband-c.h): ProcessOffline=0, EngineFiner, WindowShort,
# DetectorPercussive, ChannelsTogether
_OPTS = 0x20000000 | 0x100000 | 0x400 | 0x10000000


def _cli_path() -> str | None:
    return os.environ.get("RUBBERBAND") or shutil.which("rubberband") or shutil.which("rubberband.exe")


def backend() -> str:
    if _cli_path():
        return "cli"
    if _lib is not None:
        return "ctypes"
    raise StretchError("Rubber Band not found. Install the `rubberband` command-line tool and put it on PATH "
                       "(or set RUBBERBAND=/path/to/rubberband), or install librubberband.")


def available() -> bool:
    try:
        backend()
        return True
    except StretchError:
        return False


def _keyframes(n: int, src_pts, dst_pts):
    """Source/target keyframes (ints), output-relative. Returns (keys, targets, d0) where d0 is the master
    sample that output index 0 corresponds to."""
    src = [int(round(s)) for s in src_pts]
    dst = [int(round(d)) for d in dst_pts]
    d0 = dst[0] - src[0]  # unit slope before the first anchor
    keys, tos = [], []
    if src[0] > 0:
        keys.append(0)
        tos.append(0)
    for s, d in zip(src, dst, strict=True):
        t = d - d0
        if s > n or (keys and (s <= keys[-1] or t <= tos[-1])):
            continue  # drop anchors that would make the map non-monotonic
        keys.append(s)
        tos.append(t)
    if n > keys[-1]:
        keys.append(n)
        tos.append(tos[-1] + (n - keys[-2]))
    return keys, tos, d0


def warp(audio: np.ndarray, sr: int, src_pts, dst_pts) -> tuple[np.ndarray, int]:
    """Warp ``audio`` (n, ch) so local source sample ``src_pts[i]`` lands on master sample ``dst_pts[i]``.

    Source positions are local to ``audio``; destination positions are on the absolute master clock.
    Slope is 1 outside the anchors. With no anchors the audio is just placed at ``dst_pts`` semantics-free
    (use :func:`place`). Returns ``(warped (m, ch) float32, start)`` where ``start`` is the master sample of
    ``warped[0]``."""
    n = audio.shape[0]
    keys, tos, d0 = _keyframes(n, src_pts, dst_pts)
    x = np.ascontiguousarray(audio.T.astype(np.float32))
    if backend() == "cli":
        y = _warp_cli(x, sr, keys, tos)
    else:
        y = _warp_ctypes(x, sr, keys, tos)
    return y, d0


def _warp_cli(x, sr, keys, tos):
    with tempfile.TemporaryDirectory() as td:
        fin, fout, fmap = (os.path.join(td, n) for n in ("in.wav", "out.wav", "map.txt"))
        sf.write(fin, x.T, sr, subtype="FLOAT")
        with open(fmap, "w") as f:
            f.write("".join(f"{k} {t}\n" for k, t in zip(keys, tos, strict=True)))
        ratio = repr(tos[-1] / keys[-1])  # overall output/input duration; the time map shapes it
        cmd = [_cli_path(), "-3", "-q", "-t", ratio, "-M", fmap, fin, fout]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(fout):
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
            raise StretchError(f"rubberband failed (exit {r.returncode}): {' | '.join(tail)}")
        y, _ = sf.read(fout, dtype="float32", always_2d=True)
    return y


def _ptrs(planar, a, b):
    arr = (C.POINTER(C.c_float) * planar.shape[0])()
    for c in range(planar.shape[0]):
        arr[c] = planar[c, a:b].ctypes.data_as(C.POINTER(C.c_float))
    return arr


def _warp_ctypes(x, sr, keys, tos):
    ch, n = x.shape
    frm = (C.c_uint * len(keys))(*keys)
    to = (C.c_uint * len(keys))(*tos)
    st = _lib.rubberband_new(sr, ch, _OPTS, tos[-1] / n, 1.0)
    try:
        _lib.rubberband_set_expected_input_duration(st, n)
        _lib.rubberband_set_key_frame_map(st, len(keys), frm, to)
        for p in range(0, n, _BLOCK):
            e = min(n, p + _BLOCK)
            _lib.rubberband_study(st, _ptrs(x, p, e), e - p, int(e >= n))
        outs = []

        def drain():
            while True:
                a = _lib.rubberband_available(st)
                if a <= 0:
                    break
                buf = np.zeros((ch, a), np.float32)
                _lib.rubberband_retrieve(st, _ptrs(buf, 0, a), a)
                outs.append(buf)

        for p in range(0, n, _BLOCK):
            e = min(n, p + _BLOCK)
            _lib.rubberband_process(st, _ptrs(x, p, e), e - p, int(e >= n))
            drain()
        drain()
    finally:
        _lib.rubberband_delete(st)
    return np.concatenate(outs, axis=1).T
