import os
import wave

import numpy as np


SAMPLE_RATE = 44100


def _write_wav_mono(path: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> str:
    """Write float32/float64 samples in [-1, 1] to a 16-bit mono PCM WAV file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def generate_whoosh(output_path: str, duration: float = 0.45, sample_rate: int = SAMPLE_RATE) -> str:
    """Synthesize a smooth filtered-noise whoosh transition WAV."""
    n = int(round(duration * sample_rate))
    rng = np.random.default_rng(42)
    noise = rng.normal(0.0, 1.0, n)

    # Smooth moving-average low-pass filter to warm the noise
    kernel_size = 35
    kernel = np.hanning(kernel_size)
    kernel /= kernel.sum()
    filtered = np.convolve(noise, kernel, mode="same")

    # Add subtle low-mid airy sweep
    t = np.linspace(0.0, duration, n, endpoint=False)
    freq_sweep = 140.0 + 320.0 * np.sin(np.pi * (t / duration))
    phase = 2.0 * np.pi * np.cumsum(freq_sweep) / sample_rate
    tone = 0.20 * np.sin(phase)

    envelope = np.sin(np.pi * (t / duration)) ** 1.6
    combined = (filtered * 1.4 + tone) * envelope
    peak = np.max(np.abs(combined)) or 1.0
    return _write_wav_mono(output_path, (combined / peak) * 0.75, sample_rate)


def generate_stamp(output_path: str, duration: float = 0.25, sample_rate: int = SAMPLE_RATE) -> str:
    """Synthesize a soft percussive thud + subtle warm chime WAV."""
    n = int(round(duration * sample_rate))
    t = np.linspace(0.0, duration, n, endpoint=False)

    # Soft percussive thud (pitch drop 150 Hz -> 52 Hz)
    thud_freq = 52.0 + 98.0 * np.exp(-t * 28.0)
    thud_phase = 2.0 * np.pi * np.cumsum(thud_freq) / sample_rate
    thud = 0.65 * np.sin(thud_phase) * np.exp(-t * 18.0)

    # Subtle warm chime
    chime = (
        0.25 * np.sin(2.0 * np.pi * 660.0 * t)
        + 0.14 * np.sin(2.0 * np.pi * 990.0 * t)
    ) * np.exp(-t * 14.0)

    attack = np.minimum(1.0, t / 0.006)
    combined = (thud + chime) * attack
    peak = np.max(np.abs(combined)) or 1.0
    return _write_wav_mono(output_path, (combined / peak) * 0.80, sample_rate)


def ensure_sfx_assets(work_dir: str) -> tuple[str, str]:
    """Generate procedural whoosh.wav and stamp.wav in work_dir and return their paths."""
    whoosh_path = os.path.join(work_dir, "whoosh.wav")
    stamp_path = os.path.join(work_dir, "stamp.wav")
    generate_whoosh(whoosh_path)
    generate_stamp(stamp_path)
    return whoosh_path, stamp_path
