# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
import soundfile as sf

from blendscape.audio import AudioError, load_audio, resample, write_audio


def test_mono_is_duplicated_to_stereo(tmp_path):
    p = tmp_path / "m.wav"
    sf.write(p, np.linspace(-0.5, 0.5, 1000, dtype=np.float32), 44100, subtype="FLOAT")
    x, sr = load_audio(p)
    assert x.shape == (1000, 2) and x.dtype == np.float32
    np.testing.assert_array_equal(x[:, 0], x[:, 1])


def test_extra_channels_are_dropped(tmp_path):
    p = tmp_path / "s.wav"
    sf.write(p, np.zeros((500, 6), np.float32), 44100)
    assert load_audio(p)[0].shape == (500, 2)


def test_resampling_changes_length_and_rate(tmp_path):
    p = tmp_path / "r.wav"
    t = np.arange(48000) / 48000
    sf.write(p, np.sin(2 * np.pi * 440 * t).astype(np.float32), 48000, subtype="FLOAT")
    x, sr = load_audio(p, 44100)
    assert sr == 44100 and abs(len(x) - 44100) <= 1
    # the tone is still 440 Hz
    spec = np.abs(np.fft.rfft(x[:, 0]))
    assert abs(np.argmax(spec) * 44100 / len(x) - 440) < 2


def test_resample_noop_ratio_keeps_samples():
    x = np.random.default_rng(0).standard_normal((1000, 2)).astype(np.float32)
    np.testing.assert_allclose(resample(x, 44100, 44100), x, atol=1e-6)


@pytest.mark.parametrize("depth, subtype", [(16, "PCM_16"), (24, "PCM_24"), (32, "FLOAT")])
def test_write_bit_depths_and_creates_directories(tmp_path, depth, subtype):
    p = tmp_path / "new" / "dir" / "o.wav"
    write_audio(p, np.zeros((100, 2), np.float32), 44100, depth)
    assert sf.info(p).subtype == subtype


def test_errors_are_audio_errors(tmp_path):
    with pytest.raises(AudioError, match="cannot read"):
        load_audio(tmp_path / "missing.wav")
    with pytest.raises(AudioError, match="bit depth"):
        write_audio(tmp_path / "x.wav", np.zeros((10, 2), np.float32), 44100, 8)
    empty = tmp_path / "empty.wav"
    sf.write(empty, np.zeros((0, 2), np.float32), 44100)
    with pytest.raises(AudioError, match="no audio"):
        load_audio(empty)
