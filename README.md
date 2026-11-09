# Trance

Trance is a local rhythm, media, gallery, and AI companion app. It runs as a PySide6 desktop shell over a local Python service. User data stays in `data/`; keep that folder when moving or backing up the app.

## Run

```sh
./trance.sh
```

Development:

```sh
python3 app.py
```

Required desktop packages: Python 3, PySide6, and Qt WebEngine.

## Feature inventory

### Home and AI

- Local home chat with saved history, streaming replies, offline fallback, clear-chat, and optional Kokoro speech.
- AI character card, user profile, avatars, notes, session summaries, decision diary, and capability-aware recommendations.
- Imported SillyTavern chats and lorebooks with bounded context injection.
- Optional Vector Memory for relevant home-chat recall; it pauses during sessions.
- AI commentary on session images, spoken recaps, captions, expression sprites, queue limits, and low-VRAM safeguards.
- Custom AI chat layouts: **Chat focus**, **Balanced**, and **Compact**.

### Settings

- Appearance: themes, accent, font, text size, page width, card radius, and home background.
- Session: duration, work/rest timing, beat timing, haptics, and failure rules.
- Visuals: slideshow order, image/video behavior, framing, ambient background, transitions, motion, and EffectsCraft Motion Studio.
- Media: image/video folders, stickers, character image, audio library, gallery selection, rotation, deletion, favorites, fullscreen preview, and search/filtering.
- Effects: taunt words, beat words, onomatopoeia, stickers, color, typography, placement, wiggle, and sound timing.
- Audio: soundtrack, voice clips, ducking, gaps, priorities, trimming, normalization, and volume.
- AI: KoboldCpp/Kokoro endpoints, GPU policy, edit-time budget, context, memory, lorebook, commentary, speech, model tuning, captions, and chat layout.
- Extensions: enable/disable and configure installed modules.
- Maintenance: diagnostics, settings export/import, library cleanup, and reload.

### Rhythm modes

- Stroke Beat: detected song beats, configurable key, hit window, difficulty, density, sync offset, gamepad support, score, combo, health, and session summary.
- Note Rush/FNF mode: four lanes, keyboard or receptor taps, difficulty, health, combo, score, instant-loss rules, and results.
- Practice Goals: goals and progress tracking.

### Extensions

- AI Control Center: memory inspector, recommendations, GPU profile actions, runtime capabilities, and resource state.
- Diagnostics & Benchmark: bounded server, resource, extension, and responsiveness checks.
- Expression Sprites: local mood detection, randomized non-repeating sprites, and debounced updates.
- Vector Memory: optional embeddings-backed chat recall.
- Stroke Beat, FNF/Note Rush, and Practice Goals.

### Local service functions

- Static app shell and extension serving.
- Health and diagnostics: `/api/health`.
- Persistent state: `/api/state/settings`, `history`, `presets`, `deleted`, `memory`, `memorybooks`, `galleryprefs`, `homechat`, `chatmemory`, and `vectormemory`.
- AI relay: `/api/ai/chat`, `/api/ai/chat/stream`, `/api/ai/context`, `/api/ai/tokens`, `/api/ai/embeddings`, and `/api/ai/speech`.
- Media library: `/api/library`, `/api/library/<id>`, and `/api/library/purge`.
- Extension discovery: `/extensions/manifest.json` and `/api/extensions`.

### User actions

Start, pause, resume, finish, or give up a session; choose folders and media; rescan, activate, deactivate, rotate, favorite, preview, fullscreen, and remove gallery items; edit word lists; process audio; import/export settings and character cards; import chats and lorebooks; clear chat, memory, history, avatars, backgrounds, and library metadata; run diagnostics and benchmarks; apply AI/GPU profiles; change themes, fonts, accent, layout, and accessibility options; enable or configure extensions; and reload or exit the app.

## AI backend

Trance connects to a separate KoboldCpp server at the configured URL and optionally to Kokoro for speech. Trance-side GPU profiles change request policy, context, queueing, and image limits; KoboldCpp still controls CUDA layers and actual VRAM allocation.

For an NVIDIA setup, start KoboldCpp with CUDA enabled and AutoFit GPU layers. For CPU-only inference, use KoboldCpp's `--usecpu`. The optional Vector Memory extension uses an embeddings endpoint and stores its index in `data/vectormemory.json`.

## Media

- Put expression images in `sprites/`; filenames become expression names.
- Add images, videos, music, voice clips, stickers, and sound effects through the Media settings.
- FFmpeg is used when available for video previews.

## Checks

```sh
node --check app.js
node --check sw.js
find extensions -name '*.js' -print0 | xargs -0 -n1 node --check
python3 -m py_compile app.py server.py
```

Runtime checks are available under **Settings → Maintenance → App diagnostics**. The local service exposes `/api/health` for a basic backend check.

## Project map

- `index.html` — app shell, screens, styles, settings markup, themes, and AI layout styles.
- `app.js` — settings schema, persistence, media/gallery UI, session engine, AI pipeline, home chat, diagnostics, and extension host.
- `server.py` — local static server, persistent state APIs, media library APIs, health reporting, and KoboldCpp/Kokoro proxy.
- `app.py` — PySide6 desktop window and local server launcher.
- `extensions/` — optional feature modules and manifests.
- `sw.js` — app-shell service worker.
