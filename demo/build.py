#!/usr/bin/env python3
"""Build the narrated demo: voice + ducked music mix, end hold + fade, encode,
SRT and poster.

Inputs (from demo/tts.py and demo/record.mjs, in the work dir):
  raw.webm, timeline.json, clips/<id>.wav; plus demo/script.json.
Outputs: assets/demo/demo.mp4, demo-poster.png, demo.srt (and intermediate
files in <work>/build/).

Usage: demo/.venv/bin/python demo/build.py --work <work dir>
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import music  # noqa: E402

HOLD_S = 3.0      # frozen last frame after the final sentence ends
FADE_S = 1.5      # video + audio fade-out at the very end
FPS = 30
CRFS = [20, 22, 24, 26, 28]
# Mono AAC-LC at 64k: 128k alone would take ~2.4 MB of the 5 MB budget for a
# ~150 s video and push the picture past CRF 28 (measured: 5.56 MB at CRF 28).
AUDIO_BITRATE = "64k"
MAX_BYTES = 5 * 1024 * 1024
LINE_CHARS = 42
DUCK = "sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400"
LOUD = "I=-23:TP=-1.5:LRA=11"


def run(cmd: list, capture: bool = False) -> str:
    res = subprocess.run(cmd, check=True, text=True,
                         stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                         stderr=subprocess.PIPE)
    return (res.stdout or "") + res.stderr


def read_wav(path: Path) -> tuple:
    with wave.open(str(path)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit(f"build: {path} must be mono 16-bit")
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        return x, w.getframerate()


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)], capture=True)
    return float(out.split()[0])


def load_clips(timeline: list, clips: Path) -> tuple:
    """Read every step's clip; all must share one sample rate."""
    loaded = {s["id"]: read_wav(clips / f"{s['id']}.wav") for s in timeline}
    rates = {sr for _, sr in loaded.values()}
    if len(rates) != 1:
        sys.exit(f"build: clips have mixed sample rates {rates}")
    return {k: x for k, (x, _) in loaded.items()}, rates.pop()


def voice_track(timeline: list, clips: dict, sr: int, total: float, out: Path) -> None:
    """Place each clip exactly at its step start (= when its caption appears)."""
    buf = np.zeros(int(round(total * sr)), dtype=np.float32)
    for step in timeline:
        x, i = clips[step["id"]], int(round(step["start"] * sr))
        buf[i:i + len(x)] += x[:len(buf) - i]
    write_wav(out, buf, sr)


def mix(voice: Path, bed: Path, fade_at: float, out: Path) -> dict:
    """Duck the bed under the voice, sum, then two-pass loudnorm + fade-out."""
    pre = out.with_name("mix-pre.wav")
    graph = (f"[0:a]aresample=48000,asplit=2[vk][vm];[1:a]aresample=48000[m];"
             f"[m][vk]{DUCK}[duck];[vm][duck]amix=inputs=2:normalize=0:duration=first[out]")
    run(["ffmpeg", "-y", "-i", str(voice), "-i", str(bed), "-filter_complex", graph,
         "-map", "[out]", "-ac", "1", str(pre)])
    log = run(["ffmpeg", "-i", str(pre), "-af", f"loudnorm={LOUD}:print_format=json",
               "-f", "null", "-"])
    m = json.loads(log[log.rindex("{"):log.rindex("}") + 1])
    norm = (f"loudnorm={LOUD}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}:linear=true,aresample=48000,"
            f"afade=t=out:st={fade_at:.3f}:d={FADE_S}")
    run(["ffmpeg", "-y", "-i", str(pre), "-af", norm, "-ac", "1", str(out)])
    return m


def encode(raw: Path, audio: Path, total: float, fade_at: float, out: Path) -> int:
    pad = max(0.0, total - probe_duration(raw)) + 1.0
    vf = (f"fps={FPS},tpad=stop_mode=clone:stop_duration={pad:.3f},"
          f"fade=t=out:st={fade_at:.3f}:d={FADE_S},format=yuv420p")
    tmp = out.with_suffix(".tmp.mp4")
    for crf in CRFS:
        run(["ffmpeg", "-y", "-i", str(raw), "-i", str(audio), "-map", "0:v", "-map", "1:a",
             "-vf", vf, "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
             "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "1",
             "-t", f"{total:.3f}", "-movflags", "+faststart", str(tmp)])
        size = tmp.stat().st_size
        print(f"build: CRF {crf} -> {size} bytes")
        if size <= MAX_BYTES:
            tmp.replace(out)
            return crf
    tmp.unlink()
    sys.exit(f"build: FAILED, video is {size} bytes at CRF {CRFS[-1]}, over the "
             f"{MAX_BYTES}-byte limit. Shorten the recording.")


