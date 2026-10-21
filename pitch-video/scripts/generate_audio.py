"""Generate narrated scenes, word-aligned compact captions and an original score."""
import asyncio
import array
import hashlib
import json
import math
import re
import subprocess
import wave
from pathlib import Path

import edge_tts
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
AUDIO = PUBLIC / "audio"
FPS = 30
VOICE = "en-US-AndrewMultilingualNeural"
OFFSET = .5


def probe(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True).strip())


async def voice(scene):
    text = " ".join(scene["phrases"])
    signature = hashlib.sha256((text + VOICE + "rate=+1%").encode()).hexdigest()
    raw = AUDIO / (scene["id"] + ".mp3")
    metadata = AUDIO / (scene["id"] + "-words.json")
    cached = json.loads(metadata.read_text()) if metadata.exists() else {}
    if not raw.exists() or cached.get("signature") != signature:
        for attempt in range(3):
            try:
                words = []
                tts = edge_tts.Communicate(text, VOICE, rate="+1%", boundary="WordBoundary")
                with raw.open("wb") as stream:
                    async for chunk in tts.stream():
                        if chunk["type"] == "audio":
                            stream.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            words.append({"text": chunk["text"], "start": chunk["offset"] / 10_000_000,
                                          "end": (chunk["offset"] + chunk["duration"]) / 10_000_000})
                metadata.write_text(json.dumps({"signature": signature, "words": words}, indent=2))
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 + attempt * 2)
    else:
        words = cached["words"]
    target = AUDIO / (scene["id"] + ".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw),
        "-af", "loudnorm=I=-17:TP=-2:LRA=8", "-ar", "48000", "-ac", "1", str(target)], check=True)
    if scene["id"] == "overview":
        from correct_pronunciation import correct_brand_name
        words = await correct_brand_name(target, words)
    normal = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    cursor = 0
    captions = []
    for phrase in scene["phrases"]:
        wanted = normal(phrase)
        group = []
        while normal(" ".join(word["text"] for word in group)) != wanted:
            if cursor >= len(words):
                raise ValueError(f"Missing caption boundary: {scene['id']} {phrase}")
            group.append(words[cursor])
            cursor += 1
            if not wanted.startswith(normal(" ".join(word["text"] for word in group))):
                raise ValueError(f"Caption mismatch: {phrase} {group}")
        start = round((group[0]["start"] + OFFSET) * 1000)
        end = round((group[-1]["end"] + OFFSET + .16) * 1000)
        captions.append({"text": phrase, "startMs": start, "endMs": end,
                         "timestampMs": (start + end) // 2, "confidence": None})
    assert cursor == len(words), scene["id"]
    for current, next_caption in zip(captions, captions[1:]):
        current["endMs"] = min(current["endMs"], next_caption["startMs"])
    duration = probe(target)
    frames = math.ceil((duration + OFFSET + .65) * FPS)
    return {**scene, "narration": text, "audio": "audio/" + target.name,
            "audioOffset": OFFSET, "audioDuration": duration, "frames": frames, "captions": captions}


def original_score(seconds):
    """An original, restrained 80 BPM tonal bed; no third-party recording."""
    sample_rate = 24000
    chords = [(130.8128, 164.8138, 195.9977), (110, 130.8128, 164.8138),
              (87.3071, 130.8128, 174.6141), (97.9989, 146.8324, 195.9977)]
    with wave.open(str(PUBLIC / "score-raw.wav"), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        for second in range(math.ceil(seconds)):
            data = array.array("h")
            for n in range(sample_rate):
                t = second + n / sample_rate
                if t >= seconds:
                    break
                chord = chords[int(t / 6) % 4]
                prior = chords[(int(t / 6) - 1) % 4]
                transition = min(1, (t % 6) / 1.5)
                blend = transition * transition * (3 - 2 * transition)
                left = right = 0
                for frequencies, weight in ((chord, blend), (prior, 1 - blend)):
                    for frequency in frequencies:
                        left += .025 * weight * math.sin(math.tau * frequency * t)
                        right += .025 * weight * math.sin(math.tau * frequency * 1.0004 * t)
                beat = t % .75
                pulse = .022 * math.sin(math.tau * 55 * t) * math.exp(-beat * 13)
                high = .008 * math.sin(math.tau * chord[int(t / .75) % 3] * 4 * t) * math.exp(-beat * 8)
                envelope = min(1, t / 2, max(0, (seconds - t) / 3))
                data.extend((int((left + pulse + high) * envelope * 32767),
                             int((right + pulse + high) * envelope * 32767)))
            stream.writeframes(data.tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(PUBLIC / "score-raw.wav"),
        "-af", "loudnorm=I=-31:TP=-8:LRA=7", "-ar", "48000", "-c:a", "libmp3lame", "-b:a", "192k",
        str(PUBLIC / "score.mp3")], check=True)


async def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    Image.open(ROOT.parent / "cri-logo.gif").convert("RGBA").save(PUBLIC / "cri-logo.png")
    script = json.loads((ROOT / "src" / "script.json").read_text(encoding="utf-8"))
    scenes = []
    cursor = 0
    for scene in script:
        generated = await voice(scene)
        generated["from"] = cursor
        cursor += generated["frames"]
        scenes.append(generated)
        print(f"{scene['id']}: {generated['frames']/FPS:.2f}s", flush=True)
    manifest = {"fps": FPS, "width": 1920, "height": 1080, "totalFrames": cursor,
                "voice": VOICE, "scenes": scenes}
    (ROOT / "src" / "timing.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    def timestamp(ms):
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    subtitles = []
    for scene in scenes:
        for caption in scene["captions"]:
            offset = round(scene["from"] * 1000 / FPS)
            subtitles.append(f"{len(subtitles)+1}\n{timestamp(offset+caption['startMs'])} --> {timestamp(offset+caption['endMs'])}\n{caption['text']}\n")
    (PUBLIC / "captions.srt").write_text("\n".join(subtitles), encoding="utf-8")
    (PUBLIC / "transcript.txt").write_text("\n\n".join(s["narration"] for s in scenes), encoding="utf-8")
    # A standalone narration track with exactly the same frame-aligned scene spacing.
    with wave.open(str(PUBLIC / "voiceover.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        for scene in scenes:
            samples = scene["frames"] * 1600
            lead = round(OFFSET * 48000)
            stream.writeframes(b"\0\0" * lead)
            with wave.open(str(PUBLIC / scene["audio"]), "rb") as source:
                voice_samples = source.getnframes()
                stream.writeframes(source.readframes(voice_samples))
            stream.writeframes(b"\0\0" * (samples - lead - voice_samples))
    print(f"Narration ready: {cursor/FPS:.2f}s; composing original score", flush=True)
    original_score(cursor / FPS)
    print("Narration, caption JSON, SRT, logo and score ready.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
