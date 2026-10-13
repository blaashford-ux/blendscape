# SPDX-License-Identifier: GPL-3.0-or-later
"""Beat-map time warping on top of Rubber Band's C API (via ctypes, offline engine + key-frame map).

pylibrb does not expose set_keyframe_map (missing std::map binding), and the real-time engine's
latency depends on the current ratio, so we call librubberband directly. Offline mode with a
key-frame map places source anchors at exact output samples."""
import ctypes as C
import ctypes.util
import numpy as np
from pylibrb import Option

_lib = C.CDLL(ctypes.util.find_library("rubberband") or "librubberband.so.2")
_lib.rubberband_new.restype = C.c_void_p
_lib.rubberband_new.argtypes = [C.c_uint, C.c_uint, C.c_int, C.c_double, C.c_double]
for _n in ("delete", "study", "process", "retrieve", "available", "set_expected_input_duration",
           "set_key_frame_map", "get_samples_required", "set_max_process_size"):
    getattr(_lib, "rubberband_" + _n)
_lib.rubberband_available.argtypes = [C.c_void_p]
_lib.rubberband_available.restype = C.c_int
_lib.rubberband_retrieve.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint]
_lib.rubberband_retrieve.restype = C.c_uint
_lib.rubberband_study.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint, C.c_int]
_lib.rubberband_process.argtypes = [C.c_void_p, C.POINTER(C.POINTER(C.c_float)), C.c_uint, C.c_int]
_lib.rubberband_set_expected_input_duration.argtypes = [C.c_void_p, C.c_uint]
_lib.rubberband_set_key_frame_map.argtypes = [C.c_void_p, C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
_lib.rubberband_delete.argtypes = [C.c_void_p]
_lib.rubberband_set_time_ratio.argtypes = [C.c_void_p, C.c_double]

BLOCK = 8192
OPTS = int(Option.PROCESS_OFFLINE | Option.ENGINE_FINER | Option.TRANSIENTS_CRISP | Option.DETECTOR_PERCUSSIVE |
           Option.WINDOW_SHORT | Option.CHANNELS_TOGETHER)


def _ptrs(planar, a, b):
    arr = (C.POINTER(C.c_float) * planar.shape[0])()
    for c in range(planar.shape[0]):
        arr[c] = planar[c, a:b].ctypes.data_as(C.POINTER(C.c_float))
    return arr


def warp(audio, sr, src_pts, dst_pts):
    """Warp audio (n, ch) so source sample src_pts[i] lands on output sample dst_pts[i]
    (increasing; both in samples, output index 0 == dst coordinate 0). Outside the given points the
    ratio is 1. Returns (m, ch) float32."""
    x = np.ascontiguousarray(audio.T.astype(np.float32))
    ch, n = x.shape
    src = [int(round(s)) for s in src_pts]
    dst = [int(round(d)) for d in dst_pts]
    # source samples that land before output 0 are dropped; if the first anchor lands later, lead with silence
    shift = dst[0] - src[0]
    lead, cut = max(0, shift), max(0, -shift)
    if cut:
        x = np.ascontiguousarray(x[:, cut:])
        src = [s - cut for s in src]
        dst = [d - 0 for d in dst]
        n = x.shape[1]
    # key frames are in (trimmed input, output-without-lead) coordinates
    off = lead
    kf = {0: 0}
    for s, d in zip(src, dst):
        kf[s] = d - off
    kf[n] = kf[src[-1]] + (n - src[-1])
    kf = dict(sorted((k, v) for k, v in kf.items() if 0 <= k <= n))
    keys = sorted(kf)
    # drop non-monotonic maps (defensive)
    ok = [keys[0]]
    for k in keys[1:]:
        if kf[k] > kf[ok[-1]]:
            ok.append(k)
    keys = ok
    frm = (C.c_uint * len(keys))(*keys)
    to = (C.c_uint * len(keys))(*[kf[k] for k in keys])
    total_out = kf[keys[-1]]

    st = _lib.rubberband_new(sr, ch, OPTS, total_out / n, 1.0)
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
    y = np.concatenate(outs, axis=1).T
    if lead:
        y = np.concatenate([np.zeros((lead, ch), np.float32), y])
    return y
