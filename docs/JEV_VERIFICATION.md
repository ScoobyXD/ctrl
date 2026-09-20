# JEV decision preview verification

Verified **19 September 2026, 18:22 PDT** (`2026-09-20T01:22:06.601335Z`) on base **`3cbbeeef8e671049850837256326a6a9700fa8bb`**. Result: **PASS for decision-only integration**: isolated dependency setup, 50 offline checks, runtime AWS retrieval, and one live JEV Choice/Noul request. No input actions were executed.

Worktree: `C:/Users/Jonat/.codex/worktrees/875a/[CTRL] Loop`; branch `codex/jev-decision-preview`. See [setup and reproducible commands](JEV.md).

## Completed checks

- Fresh installation and cached installer repeat passed using unpackaged CPython **3.12.13 x64** and uv **0.11.26**. All seven distributions are pinned with hashes; boto3 and botocore are **1.43.98**. Dependency consistency checks passed. No global install or machine configuration change was made.
- **50 offline tests passed:** 12 credential checks, 16 provider-adapter checks, 17 local registry checks, and 5 CLI checks. They cover malformed/duplicate provider JSON, unknown/missing IDs, nonfinite and boolean probabilities, incomplete distributions, nonmaximum selections, wrong model/provider, stale/copied/replayed requests, copy isolation, and sanitized SDK/provider errors.
- A regression reproduces SDK DEBUG/ERROR/CRITICAL logging with a synthetic credential and verifies no output escapes during lookup, including on failure; prior logging state is restored.
- Mocked CLI integration exercises the real serializer, HTTP adapter, Choice/Noul validator, registry, and output filtering with a fake provider transport. The default CLI is checked to make no credential or network calls.
- The actual offline CLI successfully printed the complete outgoing request and wrote ignored `.ctrl/jev/offline-preview.json`.
- Independent review identified the SDK logging issue and the registry's initial trust of mutable adapter results; both were fixed and regression-tested. `git diff --check` passes.

## Live verification

The sandbox live attempt could not retrieve the credential and returned a sanitized lookup error. An authorized normal-host retry reached the existing AWS authentication flow and reported **expired/unavailable SSO sign-in**. Neither attempt reached JEV. No API key, provider error body, or SDK exception payload was printed or written.

The existing renewal command, `aws sso login --profile mangorts-dev`, completed through the normal browser flow. The subsequent normal-host run succeeded:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe ./scripts/jev-preview.py --live --output ./.ctrl/jev/live.json
```

JEV selected capture-scoped `d3:click`, the manually labeled **Save settings** button. The returned probability was **0.62** and the separate distribution-based Choice confidence was **0.51**. These show meaningful uncertainty, not a high-certainty action approval. `already_satisfied` returned **0.03**; `no_viable_target` returned **0.39**. Both are independent yes-probabilities, not completion or grounding evidence.

| Route field | Actual live result |
|---|---|
| Endpoint | `https://openrouter.ai/api/alpha/decisions` |
| Requested model | `typesafe/jev-1.13` |
| Resolved model | `typesafe/jev-1.13-20260917` |
| Provider | `TypeSafe` |
| Routing | `only` and `order`: `typesafe`; `allow_fallbacks:false` |
| Provider request + parse/validation duration | **396.902 ms** (excludes AWS secret lookup and setup) |
| Provider-reported usage | **1,124 input tokens / 300 output tokens** |
| Provider-reported cost | **0.000047208 USD** |
| Provider request ID | `gen-dec-1789867326-Hve9EaCVuVZNOMeQbmOC` |
| Input actions | **0** |

The full validated Choice distribution is preserved:

| Capture-scoped option suffix | Probability |
|---|---:|
| `d3:click` — Save settings | 0.62 |
| `d6:click` — Reset defaults | 0.00 |
| `d8:click` — Cancel | 0.00 |
| `reobserve` | 0.03 |
| `no_target` | 0.35 |

The distribution sums to one and the selected ID is its maximum. Local request `4a23149f-3edb-4dda-ba64-a9cd75fe39e7` resolves only to the outstanding archived capture. Sanitized response evidence is ignored at `.ctrl/jev/live.json`. No raw HTTP body or authorization header is retained.

The dated model is now verified as the returned canonical ID. Direct requests using that dated ID have not been tested; the documented request alias plus exact response-model check is the implemented pin. A different resolved model/provider is rejected; no fallback service/model exists. One request's duration is not a performance benchmark, and this test does not calibrate confidence or action thresholds.

## Fixture grounding and limits

The three checked-in box records and detection confidences match stage 2's archived capture `b85fcb8f-80c0-422b-915d-f4af55cf1310`. Manual visual inspection of the original `boxes.png` and fixture source identifies `d3` as Save settings, `d6` as Reset defaults, and `d8` as Cancel. English labels explicitly say `manual_fixture_annotation`; they are not OCR or model-generated labels. No generated PNG or tensor was committed.

This stage supplies a text-only decision preview and local image-pixel registry. It has no mouse/keyboard code, input action, live target recheck, calibrated action thresholds, or completion proof. OCR/text association remains absent; screenshot-to-window action transforms and GPU/NPU execution remain unverified. The archived PID/HWND do not identify a currently valid input target. This is a working provider integration over supplied English descriptions, not a full real-screen assistant.
