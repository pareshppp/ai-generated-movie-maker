# Short AI Anime Movie Maker: Phased Implementation Plan (LTX-2.5)

**Goal:** Produce 1–5 minute anime-style short films from a written story.

> **Target update (2026-11-07):** the real end goal is story → movie in the style of xianxia **2.5D/3D "donghua"** AI videos (reference: https://youtu.be/VP4YmwfNXD4, "Reign AI Anime"). It has **voiced character dialogue**, is **mostly animated clips**, and starts with **short** videos, expanding later. Phase 1 (2D anime) built the tooling and defaults. **Phase 1b** evaluates the donghua style, and the style used from Phase 2 on is decided at the Phase 1b gate. The pipeline goes script → shot list → keyframes → LTX-2.5 video+audio clips → edit → final render, with consistent characters and style across shots.

**Core model:** Lightricks **LTX-2.5** (open weights, released 2026-08-11)
- 22B diffusion core with a Gemma4-12B text encoder. Generates video and audio together.
- Two checkpoints: **dev** (full quality, many steps) and **distilled** (few steps, fast).
- Modes: T2V, I2V, V2V, text-to-audio, audio-to-video. **Native multi-shot** keeps characters, sets and voices consistent across cuts.
- Tooling: ComfyUI templates (day one), `ltx-pipelines`, Diffusers, and `ltx-trainer` for LoRA training.
- Most LTX-2.3 LoRAs and IC-LoRAs work on 2.5. Structural IC-LoRAs need `ICLoraPipeline` and the **distilled** checkpoint.

> Check the details above against the official model card and GitHub release before locking settings. Max clip length, VRAM needs and exact file names come from secondary sources.

---

## Architecture at a glance

```
story.md ──► [LLM: script + shot list JSON] ──► [Keyframe gen: anime image model + char LoRAs]
                                                        │
                                                        ▼
                         [LTX-2.5 I2V / multi-shot (+ style/char LoRAs, audio)]  ◄── RunPod GPU (ComfyUI API)
                                                        │
                                                        ▼
               [Review: pick takes, regen] ──► [Post: upscale, interp, audio mix, subs] ──► final.mp4
```

Proposed repo layout:

```
infra/        RunPod templates, Dockerfile, model download scripts
workflows/    ComfyUI workflow JSONs (exported in API format)
mm/           Python package: schema, orchestrator, comfy client, assembly
  schema.py     Pydantic models: Project, Character, Scene, Shot, Take
  comfy.py      ComfyUI HTTP/WebSocket client (queue, poll, fetch outputs)
  plan.py       story → script → shot list (LLM)
  render.py     batch keyframe + video jobs, seeds, caching
  assemble.py   ffmpeg / OTIO timeline → final render
projects/<name>/  story.md, shots.yaml, characters/, renders/, edit/
```

---

## Phase 0: Infrastructure (RunPod) · ~2–3 days

**Goal:** A GPU pod that can be rebuilt on demand, with LTX-2.5 running in ComfyUI and reachable from local scripts.

- [x] Pick a GPU. *(2026-11-07: RTX 6000 Ada 48 GB, distilled/dev int8, $0.99/hr, US-WA-1.)* Plan for **80 GB** (H100 / A100 80GB) for the dev checkpoint at high resolution. Test fp8/quantized distilled on 48 GB (L40S / RTX 6000 Ada) as the cheaper iteration tier.
- [x] Create a **network volume** *(150 GB `h70f1jyt5h` in US-WA-1; can grow later)* (~200–300 GB) for weights: LTX-2.5 dev and distilled, Gemma4-12B encoder, VAE, upscalers, the anime image model and LoRAs. Weights then survive pod restarts.
- [ ] *(Partly done: official ComfyUI template, upgraded on the volume to ComfyUI v0.44.2, plus ComfyUI-LTXVideo. Still missing: `ltx-pipelines`, `ltx-trainer`, and a pinned image.)* Build a Docker image or RunPod template: ComfyUI, ComfyUI-Manager, LTX nodes, `ltx-pipelines`, `ltx-trainer`, ffmpeg. Pin versions.
- [x] `infra/download_models.sh`: idempotent HF downloads to the volume (needs `HF_TOKEN`).
- [x] *(Runpod proxy + Caddy basic auth, 2026-11-07)* Expose the ComfyUI API (port 8188) through the RunPod proxy or an SSH tunnel. Add auth if it is public.
- [x] Write setup notes, cost per hour and start/stop commands in `runpod.md`.

**Exit criteria:** From your laptop, `python -m mm.comfy smoke` queues the stock LTX-2.5 T2V template and downloads a 5 s clip with audio.

---

## Phase 1: Single-shot anime proof of concept · ~3–5 days

**Goal:** Find out how good LTX-2.5 anime looks out of the box, and settle on default parameters.

- [x] *(I2V wins; docs/phase1.md §2)* Run T2V vs I2V on the same anime prompts. Expect I2V from a strong anime keyframe to win on style.
- [x] *(Qwen-Image-2.1 + 8-step LoRA; Anima Turbo as the fast alternative; §1)* Choose a **keyframe image model** for anime stills, such as an Illustrious/NoobAI-class SDXL anime checkpoint, Flux or Qwen-Image with an anime LoRA. Compare 3–4 options.
- [x] *(Default: distilled, 1280×720, 8 s, strength 0.9, ~$0.05/clip; dev single-pass is worse; §3)* Sweep parameters: resolution (start at 1280×720 / 24 fps), duration (4–10 s), steps, CFG, distilled vs dev. Record time and cost per second of output.
- [x] *(prompts/library.json, each fragment tested; §4)* Build a **prompt library**: camera moves (pan, dolly, static), anime-specific motion (hair and cloth, speed lines, limited-animation feel), and negatives that suppress photoreal drift.
- [x] *(Levels checked; ASR: 12/13 lines transcribed exactly; lip-sync still to check by eye; §5)* Test native audio: ambient sound, a short spoken line, SFX. Note quality and lip-sync accuracy.

**Exit criteria:** A `workflows/i2v_anime_v1.json` workflow plus a settings doc that reliably gives clean 8 s anime clips. Five sample clips you'd be happy to put in a film.

**Status (2026-11-07): done**, apart from the 5 anime samples, which were skipped by decision. Samples will be made in the donghua style in Phase 1b. Findings: [docs/phase1.md](docs/phase1.md).

---

## Phase 1b: Donghua (2.5D xianxia) style evaluation · ~1–2 days

**Goal:** Check that the stack produces the target look, with voiced dialogue, at an acceptable cost. Then lock the style for Phase 2 onward.

- [ ] Donghua prompt set (`prompts/donghua_eval.json`): hero portrait, sect courtyard with a crowd, VFX power-up (glowing energy, spirit beast), floating-palace establishing shot, two-character dialogue. Original characters only.
- [ ] Keyframes: Qwen-Image-2.1 with a donghua/CG style prefix. Add 1–2 more models only if it disappoints.
- [ ] I2V at the Phase 1 defaults (720p, 8 s). Check that the T2V vs I2V finding still holds for this style.
- [ ] **Dialogue:** short spoken lines in 1- and 2-character shots. Check intelligibility (Whisper ASR on the pod, plus manual listening) and lip-sync. Compare with a fallback of TTS voice + lip-sync pass if native audio falls short.
- [ ] VFX-heavy motion (energy orbs, sword qi, glowing auras): check stability.
- [ ] Style library: donghua style and negative fragments in `prompts/library.json` (negatives flip: remove "3D/CGI", add "flat 2D, cel shading, sketch").
- [ ] 5 donghua sample clips (the Phase 1 exit criterion, in the target style).

**Gate:** pick the style for Phase 2 onward (expected: donghua). Re-scope Phase 2 (character consistency) and Phase 5 (voices) to that style.

*Shelved:* the Blender/Unreal hybrid (3D sets/blocking → LTX V2V). This genre is purely generative. Revisit only if location consistency becomes a blocker.

---

## Phase 2: Style and character consistency · ~1–2 weeks

**Goal:** The same characters and art style in every shot. This phase is the main quality lever.

- [ ] **Character bible** per character: name, design notes, palette and turnaround. Generate front, 3/4, side and expression sheets with the image model, then curate 20–40 images.
- [ ] Train **image-model character LoRAs** so keyframes stay consistent.
- [ ] Train an **LTX-2.5 style LoRA** with `ltx-trainer` on 50–200 short anime-style clips you have rights to, or on your own generated, curated clips. Consider per-character video LoRAs for hero characters.
- [ ] Test **native multi-shot** generation as the main way to keep consistency inside a scene. Compare it against independent I2V shots that share a keyframe set.
- [ ] Evaluate IC-LoRAs for control: pose, depth, layout-to-render. They need the distilled checkpoint.
- [ ] Add a consistency check: score each clip's frames against the character reference with CLIP/DINO embeddings or a VLM, and flag drift.

**Exit criteria:** A 3-shot scene of 2 characters talking, where a viewer can't tell the shots were generated separately.

---

## Phase 3: Story → shot list pipeline · ~1 week

**Goal:** Turn a story into a machine-readable production plan.

- [ ] Define the schema (`mm/schema.py`) for a Shot: `id, scene, duration_s, shot_type, camera_move, characters[], location, action, dialogue[{speaker, line, emotion}], keyframe_prompt, motion_prompt, audio_prompt, seed, multishot_group`.
- [ ] LLM step (Claude): `story.md` → screenplay → `shots.yaml`. Enforce the style guide and a character registry. Validate against the schema and estimate total runtime. LTX-2.5's duration estimator can help size dialogue shots.
- [ ] Make `shots.yaml` the human-editable source of truth. Every later stage reads it and caches by content hash, so editing one shot only re-renders that shot.
- [ ] **Storyboard render:** a low-res keyframe per shot, compiled into a contact sheet (HTML or PDF) for review before spending GPU time on video.

**Exit criteria:** `mm plan projects/demo` writes `shots.yaml` and a storyboard for a 1-minute story. You can edit the YAML and re-run it.

---

## Phase 4: Render orchestration · ~1 week

**Goal:** Render a whole film's shots unattended, with retries and multiple takes.

- [ ] `mm/comfy.py`: inject parameters into API-format workflow JSON, queue jobs, track them over WebSocket, download outputs, and capture metadata (seed, model hashes, timing).
- [ ] `mm render`: keyframes first, then video. Produce **N takes per shot** (e.g. 3 seeds). Resume after a failure and skip shots already cached.
- [ ] Two tiers: **draft** (distilled, 720p) for blocking and **final** (dev or high-res, plus upscale) for picked takes only.
- [ ] **Take-review UI** (a small local web page): a grid of takes per shot with pick / regen / edit-prompt buttons. Picks are written back to `shots.yaml`.
- [ ] Log cost per shot (GPU-seconds × rate) and track a total budget.

**Exit criteria:** A 1-minute, 10–15-shot short renders overnight at draft quality without manual steps.

---

## Phase 5: Voice, music and sound · ~1 week

**Goal:** Believable dialogue and a full soundtrack.

Choose a dialogue strategy based on the Phase 1 audio tests:
- **A. Native LTX-2.5 audio:** dialogue is generated with the video. Simplest, but controlling voice identity is harder. Lean on multi-shot to keep voices consistent.
- **B. TTS first, then audio-to-video:** generate lines with a fixed voice per character (TTS or voice-cloning model), then drive LTX-2.5 A2V so lip movement follows the audio. Gives better voice consistency and timing control.

Then:
- [ ] Per-character voice registry: voice ID, pitch and style notes.
- [ ] Music: generate or license a score per scene. Add stems and ducking under dialogue.
- [ ] SFX and ambience: LTX native audio or a separate text-to-audio pass.
- [ ] Loudness-normalized mix (e.g. −14 LUFS for web).

**Exit criteria:** A dialogue scene where each character's voice stays the same throughout and lip sync is acceptable.

---

## Phase 6: Edit, post-processing and delivery · ~1 week

**Goal:** Turn picked takes into a finished film.

- [ ] `mm assemble`: build the timeline from `shots.yaml` with trims, simple transitions and J/L audio cuts. Export **OTIO/EDL** so you can finish in DaVinci Resolve if you want.
- [ ] Post chain: upscale to 1080p or 4K, frame interpolation only where it helps (anime often looks better at 24 fps or on 2s), color grade. HDR/EXR output is optional.
- [ ] Subtitles generated from the dialogue in `shots.yaml` (SRT, optionally burned in).
- [ ] Title and credits cards. Final encode presets for web, social vertical (1080×1920) and archive.

**Exit criteria:** `mm build projects/demo` produces `final.mp4` plus `.srt` and `.otio`.

---

## Phase 7: Quality, scale and polish · ongoing

- [ ] Automated QA pass: a VLM flags artifacts, character drift, extra limbs and text glitches, then auto-regenerates the worst shots.
- [ ] Prompt and LoRA versioning tied to each render's metadata, so any shot can be reproduced.
- [ ] Cost tuning: spot pods for draft batches, on-demand for finals, auto-stop when the queue is empty.
- [ ] Optional: a serverless RunPod endpoint for burst rendering, plus a project dashboard.
- [ ] Ship 2–3 shorts and feed what you learn back into the prompt library and the LoRAs.

---

## Milestones

| Milestone | Phase | Deliverable |
|---|---|---|
| M1 | 0–1 | First good 8 s anime clip from the API |
| M2 | 2 | Consistent 3-shot, 2-character scene |
| M3 | 3–4 | 1-min short, draft quality, rendered unattended |
| M4 | 5–6 | 1-min short with voices, music and subtitles, final quality |
| M5 | 7 | 3–5 min short; repeatable production in under a week |

## Key risks

| Risk | Mitigation |
|---|---|
| Output drifts photoreal / off-style | I2V from anime keyframes + style LoRA + negative prompts |
| Character drift between shots | Multi-shot groups, character LoRAs, embedding-based QA |
| VRAM / cost blow-up with the 22B model plus a 12B encoder | Distilled draft tier, fp8, render finals only for picked takes |
| Weak lip sync or inconsistent voices | Strategy B (TTS → A2V); keep close-ups short |
| Licensing (base model, LoRAs, training data, voices) | Check the LTX License for commercial use; train only on data you have rights to |

## Open decisions

1. Target length and output format (16:9 vs vertical) for the first short.
2. Budget per film (GPU hours), which decides the GPU tier.
3. Native audio vs TTS → A2V for dialogue (decide after Phase 1).
4. Whether a specific anime substyle is needed, which decides how early to train the style LoRA.
