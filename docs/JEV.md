# JEV decision preview through OpenRouter

This stage prepares English task and candidate descriptions, asks JEV to choose among local action IDs, and validates the returned Choice distribution and Noul probabilities. It is a decision-only preview: it does not perform mouse or keyboard input or run an autonomous loop.

## Local setup

Use Windows x64, PowerShell 7.2+, an unpackaged **CPython 3.12.13 x64** base interpreter, and **uv 0.11.26**, matching the local perception setup. Run from the repository root:

```powershell
pwsh -NoProfile -File ./scripts/install-jev.ps1 -Python '<absolute path to Python 3.12.13 base/python.exe>'
```

The installer creates `.runtime/jev-venv`, downloads only the locked packages from PyPI, verifies their hashes, checks dependency consistency, and imports `boto3` to report its version. It does not initialize an AWS session, retrieve a secret, or call OpenRouter. No global packages or machine configuration are changed.

`dependencies/jev-requirements.in` pins `boto3==1.43.98`; `dependencies/jev-requirements.lock` pins all seven distributions and their artifact hashes:

| Distribution | Version |
|---|---|
| boto3 | 1.43.98 |
| botocore | 1.43.98 |
| jmespath | 1.1.0 |
| python-dateutil | 2.9.0.post0 |
| s3transfer | 0.19.2 |
| six | 1.17.0 |
| urllib3 | 2.8.0 |

The AWS SDK is used for credential retrieval. The JEV HTTP adapter uses Python's standard-library `urllib`, and focused offline tests use standard-library `unittest`; neither an OpenAI SDK nor pytest is needed. See the [AWS-maintained boto3 release](https://pypi.org/project/boto3/1.43.98/).

## Preview and live smoke test

The default command builds the outgoing decision request offline. It needs no AWS credentials:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe ./scripts/jev-preview.py --fixture ./examples/jev-fixture.json
```

For the authorized small live fixture test, add `--live`. This retrieves the stored OpenRouter key through the existing AWS authentication flow, then sends the task and English candidate/action descriptions to OpenRouter:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe ./scripts/jev-preview.py --fixture ./examples/jev-fixture.json --live
```

Either mode can write JSON evidence with `--output`, for example `--output ./.ctrl/jev/preview.json` for offline output or `--output ./.ctrl/jev/live.json` for live output. `.ctrl/` and `.runtime/` are ignored by Git. Preview/evidence can contain the supplied task and candidate descriptions; choose their content and retention accordingly. Images, detector tensors, local coordinates, and authentication material are not part of the provider's decision input.

The secret lookup is separate from the decision adapter. The key is retrieved at runtime into process memory and must never be printed, written to a dotenv file, included in evidence, or committed. Authentication failures use sanitized messages. Reuse the existing AWS login/profile configuration; do not paste a key into a command or create a replacement secret.

The existing defaults are profile `mangorts-dev`, region `us-west-1`, and secret `mangorts/dev/openrouterjev` (the historical full ARN is in `credentials.py`). Nonsecret overrides are `CTRL_AWS_PROFILE`, `CTRL_AWS_REGION`, and `CTRL_OPENROUTER_SECRET_ID`. The secret may be a raw OpenRouter key or a JSON object with one recognized key field such as `OPENROUTER_API_KEY`; ambiguous or duplicate fields fail closed. No environment variable containing the API key is used. An expired session is renewed through the existing browser login:

```powershell
aws sso login --profile mangorts-dev
```

Setup and offline preview do not contact AWS. Live mode prints the outgoing request, makes a runtime secret lookup, and makes one OpenRouter request without retries or redirects. Only validated, whitelisted response fields are displayed. Authentication error bodies and SDK exception text are never printed. During secret lookup, process logging through CRITICAL is temporarily suppressed because botocore DEBUG logging includes secret response bodies; a lock serializes lookups and the prior threshold is restored on success or failure. This also suppresses unrelated thread logs during that scope, an intentional limitation of this small CLI. The adapter has a 20-second network-operation timeout and a 1 MiB response limit. Python does not guarantee secure zeroing of immutable strings; the process uses no credential cache and terminates after the CLI completes.

## Provider route and decision semantics

The adapter uses OpenRouter's [Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request). The verified request alias is `typesafe/jev-1.13`; the live test returned canonical model `typesafe/jev-1.13-20260917` from **TypeSafe** with fallbacks disabled. Support for requesting the dated identifier directly is not yet verified. The [measured verification record](JEV_VERIFICATION.md) distinguishes requested model, resolved model, provider, and request duration; an alias alone is not evidence of an immutable model version.

