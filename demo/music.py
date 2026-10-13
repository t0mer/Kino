#!/usr/bin/env python3
"""Synthesise the demo's background music bed (original, in-house, no samples).

A soft, upbeat instrumental loop: 100 BPM in D major, I-V-vi-IV
(D, A, Bm, G), a gentle plucked arpeggio, a warm pad and a soft bass pulse on
every beat. No percussion, no vocals, no downloaded material. The loop is
rendered with its note tails wrapped around to the start, so tiling it is
seamless; it is then tiled and trimmed to the requested length.

Usage: python demo/music.py --seconds 147.8 --out music.wav
"""
import argparse
import wave
from pathlib import Path

import numpy as np

SR = 48000
BPM = 100
SEED = 1009
BEAT = 60.0 / BPM
BAR = 4 * BEAT
# MIDI notes, D major: I-V-vi-IV, one chord per bar, eight bars per loop.
CHORDS = [
    [50, 54, 57, 62],  # D
    [45, 49, 52, 57],  # A
    [47, 50, 54, 59],  # Bm
    [43, 47, 50, 55],  # G
]
# Arpeggio pattern in eighth notes (index into chord tones + octave flag).
PATTERNS = [
    [(0, 1), (1, 1), (2, 1), (3, 1), (2, 1), (1, 1), (2, 1), (3, 1)],
    [(0, 1), (2, 1), (3, 1), (1, 2), (3, 1), (2, 1), (1, 1), (2, 1)],
]
TARGET_RMS_DB = -32.0  # bed level before ducking; speech clips sit around -22 dBFS


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def pluck(freq: float, dur: float, vel: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    env = (1 - np.exp(-t / 0.004)) * np.exp(-t / 0.35)
    # Few soft harmonics, upper ones decaying faster: a muted, plucked tone.
    tone = (np.sin(2 * np.pi * freq * t)
            + 0.35 * np.exp(-t / 0.15) * np.sin(2 * np.pi * 2 * freq * t)
            + 0.12 * np.exp(-t / 0.08) * np.sin(2 * np.pi * 3 * freq * t))
    return vel * env * tone


def pad(freqs: list, dur: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    att, rel = 0.6, 0.8
    env = np.clip(t / att, 0, 1) * np.clip((dur - t) / rel + 0.0, 0, 1) ** 0.5
    out = np.zeros_like(t)
    for f in freqs:
        for detune in (-0.06, 0.06):  # slight chorus
            ff = f * 2 ** (detune / 12)
            out += np.sin(2 * np.pi * ff * t) + 0.18 * np.sin(2 * np.pi * 2 * ff * t)
    return env * out / (2 * len(freqs))


def bass(freq: float, dur: float) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    env = (1 - np.exp(-t / 0.01)) * np.exp(-t / 0.25)
    return env * (np.sin(2 * np.pi * freq * t) + 0.2 * np.sin(2 * np.pi * 2 * freq * t))


def add(buf: np.ndarray, x: np.ndarray, at: float) -> None:
    """Add x into the loop buffer at time `at`, wrapping the tail to the start."""
    i = int(round(at * SR)) % len(buf)
    n = len(x)
    first = min(n, len(buf) - i)
    buf[i:i + first] += x[:first]
    if n > first:
        buf[:n - first] += x[first:]


def render_loop() -> np.ndarray:
    rng = np.random.default_rng(SEED)
    bars = 2 * len(CHORDS)
    loop = np.zeros(int(round(bars * BAR * SR)))
    arp = np.zeros_like(loop)
    pads = np.zeros_like(loop)
    low = np.zeros_like(loop)
    for b in range(bars):
        chord = CHORDS[b % len(CHORDS)]
        t0 = b * BAR
        add(pads, pad([hz(n) for n in chord[1:]], BAR + 0.6), t0)
        for beat in range(4):
            add(low, bass(hz(chord[0] - 12), BEAT * 0.9), t0 + beat * BEAT)
        pattern = PATTERNS[(b // len(CHORDS)) % len(PATTERNS)]
        for k, (idx, octv) in enumerate(pattern):
            note = chord[idx] + 12 * octv
            vel = (0.9 if k % 2 == 0 else 0.7) * rng.uniform(0.9, 1.0)
            add(arp, pluck(hz(note), 1.2, vel), t0 + k * BEAT / 2)
    loop = 0.55 * arp + 0.45 * pads + 0.5 * low
    # Gentle one-pole low-pass for a softer, warmer bed (applied circularly).
    a = np.exp(-2 * np.pi * 3500 / SR)
    y = np.zeros_like(loop)
    prev = 0.0
    for _ in range(2):  # second pass settles the state so the loop joins cleanly
        for i, v in enumerate(loop):
            prev = (1 - a) * v + a * prev
            y[i] = prev
    return y


def make(seconds: float) -> np.ndarray:
    loop = render_loop()
    n = int(round(seconds * SR))
    out = np.tile(loop, n // len(loop) + 1)[:n]
    rms = np.sqrt(np.mean(out ** 2))
    out *= 10 ** (TARGET_RMS_DB / 20) / rms
    fade = min(n, int(1.0 * SR))  # soft start
    out[:fade] *= np.linspace(0, 1, fade)
    return out


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    write_wav(args.out, make(args.seconds))


if __name__ == "__main__":
    main()
