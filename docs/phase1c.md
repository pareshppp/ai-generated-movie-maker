# Phase 1c: "The Rishi and the Apsara" (epic CG), findings

The first story taken end to end, from a hand-written shot list to a rough cut. Run on 2026-11-09.

- **Style:** `epic_cg`, high-end 3D CG with realistic faces. Locked on 2026-11-09; photoreal was dropped.
- **Pipeline:** Qwen-Image-2.1 8-step keyframes → LTX-2.5 distilled I2V at 1280×720, 8 s, strength 0.9. These are the Phase 1 defaults.
- **Hardware:** RTX 6000 Ada at $0.99/hr.

| Output | Path |
|---|---|
| Rough cut (80.4 s, 1280×704, 24 fps, AAC, −15.4 LUFS) | `renders/story/rishi_apsara_roughcut_v1.mp4` |
| All takes plus the HTML report | `renders/exp/story_rishi_apsara_v1/index.html`; the shot 3 fix is in `..._v1_fix03/` |
| Keyframe and clip picks | `prompts/rishi_apsara.json` → `keyframe_picks`, `clip_picks` |
| Expected dialogue | `prompts/rishi_apsara_dialogue.json` |

Commands:

```bash
python -m mm.experiments styles --prompts prompts/rishi_apsara.json --kf-root renders/keyframes_story \
    --styles epic_cg --seeds 42,7 --name story_rishi_apsara_v1      # add --shots ID to redo one shot
python -m mm.roughcut renders/story/rishi_apsara_roughcut_v1.mp4 --lufs -16 <10 picked clips in order>
```

For Whisper ASR, run it locally on the laptop GPU, so no SSH or pod time is needed:

```bash
HF_HOME=~/.cache/huggingface uv run --python 3.12 --with torch --with transformers --with numpy --with accelerate \
    python -I infra/asr.py renders/exp/story_rishi_apsara_v1 --expect-json prompts/rishi_apsara_dialogue.json
```

---

## 1. Cost and speed

- 22 clips: 10 shots × 2 seeds (42 and 7), plus 2 re-takes of shot 3. 0 failures.
- A warm clip takes **158–166 s per 8 s clip** (~20 s of compute per output second), which costs **~$0.045 per clip**. The first, cold clip took 269 s.
- Total render cost was **~$1.02**: an 80 s film at 2 takes per shot costs about $1 of GPU.

## 2. Dialogue: native LTX audio works

The motion prompt carries each line in double quotes, with the speaker and tone named.

| Shot | Line | s42 | s7 |
|---|---|---|---|
| 02 | "Om Namah Shivaya" (chant) | 1.0 | 0.75 (one extra repeat) |
| 05 | "His tapas will shake my throne." | 1.0 | 1.0 |
| 06 | Indra: "Go. Break his tapasya." / Apsara: "No man has ever resisted me, my lord." | 1.0 | 1.0 |
| 09 | "Open your eyes, rishi." (whisper) | 1.0 | 1.0 |
| 10 | "Who dares disturb my tapas?" (roar) | 1.0 | 0.8 ("TAPA") |

- Every clip spoke its line, **Sanskrit terms included**.
- **Two speakers in one shot (06):** in both takes, frame checks show Indra's mouth moving on his line and then the apsara's on hers. The turn-taking is correct.
- The sustained "Om" drone in shot 01 transcribes as "oh oh oh…", which is expected for a non-speech sound.
- **Still to do:** check lip-sync by eye at full speed. Frame strips look plausible but can't prove sync. Voice identity across shots is the next risk. The rishi speaks in 02 and 10, and Indra in 05 and 06, and nothing ties those voices together yet.
- **Decision input for Phase 5:** native audio is good enough for this story. Keep TTS → A2V (strategy B) as the fallback for voice consistency and don't build it yet.

## 3. Motion and VFX

| Shot | What it tests | Result |
|---|---|---|
| 01 Himalaya establishing | Fire column, push-in | Stable. The column grows; clouds drift. |
| 02 Rishi tapas | Fire ring, steam, orbit | s42 good (steam bursts, subject readable). s7 drifts into a pull-out and fog swallows the rishi. |
| 03 Rishi close-up | Calm face, embers | **Eyes opened in the last ~2 s in both takes**, which spoils shot 10. Fixed (below). |
| 04 Svarga trembles | Fire surge through clouds, attendants | Good. The fire blooms into a mushroom of flame. |
| 05 Indra throne | Push-in, vision orb, speech | Good; the face holds through the push-in. |
| 06 Indra and apsara | Two-shot dialogue, static camera | Good; the camera stays nearly static. s7 has livelier gestures. |
| 07 Apsara descends | Floating, cloth flowing upward | Good. The dupatta and braid move convincingly. s7 ends closer on her face. |
| 08 Dance (wide) | Spinning dance, skirt flare, tracking | Good. She passes in front of the rishi with no merging. The dance is gentle spins rather than real choreography. |
| 09 Dance (close-up) | Seductive look, whisper | Good. In s7 the face drifts rounder and younger by the end; s42 holds it. |
| 10 Eyes open | Eruption, glowing eyes, roar | Strongest shot: the fire fills the frame and the roar lands. |

**Shot 3 fix:**
- Motion prompt: added "His eyes stay firmly closed the whole time in deep meditation, eyelids still".
- New per-shot negative `ltx_negative_extra`: "eyes opening, open eyes, looking at camera, blinking". `mm.experiments styles` now reads this field.
- Result: both new takes keep the eyes closed for all 8 s.

