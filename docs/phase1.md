# Phase 1: single-shot anime proof of concept, findings

Living doc for Phase 1 of [PLAN.md](../PLAN.md). Pod and infra details: [runpod.md](../runpod.md).
Phase 1 cost is the pod rate, **$0.99/hr** (RTX 6000 Ada 48 GB).

## How to reproduce
```bash
python -m mm.keyframes compare                       # 6 models x 4 shots x 2 seeds -> renders/keyframes/index.html
python -m mm.experiments t2v_vs_i2v --kf-models qwen21_8s,anima_turbo,animagine
python -m mm.experiments sweep --shot closeup --kf-model qwen21_8s
```
- Prompts: `prompts/keyframe_eval.json`. It has 4 shots (closeup, action_wide, environment, two_shot), each with a `tags` prompt, a `natural` prompt and a `motion` prompt.
- Code:
  - `mm/keyframes.py`: per-model still-image graphs.
  - `mm/ltx.py`: LTX-2.5 T2V/I2V graphs, distilled or dev.
  - `mm/experiments.py`: the experiment runner and HTML reports.
- Outputs go to `renders/`, which is gitignored.

---

## 1. Keyframe image model (2026-11-07)

Setup: 1344×768 (16:9) images, 4 shots × 2 seeds per model. Each model ran with its card-recommended settings. Times are on the RTX 6000 Ada, warm, measured end to end through the API.

| Model | Settings | Median s/image | License | Verdict |
|---|---|---|---|---|
| **Qwen-Image-2.1 int8 + 8-step LoRA** (`qwen21_8s`) | 8 steps, cfg 1, euler, natural-language prompt + "Anime illustration, 2D cel-shaded…" prefix | 14.8 | Qwen Research | **Best prompt adherence.** Only model that keeps attributes bound per character in the two-character shot. Grounded, flat cel shading that looks like a TV anime frame. Nearly identical to 25 steps. |
| Qwen-Image-2.1 int8 (`qwen21`) | 25 steps, cfg 1, euler | 30.8 | Qwen Research | Same quality as the 8-step version at 2× the time, so not worth it. |
| **Anima Turbo v1.1** (`anima_turbo`) | 10 steps, cfg 1, euler, tags | **7.6** | circlestone NC | Fastest. Clean, saturated TV-anime look; excellent environments. Attribute binding mostly right. |
| Anima Aesthetic v1.1 (`anima`) | 30 steps, cfg 4, er_sde, tags | 15.3 | circlestone NC | Nice line work, but attributes leak between characters (the boy got the red scarf). |
| Animagine XL 4.0 Opt (`animagine`) | 28 steps, cfg 5, euler_a, tags + quality tags | 10.0 | OpenRAIL++ | Glossy illustration with heavy glow and lens flare. Attributes leak (both characters wear scarves), and the "talking" shot became a kiss. |
| NoobAI-XL 1.1 (`noobai`) | 28 steps, cfg 5, euler_a, tags | 11.3 | FAIPL + NC | Hazy, washed-out colors; generic characters; attribute errors. |

**Conclusions**
- Prompt adherence matters most for a story pipeline, because shot lists describe *who wears what, where*. Qwen-Image-2.1 is clearly ahead on this. Tag-based SDXL models leak attributes between characters.
- The Phase 2 character LoRAs will be trained on whichever model wins I2V. Anima's model card recommends training LoRAs on Anima-Base. Qwen-Image LoRA tooling is not checked yet.
- Shortlist for the I2V test: `qwen21_8s`, `anima_turbo`, `animagine` (three distinct styles).

## 2. T2V vs I2V (2026-11-07)

Setup: 4 shots × (1 T2V + I2V from 3 keyframe models) = 16 clips. Distilled model, 960×544, 5 s at 24 fps (121 frames), seed 42, I2V `img_strength` 0.9, the `motion` prompt from the prompts file. Report: `renders/exp/t2v_vs_i2v/index.html`.

**Speed and cost (warm, distilled, 960×544, 5 s):** about 60 s per clip, i.e. **~12 s of render time per output second, ~$0.016 per 5 s clip.** The two-character shot took 68–78 s. The first clip after a model swap took 156 s, because the LTX weights reload after the image models.

