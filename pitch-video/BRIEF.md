# C.R.I Pumps — From production pressure to delivery confidence

- Framework: **Remotion**, explicitly requested. Official remotion-dev best-practices, markup, creation, captions and rendering guidance reviewed.
- Deliverable: narrated, polished sales-pitch/demo video; 1920 × 1080, 30 fps, H.264 MP4 and editable source.
- Audience: C.R.I Pumps planning, production and operations decision-makers.
- Final narrated length: approximately two minutes thirty seconds; pacing follows the measured narration.
- Language/voice: professional English neural narration, calm and confident.
- Narrative: scheduling pain → connected APS → shipment overview → order drilldown → finite machine schedule → material dependencies → capacity decisions → controlled release → evidence → pilot.
- Product evidence: exact captures of http://localhost:8081 in **dark mode**, from the existing 19 October 2026 plan. Screen pixels and values are not reconstructed or altered. Cropping, zoom and annotations direct attention.
- Claims: demonstration results are specifically scoped to the current plan (10 shipments, 670 pumps, 260 operations). Do not imply these are actual C.R.I operational records or guaranteed improvements. No invented savings, integrations or measured ROI.
- Visual language: charcoal/black, C.R.I red, clean white typography, restrained green for evidence; strong composition, deliberate motion, crisp transitions, purposeful camera moves.
- Demo frames: actual application screen states; before/after filtering, real shipment tabs, real machine operation detail, real build graph and release review. No approval, rescheduling or database edits during capture.
- Captions: phrase-level, normally 3–7 words, one line at 32px; dedicated bottom safe band outside screen content. Never block charts or tables.
- Explicit exclusions: no slide navigation, page numbers, presentation controls, slide-deck imitation, scrolling-slide transitions or fake application interfaces.
- Audio: original quiet instrumental bed under voice; restrained transition accents; final loudness/peak checks.
- Review: representative frames, browser preview, final encoded MP4 checks and screenshots.

## Asset provenance

- Logo: supplied `../cri-logo.gif`.
- Capture logo treatment: freeze the supplied GIF's first Pumps frame so its animation does not switch to the Valves division during a shot. The source logo pixels are unmodified.
- App captures: local C.R.I APS, authenticated read-only browser interactions.
- Plan facts: `../scenarios/2026-10-19/validation.json` and current application screens.
- Narration: newly generated from `src/script.json` using Edge TTS.
- Music: newly synthesized original tonal/pulse score.
- Fonts: locally packaged Inter and Manrope.
