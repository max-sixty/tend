const assert = require("node:assert/strict");
const test = require("node:test");
const capture = require("./capture.cjs");

const write = (content, extra = {}) => ({
  name: "Write",
  input: { file_path: "/tmp/replay/captured.md", content },
  output: "Written",
  is_error: false,
  ...extra,
});

test("extracts literal final draft rather than the agent's completion summary", () => {
  const calls = [
    write("First draft"),
    { name: "Skill", input: {} },
    write("Literal\nnewlines\n"),
  ];
  assert.equal(
    capture("Saved it.", { metadata: { toolCalls: calls } }),
    "Literal\nnewlines\n",
  );
});

test("missing, incomplete or failed capture is an execution error", () => {
  for (const calls of [
    [],
    [write("Draft", { output: undefined })],
    [write("Draft"), write("Bad final draft", { is_error: true })],
    [write("Elsewhere", { input: { file_path: "/tmp/other.md", content: "Elsewhere" } })],
  ]) {
    assert.throws(
      () => capture("Done", { metadata: { toolCalls: calls } }),
      /No completed captured.md write/,
    );
  }
});
