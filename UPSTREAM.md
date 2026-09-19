# Upstream foundation

[CTRL] adapts the Python agent, model action space, browser executor, and page snapshot from:

- https://github.com/browser-use/jev-ultrafast
- Baseline commit: `1231850a0bf1a0c0341fe408ef1668dbbfdfac46`
- License: MIT, copyright 2026 Browser Use; full notice in `licenses/jev-ultrafast-MIT.txt`.

The agent remains in `jev_ultrafast/` to keep the adaptation recognizable. The executable page reader is compiled from `browser/snapshot.ts` into `jev_ultrafast/snapshot.js`. No site-specific action plans are added.

Intentional adaptations:

- Jev itself uses OpenRouter's Decisions endpoint, exclusively `typesafe/jev-1.13`.
- The upstream text-generation helper is removed. Jev chooses exact user-supplied strings, including quoted goal text. Missing text stops with `needs_input`.
- Existing Chrome tabs attach by target ID. Closing an agent detaches its session and preserves the tab. No viewport override or isolated browser is used.
- Browser Harness 0.1.13 supplies its persistent, patient CDP transport. [CTRL] bypasses the harness daemon's implicit tab selection and creation.
- A local TypeScript interface and Python API add task controls, pause handling, connection status, and action review.
- AWS Secrets Manager supplies the OpenRouter credential to Python memory; AWS hosts no application backend.

Browser Harness: https://github.com/browser-use/browser-harness

OpenRouter Decisions: https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-questions-and-answers-request

TypeSafe supplied-string pattern: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook
