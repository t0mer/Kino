#!/usr/bin/env python3
"""Generate one narration clip per step of demo/script.json with Kokoro.

Outputs in --out: <id>.wav (mono 24 kHz), <id>.env.json (mouth envelope, 20 fps,
0..1) and manifest.json. A clip is regenerated only when its text changed.
"""

import argparse
import hashlib
import json
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
VOICE, LANG, SPEED = "af_heart", "en-us", 1.0
KEEP_S, FPS, SILENCE_DB = 0.08, 20, -45.0


def trim(x: np.ndarray, sr: int) -> np.ndarray:
    thr = 10 ** (SILENCE_DB / 20)
    idx = np.flatnonzero(np.abs(x) > thr)
    if idx.size == 0:
        return x
    keep = int(KEEP_S * sr)
    return x[max(0, idx[0] - keep) : min(len(x), idx[-1] + 1 + keep)]


def envelope(x: np.ndarray, sr: int) -> list:
    hop = sr // FPS
    n = max(1, int(np.ceil(len(x) / hop)))
    rms = np.array(
        [
            np.sqrt(np.mean(x[i * hop : (i + 1) * hop] ** 2))
            if len(x[i * hop : (i + 1) * hop])
            else 0.0
            for i in range(n)
        ]
    )
    rms = np.convolve(rms, [0.25, 0.5, 0.25], mode="same")
    peak = rms.max()
    if peak > 0:
        rms = rms / peak
    return [round(float(v), 3) for v in rms]


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=str(HERE / "script.json"))
    ap.add_argument("--out", default=str(HERE / "work" / "clips"))
    ap.add_argument("--models", default=str(HERE / "models"))
    ap.add_argument(
        "--check", action="store_true", help="print durations from the manifest and exit"
    )
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    steps = json.loads(Path(a.script).read_text())
    mpath = out / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}

    if not a.check:
        kokoro = None
        for s in steps:
            h = hashlib.sha256(f"{VOICE}|{LANG}|{SPEED}|{s['say']}".encode()).hexdigest()
            m = manifest.get(s["id"])
            if m and m.get("hash") == h and Path(m["wav"]).exists() and Path(m["env"]).exists():
                continue
            if kokoro is None:
                from kokoro_onnx import Kokoro

                kokoro = Kokoro(
                    str(Path(a.models) / "kokoro-v1.0.onnx"),
                    str(Path(a.models) / "voices-v1.0.bin"),
                )
            x, sr = kokoro.create(s["say"], voice=VOICE, speed=SPEED, lang=LANG)
            x = trim(np.asarray(x, dtype=np.float32), sr)
            wav, env = out / f"{s['id']}.wav", out / f"{s['id']}.env.json"
            write_wav(wav, x, sr)
            env.write_text(json.dumps(envelope(x, sr)))
            manifest[s["id"]] = {
                "duration": round(len(x) / sr, 3),
                "wav": str(wav),
                "env": str(env),
                "hash": h,
            }
            print(f"generated {s['id']}: {len(x) / sr:.2f}s")
        manifest = {s["id"]: manifest[s["id"]] for s in steps}
        mpath.write_text(json.dumps(manifest, indent=2))

    total = 0.0
    for s in steps:
        d = manifest[s["id"]]["duration"]
        total += d
        print(f"{s['id']:<12}{d:6.2f}s")
    print(f"total speech: {total:.1f}s")


if __name__ == "__main__":
    main()
