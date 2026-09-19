# Explicit navigation and completion

Requested `OPEN_URL` and `SWITCH_TAB` actions activate only the selected task tab before publishing the destination observation. Existing tabs remain open. Passive connection, attachment, and observation do not activate tabs.

Navigation dispatch is returned to Agent and recorded before visibility/rendering activation is checked. If activation fails or the task pauses, the already-dispatched navigation is retained and cannot recreate the tab or repeat the switch. A later resumed observation may finish activation of that same target. The readiness error contains only bounded visibility, focus, frame, window-state, and probe-availability diagnostics; no page content or tab address is included.

The neutral bootstrap page cannot establish task completion: `DONE` is not offered there, and Agent independently rejects it. On an observed task page, `DONE` remains the model's judgment that the original goal is satisfied. Freshness confirms that this judgment refers to the unchanged observed page; it does not independently verify arbitrary task requirements. End-to-end claims require separate outcome evidence, such as the catalog lab's submission and item-view records.
