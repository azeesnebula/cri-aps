"""Replace only the opening brand-name sentence; retain every scene's timing."""
import asyncio
import io
import json
import subprocess
import wave
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parents[1]
VOICE = "en-US-AndrewMultilingualNeural"
SPOKEN = "Here is the see are eye Pumps planning workspace in action."


async def correct_brand_name(target, original_words):
    folder = target.parent
    raw = folder / "cri-pronunciation.mp3"
    metadata = folder / "cri-pronunciation.json"
    if not raw.exists() or not metadata.exists():
        words = []
        tts = edge_tts.Communicate(SPOKEN, VOICE, rate="+1%", boundary="WordBoundary")
        with raw.open("wb") as stream:
            async for chunk in tts.stream():
                if chunk["type"] == "audio":
                    stream.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({"text": chunk["text"], "start": chunk["offset"] / 10_000_000,
                                  "end": (chunk["offset"] + chunk["duration"]) / 10_000_000})
        metadata.write_text(json.dumps({"spoken": SPOKEN, "words": words}, indent=2))
    words = json.loads(metadata.read_text())["words"]
    assert [w["text"].lower() for w in words[3:6]] == ["see", "are", "eye"], words
    action = next(i for i, word in enumerate(original_words) if word["text"].lower() == "action")
    end = (original_words[action]["end"] + original_words[action + 1]["start"]) / 2
    # Fit only if necessary. Normally the clear letter names are shorter than
    # the original period-separated acronym and the remaining interval is quiet.
    useful = words[-1]["end"] + .10
    speed = max(1, useful / (end - .03))
    replacement = folder / "cri-pronunciation.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-t", str(useful),
                    "-af", f"atempo={speed},loudnorm=I=-17:TP=-2:LRA=8", "-ar", "48000", "-ac", "1", str(replacement)], check=True)
    original = target.read_bytes()
    backup = folder / "overview-before-pronunciation.wav"
    if not backup.exists():
        backup.write_bytes(original)
    with wave.open(io.BytesIO(original), "rb") as stream:
        params = stream.getparams()
        samples = stream.readframes(stream.getnframes())
    with wave.open(str(replacement), "rb") as stream:
        patch = stream.readframes(stream.getnframes())
    assert params.nchannels == 1 and params.sampwidth == 2 and params.framerate == 48000
    cut = round(end * params.framerate) * 2
    assert len(patch) <= cut
    updated = patch + b"\0" * (cut - len(patch)) + samples[cut:]
    assert len(updated) == len(samples) and updated[cut:] == samples[cut:]
    with wave.open(str(target), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(updated)
    mapped = [{**word, "text": {"see": "C", "are": "R", "eye": "I"}.get(word["text"].lower(), word["text"]),
               "start": word["start"] / speed, "end": word["end"] / speed} for word in words]
    return mapped + original_words[action + 1:]


async def main():
    target = ROOT / "public/audio/overview.wav"
    original_words = json.loads((target.parent / "overview-words.json").read_text())["words"]
    words = await correct_brand_name(target, original_words)
    timing_path = ROOT / "src/timing.json"
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    scene = next(s for s in timing["scenes"] if s["id"] == "overview")
    # Retain displayed spelling. Only the two caption timings over the edited
    # sentence are realigned; later captions and all scene offsets stay fixed.
    pumps = next(i for i, word in enumerate(words) if word["text"].lower() == "pumps")
    action = next(i for i, word in enumerate(words) if word["text"].lower() == "action")
    for caption, group in zip(scene["captions"][:2], (words[:pumps + 1], words[pumps + 1:action + 1])):
        caption["startMs"] = round((group[0]["start"] + scene["audioOffset"]) * 1000)
        caption["endMs"] = round((group[-1]["end"] + scene["audioOffset"] + .16) * 1000)
        caption["timestampMs"] = (caption["startMs"] + caption["endMs"]) // 2
    scene["captions"][0]["endMs"] = min(scene["captions"][0]["endMs"], scene["captions"][1]["startMs"])
    timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep the standalone narration in sync without touching other scenes.
    track = ROOT / "public/voiceover.wav"
    with wave.open(io.BytesIO(track.read_bytes()), "rb") as stream:
        params = stream.getparams()
        data = bytearray(stream.readframes(stream.getnframes()))
    with wave.open(str(target), "rb") as stream:
        scene_data = stream.readframes(stream.getnframes())
    offset = round((scene["from"] / timing["fps"] + scene["audioOffset"]) * params.framerate) * 2
    data[offset:offset + len(scene_data)] = scene_data
    with wave.open(str(track), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(data)
    def stamp(ms):
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    entries = []
    for shot in timing["scenes"]:
        for caption in shot["captions"]:
            start = round(shot["from"] / timing["fps"] * 1000)
            entries.append(f"{len(entries)+1}\n{stamp(start+caption['startMs'])} --> {stamp(start+caption['endMs'])}\n{caption['text']}\n")
    (ROOT / "public/captions.srt").write_text("\n".join(entries), encoding="utf-8")
    print("Corrected C.R.I pronunciation to 'see are eye'. Total duration retained:", timing["totalFrames"] / timing["fps"])


if __name__ == "__main__":
    asyncio.run(main())
