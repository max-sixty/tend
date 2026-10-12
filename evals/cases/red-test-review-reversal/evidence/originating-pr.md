# #2064 Render draft Markdown with stable local editing controls

Drafts previously showed Markdown source as plain text and placed editing actions among margin annotations. They now render formatted Markdown with a local Edit/Save/Cancel/Close footer; hiding annotations leaves editing available. Save preserves exact source, Cancel discards unsent edits, and Close keeps them. Permanent buttons remain, while the current-page marker is plain text.

The shared renderer now uses Markdown-it with the same dialect as Python, replacing Marked. Declared block Markdown is delivered formatted for first paint, while projection, editing and revision comparison retain the exact source. Revision preparation loads dependencies without formatting source; copied arrivals are painted after typed semantic capture. Annotations retain the authored widget as their margin seat. Parser token spans and the shared text aligner carry rendered carets back to source, including tab-expanded list continuations and empty table cells.

Shortcut badges are excluded from native buttons through rendered ancestry, including nested slots. External hint seats stay out of flow; button labels and hit targets no longer change when focus projects shortcuts. Deferred Close focus yields to newer user gestures through the shared intent owner. The keyboard bar and accessible shortcuts remain available. Key implementation owners are `runtime/markdown.js`, `widgets/lf-draft.js`, `keyboard/command-hints.js`, and Python passage/delivery projection.

Validation: desktop and touch editing journeys, raw clicks at both button edges, a fresh user saving/canceling/keeping drafts, live revisions, frozen-thread undo and exact-source delivery tests, independent slot/caret/gesture regressions, 254 runtime tests, 83 browser-build tests, and pre-commit. The shared parser adds approximately 110 KB at presentation so live saved Markdown can render in the same turn.

> _This was written by Codex on behalf of max-sixty_
