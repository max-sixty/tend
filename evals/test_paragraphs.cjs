const assert = require("node:assert/strict");
const test = require("node:test");
const paragraphs = require("./paragraphs.cjs");

test("grades source boundaries rather than paragraph width", () => {
  const long = "A long paragraph stays on one source line. ".repeat(30);
  assert.equal(paragraphs(long).pass, true);
  const wrapped = paragraphs("A paragraph continues\non the next source line.");
  assert.equal(wrapped.pass, false);
  assert.equal(wrapped.score, 0);
  assert.match(wrapped.reason, /1-2/);
  assert.equal(paragraphs("A paragraph.\r\n\r\nAnother paragraph.\r\n").pass, true);
  assert.equal(paragraphs("A paragraph\r\ncontinues.\r\n").pass, false);
});

test("exempts structural newlines in nested lists, code, tables and headings", () => {
  const body = [
    "The change fixes the observed failure.",
    "",
    "- A list item",
    "  continues here.",
    "  1. A nested item",
    "     continues too.",
    "",
    "    Another paragraph inside the nested item.",
    "",
    "```bash",
    "printf 'one'",
    "printf 'two'",
    "```",
    "",
    "    Indented code",
    "    keeps its lines.",
    "",
    "| Check | Result |",
    "| --- | --- |",
    "| Unit tests | Pass |",
    "",
    "Setext heading",
    "==============",
    "",
    "## ATX heading",
  ].join("\n");
  assert.equal(paragraphs(body).pass, true);
  assert.equal(
    paragraphs(body + "\n\nProse after the list\nis still checked.").pass,
    false,
  );
});

test("checks prose inside details and blockquotes without grading raw HTML", () => {
  const details =
    "<details><summary>Evidence</summary>\n\nThe recorded checks pass.\n\n</details>";
  assert.equal(paragraphs(details).pass, true);
  assert.equal(
    paragraphs(details.replace("checks pass", "checks\npass")).pass,
    false,
  );
  assert.equal(paragraphs("> A quoted paragraph.").pass, true);
  assert.equal(paragraphs("> A quoted paragraph\n> continues here.").pass, false);
  assert.equal(
    paragraphs("Narrative prose.\n\n<div>\nRaw HTML retains\nits lines.\n</div>").pass,
    true,
  );
});

test("requires narrative prose rather than accepting empty or structural-only output", () => {
  for (const body of [
    "",
    " \n",
    "- Only a list item\n  continued here.",
    "```text\nOnly code\n```",
    "| Only | Table |\n| --- | --- |\n| One | Two |",
    "Setext heading\n===",
    "<div>\nOnly HTML\n</div>",
  ]) {
    assert.deepEqual(paragraphs(body), {
      pass: false,
      score: 0,
      reason: "No narrative paragraph found.",
    });
  }
  assert.equal(paragraphs(null).pass, false);
});
