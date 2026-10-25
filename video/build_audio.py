"""Join each scene's sentence WAVs into one track and write the caption timings."""
import json
import sys
import wave
from pathlib import Path

d = Path(sys.argv[1])
scenes = json.loads((d / "narration.json").read_text(encoding="utf-8"))
LEAD, GAP, TAIL = 0.5, 0.35, 0.5
RATE = 24000
timings = []
for scene in scenes:
    name = scene["scene"]
    frames = bytearray(int(LEAD * RATE) * 2)
    t = LEAD
    cues = []
    for i, text in enumerate(scene["sentences"]):
        with wave.open(str(d / "tts" / f"{name}_{i:02d}.wav"), "rb") as w:
            assert w.getframerate() == RATE and w.getsampwidth() == 2 and w.getnchannels() == 1
            data = w.readframes(w.getnframes())
        length = len(data) / 2 / RATE
        cues.append({"start": round(t, 3), "end": round(t + length, 3), "text": text})
        frames += data
        t += length
        gap = GAP if i < len(scene["sentences"]) - 1 else TAIL
        frames += bytearray(int(gap * RATE) * 2)
        t += gap
    with wave.open(str(d / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    timings.append({"scene": name, "duration": round(len(frames) / 2 / RATE, 3), "cues": cues})
(d / "timings.json").write_text(json.dumps(timings, ensure_ascii=False, indent=1), encoding="utf-8")
total = sum(s["duration"] for s in timings)
for s in timings:
    print(f"{s['scene']:12s} {s['duration']:6.1f}s")
print(f"total {total:.1f}s")
