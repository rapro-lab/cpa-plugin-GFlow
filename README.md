# GFlow

Use Google Flow image and video models on the same EasyCPA port (8317 by default).

## CutFlow project and session binding (v0.1.12)

Adds a `gflow-project-control` control endpoint that a regular generation API key can call. Creating and querying projects does not call image or video models and does not require the client to store the management key. A new CutFlow project can bind a real Google Flow project; sessions inside that project share a fixed account and project, and consecutive image edits pass the chosen reference image explicitly from the client. When an account is invalid or its tier does not match, GFlow never silently switches accounts or rotates projects. The control model should be hidden from image/video pickers; the seven media models are unchanged.

Project creation is idempotent and persisted in SQLite. A timeout, cancellation or lost receipt marks the request "creation pending review" instead of rebuilding it automatically. Legacy project pools keep migrating; CutFlow-dedicated projects do not join other clients' rotation. See [project control protocol, parameter capabilities and permission boundaries](docs/project-control-v1.md).

## Adding an account: browser sync (recommended)

1. In GFlow click "Add account", then "Copy download address", paste it into the Chrome / Edge address bar and press Enter, download the **GFlow login helper** and extract it. Enable "Developer mode" on the Chrome / Edge extensions page and load the extracted folder.
2. In the same window click "Copy extension configuration". Open the browser extension, paste the configuration and fill in the current Google email.
3. Sign in to Google Flow in that browser, click "Save and sync" in the extension, then go back to GFlow and refresh the account list.

The EasyCPA embedded window blocks direct downloads and new-window navigation, so the download and source entries copy an address instead; the download address uses the extension package already installed on this machine and does not need GitHub. When copying is restricted, the address is shown for manual copying.

Afterwards you can sync with one click, or automatically every 60 minutes; automatic sync requires the browser to be running. Use a different browser profile for each Google account.

