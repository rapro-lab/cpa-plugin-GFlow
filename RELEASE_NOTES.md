# GFlow v0.1.21

Security hardening, full English localization and a smaller, cheaper CI. The model catalog, auto-dispatch, project binding and the host protocol are unchanged.

## Security

- The repository ships no secrets. On first boot an empty `api_key` or `admin_password` is replaced with a random value, written once to `<data_dir>/admin-credentials.txt` next to `flow.db`.
- Admin passwords are stored as bcrypt hashes, credential comparisons are constant time, and authentication fails closed when the stored value is empty or unknown.
- Management sessions expire after 12 hours, login is throttled to 5 attempts per 15 minutes, and the legacy plaintext `login_password` column is dropped.
- Outbound TLS is verified by default. `flow.tls_verify = false` (or `GFLOW_TLS_VERIFY=0`) exists only behind a TLS-inspecting proxy; `flow.tls_ca_bundle` points at its root certificate. Every previous `verify=False` call site is gone.
- The dashboard escapes model data before rendering and is served with a Content-Security-Policy header.

## Localization

- Every user-facing string in the worker, the backend, the management dashboard and the browser extension is now English.
- Cross-module retry, authentication and captcha classification no longer depends on Chinese substrings: it uses stable English markers plus the reason codes `missing_identity` and `account_mismatch`.
- Comments and docstrings remain in Chinese.

## CI and build

- A `test` job now runs on every push and pull request (worker suite, pinned upstream contract, `cargo test`); workers are only frozen on release tags or manual runs.
- Linux and macOS targets are no longer built. Releases cover windows amd64 only; upstream keeps publishing darwin and Linux builds.
- `requirements-dev.txt` records the build and test dependencies.
- The frozen worker no longer carries the browser dashboard router. An optional `build.py --lean` (`GFLOW_LEAN=1`) also drops playwright, taking the Windows worker from 64.2 MB to 24.8 MB; that variant cannot use the `browser` captcha method.

## Verification

- 180 worker tests (9 skipped), 29 pinned upstream contract tests, 21 Rust tests, 9 bootstrap tests and the clean-install smoke pass; the CI test job is green on windows-2022.
- The installed plugin serves the management page with the CSP header and no Chinese text, and the management API rejects requests without the key.
- Real Google generation was not re-run for this release.

## Windows install

Download gflow_0.1.21_windows_amd64.zip and extract gflow.dll. Quit the EasyCLIProxyAPI proxy, replace the plugin keeping gflow-data, then open the proxy again.

---

## Previous release: v0.1.20

Fixes media return and model capability validation, and adds credential-free browser stage diagnostics.

- Inline media is read with a 128 MiB decode cap, including growth during the file read; reduces parsing overhead for large media.
- Video reference-image limits are published per actual model and duration; Quality 8-second requests that do not support reference images are rejected explicitly instead of silently dropping the image.
- Browser launch, page load and session operation failures keep safe stage and reason classification; cookies, tokens, captcha parameters and full sensitive exceptions are not logged, and the verification flow and retry policy are unchanged.
- Keeps the 0.1.19 host SSE format, asynchronous return, fixed account and project binding.

Real-machine boundary: CutFlow 0.6.5 with GFlow 0.1.19 passed real project creation and old-image download-only recovery; one new image request returned "Failed to obtain reCAPTCHA token" after 109.1 seconds and produced no work. A local blank-page launch check in ordinary Chrome passed, which does not prove the Google verification page works. New images and consecutive edits still need real acceptance; this release does not claim to have fixed upstream verification.

On Windows download gflow_0.1.20_windows_amd64.zip and extract gflow.dll. Quit the EasyCLIProxyAPI proxy, replace the plugin keeping gflow-data, then open the proxy again. CutFlow 0.6.6 is the recommended companion. Do not delete accounts, projects or previous work to retry.
