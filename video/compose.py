"""Compose the submission video: cards and demo recordings timed to the narration, then burned-in captions."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

d = Path(sys.argv[1])
FFMPEG = sys.argv[2]
timings = json.loads((d / "timings.json").read_text(encoding="utf-8"))
CARDS = {"s0_title", "s1_problem", "s6_close"}
FADE = 0.35


def run(args):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], cwd=d, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])


def duration(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


parts = []
for scene in timings:
    name, dur = scene["scene"], scene["duration"]
    fades = f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.3f}:d={FADE}"
    common = ["-map", "0:v", "-map", "1:a", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
              "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", f"{name}.mp4"]
    if name in CARDS:
        run(["-loop", "1", "-i", f"{name}.png", "-i", f"{name}.wav",
             "-vf", f"scale=1920:1080,fps=30,{fades},format=yuv420p", *common])
    else:
        raw = duration(d / f"{name}.webm")
        stretch = dur / raw
        # Pad the recording's last frame a little so speed changes never leave the tail short.
        run(["-i", f"{name}.webm", "-i", f"{name}.wav",
             "-vf", f"setpts=PTS*{stretch:.5f},fps=30,tpad=stop_mode=clone:stop_duration=1,scale=1920:1080:flags=lanczos,{fades},format=yuv420p",
             *common])
        print(f"{name}: recording {raw:.1f}s -> {dur:.1f}s (x{1 / stretch:.2f} speed)")
    parts.append(f"file '{name}.mp4'")
(d / "parts.txt").write_text("\n".join(parts) + "\n", encoding="utf-8")
run(["-f", "concat", "-safe", "0", "-i", "parts.txt", "-c", "copy", "full.mp4"])


def stamp(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


cues, offset, n = [], 0.0, 0
for scene in timings:
    for cue in scene["cues"]:
        n += 1
        cues.append(f"{n}\n{stamp(offset + cue['start'])} --> {stamp(offset + cue['end'])}\n{cue['text']}\n")
    offset += scene["duration"]
(d / "polaris-demo.en.srt").write_text("\n".join(cues), encoding="utf-8")

fonts = d / "fonts"
fonts.mkdir(exist_ok=True)
shutil.copy("C:/Windows/Fonts/arial.ttf", fonts / "arial.ttf")
style = ("FontName=Arial,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BackColour=&H80000000,"
         "BorderStyle=3,Outline=1,Shadow=0,MarginV=22,MarginL=60,MarginR=60")
run(["-i", "full.mp4", "-vf", f"subtitles=polaris-demo.en.srt:fontsdir=fonts:force_style='{style}'",
     "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "copy",
     "-movflags", "+faststart", "polaris-demo.mp4"])
print(f"final: {duration(d / 'polaris-demo.mp4'):.1f}s, {(d / 'polaris-demo.mp4').stat().st_size / 1e6:.1f} MB, {n} captions")
