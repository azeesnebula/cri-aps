# C.R.I Pumps APS — pitch and product demo

**Final video:** `out/CRI_Pumps_APS_Pitch.mp4`  
**Duration:** 2 minutes 31 seconds · **Format:** 1920 × 1080, 30 fps, H.264/AAC  
**Framework:** Remotion 4.0.536

## Pitch structure

Scheduling pressure → connected orders/materials/capacity → shipment visibility → order filtering → machine sequencing → supply and build dependencies → capacity evidence → controlled release → demonstration results → focused pilot.

All application images are captured from the running C.R.I APS in dark mode. The video uses real screen states, crops and camera moves, with conceptual motion graphics for the problem and value proposition. There are no slideshow controls, page numbers or scrolling presentation pages.

The supplied GIF cycles between C.R.I divisions. Capture freezes its original Pumps frame; no screen data, labels or performance values are retouched. Read-only interactions are used during capture. Filtering and operation drilldowns reflect actual application states.

Narration uses a newly generated English neural voice, with an original quiet instrumental bed. Phrase-level subtitles occupy a separate bottom band beneath the application footage.

The plan results are explicitly presented as **demonstration evidence**, not a promise of actual factory outcomes or a claim of measured cost savings.

## Deliverables

- `out/CRI_Pumps_APS_Pitch.mp4` — final video.
- `out/CRI_Pumps_APS_Pitch.srt` — timed subtitle file.
- `out/narration.txt` — complete narration.
- `out/contact-sheet.jpg` — representative frames extracted from the final MP4.
- `out/quality-report.json` — resolution, codecs, duration, caption timing and audio checks.
- `public/captures/manifest.json` — actual application routes and capture provenance.
- `src/` — editable Remotion compositions, narration and timing.

## Preview and render

```powershell
npm install
npm run studio
npm run typecheck
npm run render
python scripts/verify.py
```

Studio runs at **http://localhost:3011/CRIPumpsPitch**. Individual scenes are registered as connected compositions for editing. Use `npm run watch` to watch the final MP4 in a simple video player at **http://localhost:3012**.

To recapture the running application or regenerate narration:

```powershell
python -m pip install edge-tts Pillow
npm run audio
npm run capture
```

Capture defaults to localhost:8081 and the initial local login. Set `CRI_VIDEO_USER` and `CRI_VIDEO_PASSWORD` environment variables for a different account. Credentials are not stored in the capture manifest.

FFmpeg/ffprobe are required for audio creation and final verification. The renderer uses an existing local Chrome headless shell when available; otherwise Remotion can provision its browser. `REMOTION_BROWSER_EXECUTABLE` can point to an installed compatible browser.

The official Remotion skills were consulted for composition structure, frame-driven motion, connected scenes, media playback, captions and render settings. Dependencies are pinned together at version 4.0.536.