## 4. Character consistency (baseline for Phase 2)

There are no LoRAs. Each shot repeats the same character description, written into the prompt.

- **Within a clip:** identity holds in every picked take. LTX I2V doesn't introduce drift on its own beyond the late-clip rounding of the apsara's face in 09 s7.
- **Across shots:** the drift comes from the **keyframes**.
  - **Rishi:** shot 10 looks heavier, older and has a different face from shots 2–3. This is the most visible break in the cut.
  - **Apsara:** the costume stays steady (emerald choli, gold dupatta, jasmine braid). The lower garment varies, though: harem pants in 06–07 and a flared skirt in 08. The face varies moderately between shots.
  - **Indra:** consistent between 05 and 06 (crown, blue cape, beard).
- **Conclusion:** Phase 2's first job is **image-model character LoRAs, or reference-image conditioning, for the keyframes**. A video LoRA isn't needed to keep identity steady inside a clip.

## 5. Edit and audio

- LTX outputs **1280×704**, not 720, because it rounds to multiples of 32. Upscale or pad in the final post pass (Phase 6).
- Loudness ranges from −33.6 LUFS (07, bells and wind) to −7.7 LUFS (10, the roar). Without correction the cut's loudness range is 20.8 LU. `mm.roughcut --lufs -16` normalizes each clip, bringing the cut to −15.4 LUFS with a 5.0 LU range. That flattens the drama somewhat, so a proper mix belongs in Phase 5.
- Hard cuts every 8 s feel even and mechanical. The real edit should trim each shot to its action: 4–6 s for establishing and reaction shots, full length for dialogue.

## 6. Takeaways

1. The stack can already make a watchable 80 s story at ~$1 of GPU. Epic CG keeps realistic faces through motion.
2. Native dialogue is reliable for single and two-speaker lines. Voice identity across shots hasn't been tested.
3. The main quality gap is **character identity across keyframes**, and that is what Phase 2 should address.
4. Prompt rules learned here are in `prompts/library.json`:
   - Keep closed eyes closed explicitly, in the prompt and in the negative.
   - Quote the dialogue and name the speaker.
   - Normalize loudness for each clip.

---

## 7. Second pass after the user's review (rough cut v2)

`renders/story/rishi_apsara_roughcut_v2.mp4`: 69.8 s, −15.4 LUFS, loudness range 5.6 LU. The picks are `clip_picks_v2` in `prompts/rishi_apsara.json`. 24 more clips, ~$1.25.

| User's note | Cause | Fix tried | Result |
|---|---|---|---|
| The fire should rise to meet heaven, not lift the palace (04) | The motion prompt said "tilt up" and "palaces shake"; the keyframe already showed the fire at the palace | Static camera, a "palaces stay fixed" negative, then a new keyframe with the fire still below the palace | **Partly fixed.** The fire now rises to the palace base in ~2 s, but then the palace sinks and the fire pierces through it. **Trimmed to 3.0 s.** |
| The rishi's fire should be a haze joining the column to heaven (02/03/08/10) | The keyframe prompt asked for "a ring of flames" | Keyframe prompt: an aura of fire haze that gathers into a column rising into the sky; negative "small ring of fire" | **Fixed** in all four shots, so the column now links shots 01 → 02 → 04. |
| The apsara is Indra's servant and shouldn't stand so close (06) | The prompt put them side by side | Indra on a raised throne; the apsara bows with joined palms several paces below; negative against standing together | **Fixed.** She bows, then looks up with the sly smile; both lines are still spoken (ASR 1.0). |
| The dance shortens her and makes her cartoonish (08) | Fast spinning in a wide shot | Slower dance, "tall slender realistic adult proportions", negative against squat or chibi bodies | **Better** proportions. Side effect: she crossed in front of the rishi and he **vanished**, so the prompt now keeps her on her side with a static camera. **Fixed.** |
| Face glitches while she dances (09) | The push-in makes the face larger than in the keyframe, so LTX invents a different face (a bindi appears, the face gets rounder) | Static camera with a "zoom in" negative; also tried 1536×864 | **Not fixed.** LTX still pushes in, and 864p drifts the same way. **Trimmed to 5.0 s**; the line ends at 4.4 s. |
| Face glitches when the rishi roars (10) | Same push-in drift, plus an extreme expression | Same as shot 9 | **Not fixed** (heavier, older face by 5–8 s). **Trimmed to 5.5 s**; the roar ends at 5.0 s. |

**What we learned** (also added to `prompts/library.json`):
1. **Push-ins on faces cause identity drift, and LTX pushes in whatever the prompt says.** Higher resolution doesn't help. Real fixes:
   - (a) Anchor the end frame too. LTX supports guide keyframes at later frames; this needs a graph change in `mm/ltx.py`.
   - (b) A video LoRA per character (Phase 2).
   - For now: trim.
2. **When one character crosses in front of another, the hidden one vanishes.** Keep characters on separate sides of the frame.
3. **Give the keyframe room for the action.** If the keyframe already shows the end state, LTX moves the scenery instead.
4. **Large scenery doesn't hold still.** Anchoring both the first and last frames is the fix here as well.

**Remaining work, assigned to later phases:**
- End-frame or multi-keyframe conditioning goes to Phase 2. It's the next thing to build, because it fixes items 1 and 4.
- Character identity across keyframes (the rishi in 10) is Phase 2.
- Final-tier quality (two-stage dev pipeline plus upscaler) is Phase 4.
