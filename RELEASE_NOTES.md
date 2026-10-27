# GFlow v0.1.20

Fixes media return and model capability validation, and adds credential-free browser stage diagnostics.

- Inline media is read with a 128 MiB decode cap, including growth during the file read; reduces parsing overhead for large media.
- Video reference-image limits are published per actual model and duration; Quality 8-second requests that do not support reference images are rejected explicitly instead of silently dropping the image.
- Browser launch, page load and session operation failures keep safe stage and reason classification; cookies, tokens, captcha parameters and full sensitive exceptions are not logged, and the verification flow and retry policy are unchanged.
- Keeps the 0.1.19 host SSE format, asynchronous return, fixed account and project binding.

Real-machine boundary: CutFlow 0.6.5 with GFlow 0.1.19 passed real project creation and old-image download-only recovery; one new image request returned "Failed to obtain reCAPTCHA token" after 109.1 seconds and produced no work. A local blank-page launch check in ordinary Chrome passed, which does not prove the Google verification page works. New images and consecutive edits still need real acceptance; this release does not claim to have fixed upstream verification.

On Windows download gflow_0.1.20_windows_amd64.zip and extract gflow.dll. Quit the EasyCLIProxyAPI proxy, replace the plugin keeping gflow-data, then open the proxy again. CutFlow 0.6.6 is the recommended companion. Do not delete accounts, projects or previous work to retry.
