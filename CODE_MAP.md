# Code map and integrity checks

This guide maps the existing app without changing how its screens or features behave.

## Runtime layout

- `index.html` defines the visible screens and settings controls.
- `app.js` owns browser state, validation, persistence calls, media libraries, game timing, AI chat, and the settings UI. Section banners in the file mark these areas.
- `server.py` serves the app and exposes the local filesystem-backed APIs in `data/`. It proxies KoboldCpp and Kokoro requests.
- `app.py` starts the local server and the PySide6 WebEngine desktop window. Desktop and worker-thread exceptions are written to `data/trance.log`.
- `extensions/<id>/app.js` contains optional feature modules. Each registers with `TranceExtensionHost`; the host API is returned from the extension-host block in `app.js`.
- `sw.js` caches only the app shell and remote fonts.

## Important flows

- **Settings:** defaults `D` → controls `G` → `normalizeStoredSettings` → `SETTINGS_SCHEMA_PROBLEMS` → `/api/state/settings`. Add a control to both the defaults and its settings group. Validation repairs are listed in the app diagnostics report.
- **Game start:** `start()` pauses extension chat-memory work, summarizes pending home chat, prepares the selected game extension, then initializes the session. Optional memory failures must remain fail-open; failures to prepare a game mode are reported to the user.
- **Home chat:** `sendHomeChatMessage()` requests optional vector recall, includes saved chat/persona summaries, calls `aiChat()`, saves the exchange, then indexes memory asynchronously. If the embeddings endpoint fails, Vector Memory disables itself and the regular summary path remains available.
- **Streaming chat:** the `homeChatStream` setting affects only home-chat replies. Browser SSE parsing is in `readChatEventStream()` and the local relay is `/api/ai/chat/stream` in `server.py`. Other AI work stays on the existing buffered request path.
- **App diagnostics:** Settings → Settings backup → App diagnostics captures client errors, settings schema repairs, persistence errors, server health, extension registration, and enabled game/vector-memory modes. The server log is `data/trance.log`.

## Checks

From the project directory:

```sh
node --check app.js
node --check sw.js
find extensions -name '*.js' -print0 | xargs -0 -n1 node --check
python3 -m py_compile app.py server.py
```

These checks validate syntax only. Use the in-app diagnostics for runtime API, extension, and persistent-data status.
