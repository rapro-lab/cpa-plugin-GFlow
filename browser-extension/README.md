# GFlow Login Assistant

Syncs the Google Flow session of the current Chrome / Edge browser to the local EasyCPA.

## Install and use

1. Download the [extension ZIP](https://github.com/daodeqing/cpa-plugin-GFlow/raw/main/downloads/GFlow-browser-extension.zip) and unzip it. Open `chrome://extensions/` (or `edge://extensions/` in Edge), enable Developer mode and load the unpacked extension.
2. In EasyCPA, go to GFlow → Add account and click "Copy extension config", paste it into the assistant's quick import box, and enter the current Google email.
3. Sign in to [Google Flow](https://flow.google.com/projects) in the same browser profile and click "Save and sync now".

After the first save, the local connection config is reused; there is no need to paste or enter credentials again. The 60-minute auto-sync requires the browser to stay open. For multiple Google accounts, use separate browser profiles.

## Cost and data

- The GFlow extension and its source are free; no separate GFlow account is required.
- Google Flow requires a Google account; available free quota, regional conditions, subscriptions and credits are governed by the Google pages. This extension never buys a paid subscription automatically.
- Third-party CAPTCHA services usually require registration, top-up and an API key; the local browser method needs no CAPTCHA service purchase. A proxy may cost extra.
- Syncing verifies the login, queries the quota and may prepare projects; it never starts image or video generation.
- During a sync the extension only observes the Flow tab it opened itself, reads the Cookie actually carried by the read-only credits request, then immediately removes the listener and closes the tab; other tabs are never observed. Credentials are submitted only to the local GFlow endpoint in the config, which shares port 8317 by default.
- The pairing credential lives only in this extension's local storage and is not synced by Chrome. Once the page is closed, the full saved credential is no longer displayed. Logs hide emails and credentials.

## Updates and troubleshooting

Current version **1.3.4**. After overwriting the program files in the original extension folder, click "Reload" on the extension management page. Existing connection config keeps working.

1.3.4 adds the webRequest permission to read the Cookie actually carried by this credits request; reload the extension after updating. The browser credit check is only used for diagnosis — the account is saved only after the backend independently verifies it.

A failed sync shows a red message; "View sync log" shows the filtered error phase, RPC and status codes. "Cookie extracted" only means the read finished; sync success is shown only when Google verification passes and the account is saved.

Source: [GFlow](https://github.com/daodeqing/cpa-plugin-GFlow/tree/main/browser-extension). The endpoint handling was informed by Flow2API; GFlow is an independent adaptation and does not imply official endorsement by Google or EasyCPA. Real account syncing is still being investigated, and real image and video generation has not yet been verified.
