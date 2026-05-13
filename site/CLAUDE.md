# Site

Astro marketing site for tend-src.com.

## Paragraph formatting

Put each prose element's text on a single line with its tags:

```astro
<p>The content goes here.</p>
```

Not:

```astro
<p>
  The content goes here.
</p>
```

Firefox grabs the leading whitespace as part of the selection when a reader triple-clicks the paragraph, polluting copy-paste. Applies to any prose block element — `<p>`, `<li>`, `<blockquote>`. Long paragraphs stay on one long line; let the editor soft-wrap.
