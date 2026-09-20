# CTRL Loop

A fresh Windows visual assistant project. The previous local implementation has been moved into a recovery backup; Git history and the configured remote are preserved.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the agreed design, component responsibilities, remaining choices, and implementation stages.

The intended loop is:

1. CUA Driver captures the selected window.
2. Local UI-trained YOLO and a separate text reader identify candidate controls.
3. Application code builds English action descriptions inside structured data.
4. Online JEV chooses among those actions.
5. Application code validates the target, sends the action through CUA Driver, and observes the result.

Application code is not implemented yet. There are no install or run commands at this stage. The first milestone is read-only window capture with verified coordinates.