| Shot | T2V | I2V (qwen21_8s) | I2V (anima_turbo) | I2V (animagine) |
|---|---|---|---|---|
| closeup (push-in + head turn + line) | Wrong design: long-haired, older-looking, 2.5D. Face melts at the end | Correct design; **face smears in the final ~1 s** | Strongest end-of-clip face distortion | Most stable face, glossy style |
| action_wide (run, tracking cam) | Tiny figure, flat | Stable runner and attributes; background redraws | OK | Background morphs into a different street; rubble-like splash |
| environment (slow pan) | Good | Good | Good | Good |
| two_shot (dialogue) | Attributes lost (no scarf, T-shirt instead of hoodie) | **Boy dissolves into a ghost in the last frames** | Stable | Stable |

**Conclusions**
- **I2V is clearly better than T2V** for style and character fidelity: the clip keeps the keyframe's design and art style. T2V drifts toward a generic 2.5D look and drops attributes. This confirms the plan: keyframe first, then I2V.
- **The main failure is identity breakdown late in the clip with large motion on faces or people** (push-in, head turns, ghosting). Low-motion shots are clean. The sweep tests whether resolution, image strength or the dev model fixes it.
- The keyframe model matters less than expected for motion stability. The Qwen keyframes animate well except for the ghosting case. Re-test across seeds before drawing conclusions from single samples.

## 3. Parameter sweep (2026-11-07)

Shot: `closeup` (the hardest case: face, push-in + head turn + spoken line), I2V from the `qwen21_8s` keyframe, seed 42. One factor changed at a time from the base: distilled, 960×544, 5 s, strength 0.9. Report: `renders/exp/sweep_closeup_qwen21_8s/index.html`.

| Variant | Last-frame face | Render | s / output s | $ / clip |
|---|---|---|---|---|
| base (960×544, 5 s) | ❌ smears | 65 s | 13.0 | 0.018 |
| **1280×720** | ✅ clean, sharp | 97 s | 19.4 | 0.027 |
| **8 s** | ✅ clean | 94 s | 11.8 | 0.026 |
| **10 s** | ✅ clean | 117 s | 11.7 | 0.032 |
| img_strength 0.7 | ❌ smears | 59 s | 11.9 | 0.016 |
| img_strength 1.0 | ❌ smears | 60 s | 12.1 | 0.017 |
| dev, 30 steps, cfg 3.5 | ❌ worst: soft or blurry | 329 s | 65.8 | 0.091 |
| dev, 30 steps, cfg 2.5 | ❌ soft, smeared | 290 s | 58.0 | 0.080 |
| dev, 30 steps, cfg 5 | ❌ soft, smeared | 282 s | 56.5 | 0.078 |

**Conclusions**
- **The late-clip identity breakdown depends on motion speed and resolution, not image strength.** At 5 s, the push-in plus head turn is squeezed into too few frames. The same action at 8–10 s, or at 720p, keeps the face intact.
- **Cost per output second is nearly flat with duration** (~11.7–13 s of render per second at 960×544). 720p costs ~1.6× more per second.
- **dev, run through a plain `LTXVScheduler` single pass, is ~5× slower and worse at every CFG tested.** Use distilled for everything for now. The dev model presumably needs the official two-stage pipeline (dev pass + distilled-LoRA refine + latent upscaler), so revisit it later as a "final render" option.
- `img_strength` 0.7 vs 0.9 vs 1.0 made no visible difference here. Keep 0.9.
- Candidate default: **distilled, 1280×720, 8 s (193 frames), strength 0.9**.

**Confirmation (2 failure shots × 2 seeds at the candidate default), `renders/exp/confirm_720p_8s/`:** all 4 are clean.
- Both closeups end the push-in on a sharp, intact face.
- Neither two-shot ghosts, and the acting follows the prompt (she laughs, he looks away embarrassed).
- Render times: 159–227 s per 8 s clip, i.e. **~20–28 s of render per output second, ~$0.045–0.06 per clip**.
- **≈ $5.50–7.50 of GPU time per finished minute**, before retakes.

**➡ Default settings:** distilled int8, 1280×720, 8 s at 24 fps (193 frames), I2V strength 0.9, img_compression 18, euler_ancestral, the official 8-step sigmas, cfg 1.0.

