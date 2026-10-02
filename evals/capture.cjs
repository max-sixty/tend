/** Extract the completed draft from SDK tool metadata before grading it.
 * Only Write is exposed, so the last write is the final artifact. Promptfoo
 * removes its temporary directory before transforms; tool inputs retain the
 * literal file bytes. A missing or failed write is an execution error.
 */
module.exports = function capture(_output, context) {
  const writes = context.metadata.toolCalls.filter(
    (call) => call.name === "Write" && /(?:^|\/)captured\.md$/.test(call.input.file_path),
  );
  const draft = writes.at(-1);
  if (!draft || draft.is_error || draft.output === undefined) {
    throw new Error("No completed captured.md write");
  }
  return draft.input.content;
};
