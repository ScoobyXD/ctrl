# JEV decision preview verification

Live route verified **19 September 2026, 18:22 PDT** (`2026-09-20T01:22:06.601335Z`) on base **`3cbbeeef8e671049850837256326a6a9700fa8bb`**. Result: **PASS for decision-only integration**: isolated dependency setup, runtime AWS retrieval, and one live JEV Choice/Noul request. The initial 50 offline checks passed; the subsequent local response-correlation fix passes **56 offline checks** with the provider payload unchanged. No input actions were executed.

Worktree: `C:/Users/Jonat/.codex/worktrees/875a/[CTRL] Loop`; branch `codex/jev-decision-preview`. See [setup and reproducible commands](JEV.md).

## Rounded-probability regression

The 20 September 2026 investigation of a UIA viewer response-validation failure made a live synthetic Choice request with **48 options** through the existing pinned OpenRouter/TypeSafe route. The returned model/provider and question/option IDs matched the request. All probabilities were finite and in `[0,1]`, and the selected option was the returned maximum (`0.18000000000000002`). The complete distribution totaled **`0.99`**, so the original sum tolerance of `1e-6` rejected it. No native input was delivered. The screenshot's rejected provider response was unavailable; this establishes a synthetic reproduction of a validator failure, not a replay or confirmed diagnosis of that exact response.

TypeSafe's [Choice contract](https://docs.typesafe.ai/primitives/choice) describes a full distribution summing to one and a highest-probability choice; its examples use two decimal places. It does not specify a rounding-error tolerance. The observed response therefore supplies the evidence for a narrow local compatibility rule: retain the original `1e-6` sum tolerance, but also accept totals within one percentage point of one only when every value is on the `0.01` grid within floating-point tolerance. The allowance does not grow with the number of options. Values and model confidence remain unchanged; there is no invented probability or normalization. A larger discrepancy, incomplete option set, nonfinite or out-of-range value, nonmaximum choice, or changed model/provider still fails validation.

The selection helper now identifies the failed check using static, allowlisted diagnostics such as `[probability_sum]`. Unknown or malformed diagnostic codes fall back to the generic message; credentials, provider bodies and exception text are excluded. Offline CLI checks cover each allowed code, secret-bearing and hostile values, and HTTP-error precedence.

After the fix, **235 affected Jev, selection, UIA viewer and CUA execution tests passed** using the UIA runtime. Regression cases accept complete 48-option distributions totaling `0.99` and `1.01` without changing their values and reject larger errors, off-grid values, missing IDs and a nonmaximum selection. A fresh live request with the same synthetic 48-option input also passed; that response totaled `1.0`, so the rounded-total acceptance is established by the deterministic regression cases rather than that rerun.

## Completed checks

- Fresh installation and cached installer repeat passed using unpackaged CPython **3.12.13 x64** and uv **0.11.26**. All seven distributions are pinned with hashes; boto3 and botocore are **1.43.98**. Dependency consistency checks passed. No global install or machine configuration change was made.
- **56 offline tests passed:** 12 credential checks, 19 provider-adapter checks, 20 local registry checks, and 5 CLI checks. They cover malformed/duplicate provider JSON, unknown/missing IDs, nonfinite and boolean probabilities, incomplete distributions, nonmaximum selections, wrong model/provider, stale/copied/replayed requests and results, local dispatch correlation, copy isolation, and sanitized SDK/provider errors.
- A regression reproduces SDK DEBUG/ERROR/CRITICAL logging with a synthetic credential and verifies no output escapes during lookup, including on failure; prior logging state is restored.
- Mocked CLI integration exercises the real serializer, HTTP adapter, Choice/Noul validator, registry, and output filtering with a fake provider transport. The default CLI is checked to make no credential or network calls.
- The actual offline CLI successfully printed the complete outgoing request and wrote ignored `.ctrl/jev/offline-preview.json`.
- Independent review identified the SDK logging issue and the registry's initial trust of mutable adapter results; both were fixed and regression-tested. `git diff --check` passes.

## Local response correlation review fix

Coordinator review found that the original registry checked the pending handle but did not bind the result itself: a result accepted under `p1` could be supplied again with a fresh `p2` handle. The adapter now stamps each normalized result at dispatch with the local request ID, capture ID, and SHA-256 of its snapshotted payload. Registry acceptance validates and compares all three fields to the outstanding request. The binding is kept outside the provider body, and provider-supplied binding fields are ignored.

New regression checks exercise actual adapter code with mocked HTTP: `p1` dispatch/accept followed by rejection of its old result under `p2`; a foreign response with the same capture/options but different task; missing or changed metadata; and successful acceptance after a fresh dispatch. The payload fingerprint also rejects the foreign-task result when its request ID is accidentally reassigned. This protects local correlation, not cryptographic authenticity, deliberate caller tampering, screen freshness, or grounding.

The provider payload serialized by the current code is byte-for-byte equal to the preserved pre-fix offline preview (SHA-256 `fde7e5d544ec712c4aa63a37cca66cfc9fea49f925d281c2ff6a7df2e9a5bd41`). No additional AWS retrieval or live request was made for this fix. The original live artifact remains unchanged (file SHA-256 `7cdf3540fd24a18671501c83eac2e7739b4d7c27cdb7c80b580dd8a1d712f31b`); it records the earlier synchronous run and does not contain the new local binding metadata. Current acceptance rejects such an unbound historical result.

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

The distribution sums to one and the selected ID is its maximum. The original synchronous CLI run associated local request `4a23149f-3edb-4dda-ba64-a9cd75fe39e7` with the archived capture. Sanitized response evidence is ignored at `.ctrl/jev/live.json`; the later correlation fix is documented above. No raw HTTP body or authorization header is retained.

The dated model is now verified as the returned canonical ID. Direct requests using that dated ID have not been tested; the documented request alias plus exact response-model check is the implemented pin. A different resolved model/provider is rejected; no fallback service/model exists. One request's duration is not a performance benchmark, and this test does not calibrate confidence or action thresholds.

## Fixture grounding and limits

The three checked-in box records and detection confidences match stage 2's archived capture `b85fcb8f-80c0-422b-915d-f4af55cf1310`. Manual visual inspection of the original `boxes.png` and fixture source identifies `d3` as Save settings, `d6` as Reset defaults, and `d8` as Cancel. English labels explicitly say `manual_fixture_annotation`; they are not OCR or model-generated labels. No generated PNG or tensor was committed.

This stage supplies a text-only decision preview and local image-pixel registry. It has no mouse/keyboard code, input action, live target recheck, calibrated action thresholds, or completion proof. OCR/text association remains absent; screenshot-to-window action transforms and GPU/NPU execution remain unverified. The archived PID/HWND do not identify a currently valid input target. This is a working provider integration over supplied English descriptions, not a full real-screen assistant.
