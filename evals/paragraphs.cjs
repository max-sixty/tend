/**
 * Grade literal Markdown paragraph boundaries for PR-description wrapping.
 * Lists, headings, tables, code and raw HTML retain their structural newlines;
 * narrative paragraphs, including those inside details and blockquotes, must
 * occupy one source line. At least one narrative paragraph is required.
 */
const MarkdownIt = require("markdown-it");

const markdown = new MarkdownIt({ html: true }).enable("table");

module.exports = function paragraphs(output) {
  if (typeof output !== "string") {
    return { pass: false, score: 0, reason: "Expected a Markdown string." };
  }

  let listDepth = 0;
  const prose = [];
  for (const token of markdown.parse(output, {})) {
    if (token.type === "bullet_list_open" || token.type === "ordered_list_open") {
      listDepth += 1;
    } else if (
      token.type === "bullet_list_close" ||
      token.type === "ordered_list_close"
    ) {
      listDepth -= 1;
    } else if (token.type === "paragraph_open" && listDepth === 0) {
      prose.push(token.map);
    }
  }

  if (prose.length === 0) {
    return { pass: false, score: 0, reason: "No narrative paragraph found." };
  }

  const wrapped = prose.filter(([start, end]) => end - start !== 1);
  return {
    pass: wrapped.length === 0,
    score: wrapped.length === 0 ? 1 : 0,
    reason: wrapped.length
      ? `Narrative paragraphs span source lines ${wrapped.map(([start, end]) => `${start + 1}-${end}`).join(", ")}.`
      : "Every narrative paragraph occupies one source line.",
  };
};
