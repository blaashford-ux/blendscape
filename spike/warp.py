# SPDX-License-Identifier: GPL-3.0-or-later
"""Beat-map time warping on top of Rubber Band (R3 engine, offline mode, key-frame/time map).

Two interchangeable backends, both sample-accurate:
  * "cli":    the `rubberband` executable with --timemap (works on Windows/macOS/Linux; set RUBBERBAND=path if not on PATH)
  * "ctypes": the librubberband C API (Linux/macOS with the shared library installed)
pylibrb is not usable: it does not expose set_keyframe_map, and the real-time engine's latency depends on the ratio."""
import ctypes as C
import ctypes.util
import os
import shutil
import subprocess
import tempfile
import numpy as np
import soundfile as sf

try:
    _lib = C.CDLL(ctypes.util.find_library("rubberband") or "librubberband.so.2")
except OSError:
    _lib = None

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

BLOCK = 8192
# RubberBandOption flags (rubberband-c.h): ProcessOffline=0, EngineFiner=0x20000000, WindowShort=0x100000,
# DetectorPercussive=0x400, ChannelsTogether=0x10000000
OPTS = 0x20000000 | 0x100000 | 0x400 | 0x10000000


def _cli_path():
    return os.environ.get("RUBBERBAND") or shutil.which("rubberband") or shutil.which("rubberband.exe")


def backend():
    if _cli_path():
        return "cli"
    if _lib is not None:
        return "ctypes"
    raise RuntimeError("Rubber Band not found: put the `rubberband` executable on PATH (or set RUBBERBAND=path), "
                       "or install librubberband.")


def _prepare(audio, src_pts, dst_pts):
    """Shared key-frame preparation. Returns (x (ch,n) float32, keys list, targets list, lead samples)."""
    x = np.ascontiguousarray(audio.T.astype(np.float32))
    n = x.shape[1]
    src = [int(round(s)) for s in src_pts]
    dst = [int(round(d)) for d in dst_pts]
    shift = dst[0] - src[0]
    lead, cut = max(0, shift), max(0, -shift)
    if cut:
        x = np.ascontiguousarray(x[:, cut:])
        src = [s - cut for s in src]
        n = x.shape[1]
    kf = {0: 0}
    for s, d in zip(src, dst):
        kf[s] = d - lead
    kf[n] = kf[src[-1]] + (n - src[-1])
    kf = dict(sorted((k, v) for k, v in kf.items() if 0 <= k <= n))
    keys = []
    for k in sorted(kf):
        if not keys or kf[k] > kf[keys[-1]]:
            keys.append(k)
    return x, keys, [kf[k] for k in keys], lead


def _warp_cli(x, sr, keys, tos):
    with tempfile.TemporaryDirectory() as td:
        fin, fout, fmap = (os.path.join(td, n) for n in ("in.wav", "out.wav", "map.txt"))
        sf.write(fin, x.T, sr, subtype="FLOAT")
        with open(fmap, "w") as f:
            f.write("".join(f"{k} {t}\n" for k, t in zip(keys, tos)))
        ratio = repr(tos[-1] / keys[-1])  # overall output/input duration; the time map shapes it
        subprocess.run([_cli_path(), "-3", "-q", "-t", ratio, "-M", fmap, fin, fout], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        y, _ = sf.read(fout, dtype="float32", always_2d=True)
    return y


def warp(audio, sr, src_pts, dst_pts):
    """Warp audio (n, ch) so source sample src_pts[i] lands on output sample dst_pts[i]
    (increasing; output index 0 == dst coordinate 0). Ratio is 1 outside the given points. Returns (m, ch) float32."""
    x, keys, tos, lead = _prepare(audio, src_pts, dst_pts)
    ch, n = x.shape
    if backend() == "cli":
        y = _warp_cli(x, sr, keys, tos)
    else:
        y = _warp_ctypes(x, sr, keys, tos)
    if lead:
        y = np.concatenate([np.zeros((lead, ch), np.float32), y])
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
    st = _lib.rubberband_new(sr, ch, OPTS, tos[-1] / n, 1.0)
    try:
        _lib.rubberband_set_expected_input_duration(st, n)
        _lib.rubberband_set_key_frame_map(st, len(keys), frm, to)
        for p in range(0, n, BLOCK):
            e = min(n, p + BLOCK)
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

        for p in range(0, n, BLOCK):
            e = min(n, p + BLOCK)
            _lib.rubberband_process(st, _ptrs(x, p, e), e - p, int(e >= n))
            drain()
        drain()
    finally:
        _lib.rubberband_delete(st)
    return np.concatenate(outs, axis=1).T
