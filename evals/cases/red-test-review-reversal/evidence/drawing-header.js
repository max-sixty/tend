/* Drawing gesture controller.
 *
 * Draw mode claims primary-pointer drags anywhere on the page until the user leaves it.
 * Every drawing belongs to the comment draft of one element: the semantic target under
 * a stroke's first point, or else the one on screen nearest it, so a stroke in empty
 * space opens its box beside the content it was drawn by, as any other does. (Strokes
 * with no element made page comments once; their box had to open in Threads, which
 * covered the ink just drawn, and a page-level box anywhere else would have been the
 * one comment box with no element to stand by.) A stroke joins the drawing its draft
 * already holds, in that drawing's frame, so neither putting the box away nor leaving
 * Draw mode loses ink; once the draft is sent or its drawing removed, the next stroke
 * starts another. Within one Draw mode session, each stroke after the first goes to the
 * first stroke's draft wherever it starts, while that draft holds a drawing.
 *
 * Ink the user has drawn and not sent stands on the page until they send it or take it
 * back, whether or not its composer is up and whether or not Draw mode is: a drawing that
 * vanished with its box and came back whole with the next stroke read as one deleted and
 * then one that could not be. The open composer's drawing is drawn as pending, the others
 * as parked. Where a draft's element stands is read once (`targetOf`) for its ink, its
 * strokes and its undo; a draft whose element a revision took away is drawn parked in
 * the section that held it, where the element stood in it (the record's `at`), still
 * undoable and removable from its box, rather than vanishing again. `z` or ⌘Z in Draw
 * mode takes back the last stroke drawn, and a composer's own control its own drawing's;
 * in the composer, ⌘Z and ⌘⇧Z walk the draft's strokes and words as one history
 * (`input.js`). Taking back the last stroke removes the drawing, as the composer's
 * removal does at once.
 *
 * The controller owns pointer capture, stroke sampling, mode state and stroke undo. SVG
 * replay, anchor placement, composers, reactions, and page geometry enter through
 * explicit capabilities, and the drafts are the one record of the strokes already drawn.
 * As each stroke lifts or is taken back, the controller also reads the page's words the
 * drawing stands over into the record, for whoever reads the comment without the page.
 */