- [Extension GitHub and install instructions](https://github.com/daodeqing/cpa-plugin-GFlow/tree/main/browser-extension)
- [Download the GFlow login helper ZIP directly](https://github.com/daodeqing/cpa-plugin-GFlow/raw/refs/heads/main/downloads/GFlow-browser-extension.zip)

## When extension sync fails

The latest extension version is **1.3.4**. Download it again from "Add account" and overwrite the previous extension folder, then click "Reload" on the Chrome / Edge extensions page; the existing connection configuration is preserved. Repository updates do not automatically update a separate older folder extracted in the browser.

1.3.4 adds the `webRequest` permission: it reads the cookies actually carried by the quota request only inside the Flow tab created for that sync, and queries the quota once; the listener is removed and the temporary tab closed when the sync ends. Reload the extension after updating. The browser and the backend validate separately, and success is shown only when the backend passes and the account is saved.

Error messages and logs distinguish local connection, Google authentication, timeout, API changes and project preparation failures; cookies and keys are never printed. Real account sync and the backend quota query after saving have been verified on this machine; real generation has not been verified.

## Supported models and how to call them

GFlow exposes **7 core models (the media picker shows these 7, plus a control-only entry)**, which avoids dozens of variants flooding the client dropdown. When the client picks a feature parameter in the UI, the backend **auto-dispatches** to the underlying cartesian product model:

### 1. Core models selected by the client (7 media models)
- **Image models (3)**:
  - `gflow-nano-banana-pro`: Nano Banana Pro (GEM_PIX_2, auto-dispatch across 1:1, 16:9, 9:16, 4:3, 3:4 and 1K/2K/4K)
  - `gflow-nano-banana-2-lite`: Nano Banana 2 Lite (HARBOR_SEAL, auto-dispatch across 5 aspect ratios and 1K/2K/4K)
  - `gflow-nano-banana-2.1`: Nano Banana 2.1 (BELUGA, auto-dispatch across 5 aspect ratios and 1K/2K/4K)
- **Video models (4)**:
  - `gflow-omni-1.1-flash`: Omni 1.1 Flash (auto-dispatch across 4s/6s/8s/10s durations and landscape/portrait, with native support for multiple reference images)
  - `gflow-veo-3.1-lite`: Veo 3.1 - Lite (auto-dispatch across 4s/6s/8s durations and landscape/portrait)
  - `gflow-veo-3.1-fast`: Veo 3.1 - Fast (auto-dispatch across 4s/6s/8s durations and landscape/portrait)
  - `gflow-veo-3.1-quality`: Veo 3.1 - Quality (auto-dispatch across 4s/6s/8s durations and landscape/portrait)

### 2. Feature-based auto-dispatch
When the client selects one of the 7 models above, the backend resolves the dispatch from the parameters in the request:
- **Image dispatch**: from the client's `aspectRatio` (1:1, 16:9, 9:16, 4:3, 3:4) or the OpenAI `size` (such as 1024x1024, 1792x1024), and from `imageSize` (1K, 2K, 4K) or the OpenAI `quality` (standard, medium, hd, high), it dispatches to one of 45 underlying combinations. When nothing is specified it defaults to 16:9 standard 1K.
- **Video dispatch**: from the chosen duration (4s, 6s, 8s, 10s), the orientation (16:9 landscape, 9:16 portrait) and whether a reference image is attached (which switches to I2V/R2V automatically), it dispatches to one of 26 underlying video combinations. When nothing is specified it defaults to 8s 16:9 landscape.

### 3. Underlying cartesian variants (direct and function calls still work)
Every combination is still exposed underneath, so a third-party client or agent that sends a concrete variant name or calls it as a function is matched directly:
- **Image variants (3 base × 5 aspect ratios × 3 resolutions = 45)**:
  - Examples: `gflow-nano-banana-2.1-square-4k` (1:1 4K), `gflow-nano-banana-pro-portrait-2k` (9:16 2K), `gflow-nano-banana-2-lite-four-three` (4:3 1K).
- **Video variants (Veo 3 family × 3 durations × 2 orientations + Omni 4 durations × 2 orientations = 26)**:
  - Examples: `gflow-veo-3.1-fast-4s-portrait` (4s portrait), `gflow-omni-1.1-flash-10s-landscape` (10s landscape).
- The backend also accepts legacy Gemini aliases (such as `gflow-gemini-3.1-flash-image-square`) and numeric aspect names (such as `-16-9`, `-1-1`).

## Manual paste also works

GFlow → Add account → "Option 2: paste manually". Paste a full cookie or cookie JSON; a session token is also accepted. If a recent Flow build no longer has the old token field, prefer the extension. Expand the help below the input to see the F12 steps.

## First install

After installing from the plugin store, just open GFlow. The official package embeds a standalone Python backend, the Python runtime and its dependencies inside the dynamic library; it needs no Python installation and never reads the developer's source directory.

After signing in to the CPA management console in the same browser and choosing "remember me", the GFlow static page reads the management credential from the same-origin `cli-proxy-auth`, matching the console's current obfuscated storage format. The credential is only sent to `/v0/management/plugins/gflow/` on the current host; the management key is no longer returned through a public resource endpoint. Desktop embedded windows that do not share browser storage can expand "connect to the management console manually"; the temporary key stays in page memory only and is cleared when the page closes.

The default data directory is `gflow-data` next to the installed plugin library, holding the database, media cache, backend logs and the runtime released per version. Advanced setups can point `data_dir` at an existing data directory; back it up before upgrading. The old `management_key` and `python_path` settings are no longer used. GFlow never reads the developer's old directories or another installation's account data.

When the Windows host uses a temporary copy of the library, the default data directory is `plugins/gflow/gflow-data` next to the core executable, so accounts are not written into the temporary process directory. Since v0.1.11, reading management state and running generations use separate request channels; the console's automatic refresh only reads status and accounts and never overwrites unsaved settings.

Chrome/Edge and other browsers are not bundled with the plugin. Choosing the local-browser captcha method still requires a working browser; a supported captcha service can be configured instead. The plugin backend talks to the host over stdin/stdout and adds no external business port.

The public resource endpoints expose only the management page and the static login helper ZIP. Manual download of cached media goes through the authenticated management endpoint `/v0/management/plugins/gflow/api/media?file=<filename>`; local images and videos in generation responses are returned as data URIs, so clients do not need the management key. Inline media is capped at 128 MiB; above that GFlow clearly points at the authenticated endpoint instead of resubmitting the generation.

"Copy extension configuration" contains the management credential; paste it only into the local GFlow login helper. The extension stores it in `chrome.storage.local` and does not sync it through Chrome; clearing the configuration removes the stored data. Never publish cookies or the extension connection configuration.

## Free and paid scope

GFlow and the browser extension are free. Google Flow subscriptions and generation credits, paid captcha platforms and proxy costs are separate. Local-browser captcha has no platform fee but needs a working browser environment.

Adding or syncing an account validates the session, queries the quota and may create a project; it never generates images or videos automatically. Sync never enables a disabled account.

## Verification scope

Extension cookie scope, sync authentication, account create/update, duplicate task merging and page connectivity have been verified with synthetic data. The extension was installed locally and its file versions were checked. Verified on this machine on 16 October 2026: login helper 1.3.4 completed a real sync, the account was saved and enabled, and the backend queried the quota independently using the saved session. Real image/video generation has not been verified.

## Sources

The model adapter code embedded in GFlow is based on [TheSmallHanCat/flow2api](https://github.com/TheSmallHanCat/flow2api), keeping its MIT notice. The GFlow login helper is an independent implementation and does not copy third-party browser extension source that carries no license declaration.


### Generation lifecycle repair

Generation requests are attempted once and have bounded CAPTCHA/overall timeouts. Windows clients can stop their own request through a request-specific named pipe; queued cancellation cannot close the active browser. The dedicated browser runs headless and is reclaimed after the task. Python requires psutil for process ownership checks.

Cookie injection preserves explicit site scopes and handles legacy three-site mirrors without copying Flow-only OSID cookies to Google Accounts. Account sync and quota checks do not verify CAPTCHA readiness. Local preflight now obtains an IMAGE_GENERATION reCAPTCHA token after preserving cookie scopes, enabling native Trusted Types and executing the score-based key directly. A token check alone does not prove successful media generation.