The request includes `provider: {"only":["typesafe"],"order":["typesafe"],"allow_fallbacks":false}`. The adapter rejects a response unless its model exactly equals `typesafe/jev-1.13-20260917` and its provider exactly equals `TypeSafe`. A changed alias resolution therefore fails closed instead of silently accepting a new model. Updating this pin requires deliberate verification. Provider fields are supported by the [official Decisions request schema](https://raw.githubusercontent.com/OpenRouterTeam/typescript-sdk/main/src/models/decisionsrequest.ts) and [provider preferences schema](https://raw.githubusercontent.com/OpenRouterTeam/typescript-sdk/main/src/models/providerpreferences.ts).

- **Choice** selects among the supplied action IDs. Preserve the complete distribution and the model's reported confidence. These are different quantities from detector confidence or OCR confidence.
- Separate **Noul** statements assess whether the requested result is already satisfied and whether no viable supported target is visible. Each returns a yes-probability from 0 to 1. Questions evaluated together do not observe each other's answers, so local code must handle contradictions and insufficient evidence.
- IDs belong to one capture and resolve through a local target registry. A later capture with the same visible label does not validate an old ID. Unknown, duplicate, malformed, nonfinite, out-of-range, or stale response data must not become an input action.

See [Choice](https://docs.typesafe.ai/primitives/choice), [Noul](https://docs.typesafe.ai/primitives/noul), and the application's [architecture](../ARCHITECTURE.md) for the contract boundaries. This stage validates decisions only; probabilities are not proof of grounding, action safety, or task completion.

Choice validation requires all and only the offered IDs, finite nonboolean probabilities in `[0,1]`, a sum within `1e-6` of one, a selected maximum (ties allowed for inspection), and a separate finite confidence. Noul requires its typed yes-probability. Missing/extra question IDs, duplicate JSON keys, invalid usage numbers, wrong model/provider, and malformed provider JSON fail closed. There are no calibrated action thresholds: both Noul answers and the full Choice distribution remain visible for review, and every result is explicitly `preview_only`. Contradictory judgments never trigger input.

The local registry permits at most 64 explicitly supplied candidates plus capture-scoped `reobserve` and `no_target` options. This conservative local limit is not a provider-capacity claim. Empty candidates are allowed for no-target inspection. It copies input data and request payloads and consumes each response once. At dispatch, the adapter attaches a local request ID, capture ID, and SHA-256 fingerprint of the exact snapshotted payload to its normalized result. These fields are never sent to JEV and cannot be supplied by its response. Before acceptance, the registry checks both the outstanding request object and that result binding. An old result paired with a new handle is rejected, including when captures/options are identical but tasks differ. Invalidated/replaced requests also fail closed.

This binding protects local response correlation. It is not cryptographic proof of model origin, protection against a caller deliberately rewriting the binding, or evidence of screen freshness. The fingerprint detects a changed local payload; it is not a signature. The archived-fixture registry performs no live window-identity recheck, and no input adapter is imported.

## Fixture evidence and remaining limits

The benign fixture uses explicitly identified **manual or synthetic English labels**. They are test data with recorded provenance, not OCR output. The merged local detector produces UI-trained YOLO11m boxes and generic `icon` classes; that class does not identify a readable label or prove an operation is supported.

`examples/jev-fixture.json` copies three archived box records (`d3`, `d6`, `d8`) from the stage-2 verified capture `b85fcb8f-80c0-422b-915d-f4af55cf1310`. Their Save settings, Reset defaults, and Cancel labels were manually annotated after visual inspection of `boxes.png` and the fixture source. The test task and state are supplied fixture text. This is a curated subset, not a complete detector inventory. No generated image/tensor is committed. The local observation records the original PNG hash, acquisition time, PID/HWND and image dimensions; the online request excludes those geometry/identity fields and detector confidence.

OCR is absent, so a general screenshot-to-English candidate pipeline remains unimplemented. Small controls may be missed, GPU/NPU execution is unverified, and screenshot-to-window action transforms are unverified. This preview does not add OCR, input execution, target rechecks before input, or a full real-screen agent. Preserve the capture ID, original-PNG box evidence, model identity, confidence, and manual-label provenance when preparing fixture candidates from local perception results.

## Offline verification

These tests need no AWS or OpenRouter access:

```powershell
& ./.runtime/jev-venv/Scripts/python.exe -m unittest discover -s tests -p test_credentials.py -v
& ./.runtime/jev-venv/Scripts/python.exe -m unittest discover -s tests -p test_jev.py -v
& ./.runtime/jev-venv/Scripts/python.exe -m unittest discover -s tests -p test_decisions.py -v
& ./.runtime/jev-venv/Scripts/python.exe -m unittest discover -s tests -p test_preview.py -v
```

The separate detector tests still require the perception environment; no NumPy/OpenCV/Windows ML packages are added to the JEV environment. Measured live and offline results are recorded in [JEV verification](JEV_VERIFICATION.md).