## 4. Prompt library (2026-11-07)

`prompts/library.json` holds style, camera, motion, audio and negative fragments, each with a tested status. The test: 20 clips, I2V from `qwen21_8s` keyframes, 960×544, 8 s, distilled, each fragment on its own. Camera moves were tested on the environment keyframe and motions on the closeup. Report: `renders/exp/library_camera_motion_audio/index.html`. ~95–100 s per clip, $0.026–0.036.

| Category | Works | Weak | Fails |
|---|---|---|---|
| Camera | push-in, tilt-up, pan-left | static (still drifts forward), pan-right, tracking-side (needs a moving subject) | pull-out, orbit (both became generic drift) |
| Motion | idle, wind in hair/cloth, head turn, run + **anime speed lines** (they actually appear), ambient environment | — | **walk from an upper-body keyframe** (face disintegrates) |

**Takeaways**
- LTX-2.5 has a strong default **slow forward drift**. Push-in is the most reliable camera move. For locked-off shots, freeze or trim in the edit.
- Big body motion needs a keyframe framed for it: a walk needs a full-body shot.
- One camera move and one main action per shot (already in the library's rules of thumb).

## 5. Native audio (2026-11-07, levels only)

Mean loudness by clip type (volumedetect): dialogue −27.5 dB, door-slam SFX −29 dB, rain −32 dB, nature ambience −39 dB, "quiet room tone" −47 dB, city ambience −51 dB (nearly silent). Motion clips with only "sound of wind" came out at −51 to −55 dB.
- Prompted audio events produce real, appropriately loud sound. Faint ambience prompts (city traffic, wind) produce near-silence.
- **Dialogue intelligibility (Whisper ASR check, 2026-11-07, `infra/asr.py` on the pod):** 13 clips with a prompted line. **12/13 transcribe exactly as the prompt line**: "We made it." ×6, "You really didn't know?" ×5, "Wait for me. I'm coming with you." The one miss was "He really didn't know." (one word wrong).
  - Native LTX-2.5 speech follows quoted lines very reliably.
  - Run it with: `scp infra/asr.py` to the pod, then `.venv-cu128/bin/python /workspace/asr.py <output dir or mp4s> --expect-json map.json`.
- **Still to verify by eye:** lip-sync accuracy and which character speaks in two-character shots.

---

## Phase 1 wrap-up (anime)

- ✅ T2V vs I2V: **I2V wins.**
- ✅ Keyframe model: **Qwen-Image-2.1 + 8-step LoRA**, with Anima Turbo as the fast alternative.
- ✅ Parameter sweep: **distilled, 1280×720, 8 s, strength 0.9**, about $0.045–0.06 per clip.
- ✅ Prompt library: `prompts/library.json`.
- ✅ Workflow: `workflows/i2v_anime_v1.json`.
- ◐ Audio: levels checked; intelligibility moved to 1b.
- ⏭ The 5 anime sample clips were **skipped by decision (2026-11-07)**. The real target is 2.5D donghua (xianxia), so sample clips will be produced in that style in Phase 1b.

---

## Phase 1b (in progress): donghua vs photoreal keyframes (2026-11-07)

Command: `python -m mm.keyframes styles` (prompts in `prompts/donghua_eval.json`). Model: `qwen21_8s`. 5 xianxia shots × 2 styles × 2 seeds = 20 images, all succeeded, ~14 s each warm. Report: `renders/keyframes_styles/index.html`.

- **The same seed gives the same composition and costume design in both styles.** Only faces, skin and materials change, so a design carries over between styles intact.
- **Donghua:** idealized CG faces, glossy materials, glowing VFX. Very close to the reference thumbnail's look. One stray unprompted glowing figure appeared in the courtyard shot.
- **Photoreal:** convincing live-action C-drama look (real faces, costume fabric, practical lighting). The golden spirit dragon VFX still composites believably.
- **Environments** (floating palaces) look almost the same in both styles. The style difference is mostly in the characters.
- **Not run yet:** I2V for both styles (`python -m mm.experiments styles --modes i2v`, 10 clips at 720p and 8 s, ~30 min) with the ASR dialogue check. Paused at the user's request.