def faststart_ok(path: Path) -> bool:
    """True when the moov box comes before mdat (top-level box order)."""
    order, data = [], path.read_bytes()
    i = 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8].decode("latin-1")
        if size == 1:
            size = int.from_bytes(data[i + 8:i + 16], "big")
        order.append(kind)
        if size < 8:
            break
        i += size
    return "moov" in order and "mdat" in order and order.index("moov") < order.index("mdat")


def wrap(text: str) -> list:
    """Greedy wrap to LINE_CHARS; a two-line result is rebalanced so the
    lines are of similar length (no one-word second line)."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > LINE_CHARS:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    lines.append(cur)
    if len(lines) == 2:
        pairs = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        lines = list(min((p for p in pairs if max(map(len, p)) <= LINE_CHARS),
                         key=lambda p: max(map(len, p))))
    return lines


def split_text(text: str) -> list:
    """Halve text that needs more than two lines, preferring a comma near the
    middle, else the word boundary nearest the middle (no orphan words)."""
    if len(wrap(text)) <= 2:
        return ["\n".join(wrap(text))]
    words = text.split()
    cuts = [(len(" ".join(words[:i])), i) for i in range(1, len(words))]
    commas = [c for c in cuts if words[c[1] - 1].endswith(",")]
    mid = len(text) / 2
    pool = [c for c in commas if abs(c[0] - mid) < len(text) / 4] or cuts
    i = min(pool, key=lambda c: abs(c[0] - mid))[1]
    return split_text(" ".join(words[:i])) + split_text(" ".join(words[i:]))


def cue_texts(caption: str) -> list:
    """Split a caption into cues of at most two lines of ~LINE_CHARS each."""
    return [c for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", caption.strip())
            for c in split_text(sentence)]


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def srt(timeline: list, captions: dict, speech: dict, total: float) -> str:
    """One or more cues per step. A step's first cue starts with its voice line;
    long captions are split in proportion to their length over the spoken
    time, and the last cue stays up until the step ends (as on screen); the
    final step's caption stays through the end hold and fade."""
    out, n = [], 0
    for step in timeline:
        parts = cue_texts(captions[step["id"]])
        weights = np.cumsum([0] + [len(p) for p in parts]) / sum(len(p) for p in parts)
        span = min(speech[step["id"]], step["end"] - step["start"])
        for k, text in enumerate(parts):
            a = step["start"] + span * weights[k]
            b = step["end"] if k == len(parts) - 1 else step["start"] + span * weights[k + 1]
            if step is timeline[-1] and k == len(parts) - 1:
                b = total
            n += 1
            out.append(f"{n}\n{ts(a)} --> {ts(b)}\n{text}\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build assets/demo from the recording.")
    ap.add_argument("--work", type=Path,
                    default=Path(os.environ.get("DEMO_WORK", HERE / "work")))
    ap.add_argument("--out", type=Path, default=ROOT / "assets" / "demo")
    ap.add_argument("--poster", default="gallery:1.7",
                    help="<step id>:<seconds into the step> for the poster frame")
    args = ap.parse_args()

    work, outdir = args.work.resolve(), args.out.resolve()
    build = work / "build"
    build.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    timeline = json.loads((work / "timeline.json").read_text())
    captions = {s["id"]: s["caption"] for s in json.loads((HERE / "script.json").read_text())}
    if missing := [s["id"] for s in timeline if s["id"] not in captions]:
        sys.exit(f"build: timeline steps missing from script.json: {missing}")
    raw = work / "raw.webm"

    clips, sr = load_clips(timeline, work / "clips")
    speech = {k: len(x) / sr for k, x in clips.items()}
    # Length: hold the last frame HOLD_S after the last line ends, then fade.
    voice_end = max(s["start"] + speech[s["id"]] for s in timeline)
    total = max(voice_end + HOLD_S, probe_duration(raw)) + FADE_S
    fade_at = total - FADE_S
    voice_track(timeline, clips, sr, total, build / "voice.wav")
    print(f"build: last line ends {voice_end:.2f} s, fade {fade_at:.2f}-{total:.2f} s")

    music.write_wav(build / "music.wav", music.make(total))
    m = mix(build / "voice.wav", build / "music.wav", fade_at, build / "mix.wav")
    print(f"build: pre-norm loudness {m['input_i']} LUFS, TP {m['input_tp']} dBTP")

    mp4 = outdir / "demo.mp4"
    crf = encode(raw, build / "mix.wav", total, fade_at, mp4)
    if not faststart_ok(mp4):
        sys.exit("build: FAILED, moov is not before mdat (faststart)")

    (outdir / "demo.srt").write_text(srt(timeline, captions, speech, total))

    sid, off = args.poster.split(":")
    at = next(s["start"] for s in timeline if s["id"] == sid) + float(off)
    run(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(mp4), "-frames:v", "1",
         str(outdir / "demo-poster.png")])
    print(f"build: done, {mp4} ({mp4.stat().st_size} bytes, CRF {crf}, {total:.2f} s), "
          f"poster at {at:.2f} s")


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        sys.exit("build: ffmpeg not found")
    main()
