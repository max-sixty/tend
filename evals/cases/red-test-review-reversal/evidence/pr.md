# Keep draft controls usable in Draw mode and update browser checks

PR #2064 moved draft editing actions from the annotation rail into each draft. The complete main run still checks the old rail controls; Draw mode also claims a press on the new in-widget Edit button as ink. This change lets widget-offered controls keep their pointer gestures in Draw mode and updates the affected browser journeys to use the local draft actions and current keyboard labels.

The three-width draft and Page Map journey, the rail Ask/save/undo journey, Draw mode editing, Page Map routing, and numbered Ask navigation now pass (7 browser cases). All 254 runtime tests and pre-commit pass. The affected browser checks failed before this change on current `main`.

This addresses the draft-control subset of [failed main run 37819031263](https://github.com/max-sixty/leaf/actions/runs/37819031263). The run also contains other failures that need separate repairs.
