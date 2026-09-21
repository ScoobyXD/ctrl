When I ask you to create a new git worktree, I always mean to create a new git worktree in a brand new codex instance. And make sure to number them, like # <worktree name>

## Jev integration rules

Follow these rules when designing, changing, or reviewing Jev calls:

1. **Batch independent questions that share state.** Put multiple questions in one request whenever they can use the same evidence and fit the request limits. Do not make one call per question by default. Questions in a request cannot read one another's answers; use another request when a decision needs earlier answers or new observations.
2. **Send only the state needed for the questions.** Keep the goal and relevant evidence, and remove unnecessary fields and repeated descriptions. Share common control and parent descriptions instead of copying them for every candidate. Preserve the context needed to distinguish controls and validate decision quality when changing the representation. A request-size cap alone does not make state minimal.
3. **Do deterministic math in code.** Do not ask Jev to calculate dates or date intervals, count items, perform arithmetic, compare numeric values, or determine numeric magnitudes. Parse, calculate, and compare those values in code; use Jev for semantic judgments that require model interpretation.
4. **Pin and verify the Jev version.** Use a verified version and validate the resolved model identity. Do not silently follow a latest or moving alias; when using a provider alias, reject responses that resolve to an unapproved version. Log the model that answered, and re-evaluate questions and thresholds before deliberately adopting a new version.
5. **Treat Noul and Choice as different judgments.** Noul estimates the likelihood of a statement; Choice compares the offered alternatives. Do not assume their outputs agree or transfer thresholds, probabilities, or confidence interpretations between them. Calibrate each primitive for its own question, including separately phrased or negated statements.

Source: [The Jev Setup Guide](https://x.com/zodchiii/status/2101243146596384854).
