---
name: markdown
description: Transform AI markdown generation to be 100% markdownlint-compliant.
  Use this skill whenever generating messages containing markdown, generating or
  editing markdown files (.md) for technical documentation, README files, guides,
  tutorials, or any GFM content requiring clean, professional presentation.
  Also use when the user asks to write math, equations, or LaTeX formulas
  inside markdown.
license: MIT. See LICENSE for complete terms
---

# GitHub Flavored Markdown Generation

Generate GFM that passes markdownlint validation with zero violations,
including math/LaTeX expressions via `$` delimiters.

---

## Core Rules

Every block element needs a blank line before and after it. No exceptions.

| Rule | Requirement |
| --- | --- |
| Lists (MD032, MD004) | Blank lines before/after; use `-` only |
| Headings (MD001, MD022) | Blank lines before/after; use `#`; no skipped levels |
| Code blocks (MD031, MD040) | Blank lines before/after; always specify language |
| Math blocks `$$` | Blank lines before/after; treat like code blocks |
| Inline math `$` | No spaces inside delimiters |
| Lines (MD013) | Under 80 chars |
| Encoding | Regular spaces only — never tabs or `&nbsp;` |
| File ending (MD047) | Single newline at EOF |
| Ordered lists (MD029) | Use `1.` for every item |
| URLs (MD034) | Wrap in `<>` or `[]()` |

---

## Block Patterns

### Lists

```markdown
Text before.

- Item one
- Item two

Text after.
```

### Headings

```markdown
Text.

## Heading

Content.
```

### Code Blocks

````markdown
Text.

```python
code()
```

Text.
````

**Nested fences:** use one more backtick than the deepest level inside.

### Math — Inline

Wrap with single `$`. No spaces after opening or before closing delimiter.

```markdown
The formula $E = mc^2$ is well known.

Solve for $x$ in $ax^2 + bx + c = 0$.
```

**Punctuation adjacent to `$` must be ASCII.** Full-width (CJK) punctuation
immediately before or after `$` breaks inline math rendering.

**Wrong:**

```markdown
其中 $k$ 为平滑常数，$r_i$ 为排名。
```

**Right:**

```markdown
其中 $k$ 为平滑常数, $r_i$ 为排名。
```

Common offenders: `，` `。` `：` `（` `）` `、` `；` `——`. The em dash
`——` is not standard CJK punctuation but behaves the same way: `——$x$`
or `$x$——` breaks rendering just like the punctuation marks above. When
inline math fails to render, check adjacent punctuation (including the
em dash) first.

**Alternative fix — add a space.** Instead of switching the punctuation
to ASCII, adding a single space between the full-width character and
`$` works equally well and keeps the original punctuation style:

```markdown
其中 $k$ 为平滑常数 ，$r_i$ 为排名。
```

Both fixes are valid; use whichever is less disruptive to the
surrounding text (e.g. prefer the space if converting the punctuation
would force further style decisions nearby, as in the parenthesis case
below).

**Default parenthesis style.** Default to full-width `（）` in Chinese
text; switch to ASCII `()` only when the parenthesis sits directly
against `$`. Mixing styles inconsistently (e.g. `（text)` with one
full-width and one ASCII bracket) is sloppy and should be avoided
regardless of rendering. The fix for the `$`-adjacency problem is
scoped to that one parenthesis — it is not a license to convert every
parenthesis in the document to ASCII.

**Wrong (over-correction — one `$`-adjacent paren justified converting
every other paren in the sentence too):**

```markdown
模型参数 $\alpha$ (说明) 决定平滑程度，其余符号见正文 (参数说明见下表)。
```

**Right (the `$`-adjacent paren switches to ASCII with a space; the
unrelated paren stays full-width as pure Chinese commentary):**

```markdown
模型参数 $\alpha$ (说明) 决定平滑程度，其余符号见正文（参数说明见下表）。
```

### Math — Display (Block)

Use `$$` on its own line. Blank lines before and after — same rule as code blocks.

```markdown
Text before.

$$
\int_0^\infty e^{-x^2}\, dx = \frac{\sqrt{\pi}}{2}
$$

Text after.
```

Multi-line equations use `\\` for line breaks:

```markdown
$$
\begin{align}
a &= b + c \\
  &= d + e
\end{align}
$$
```

### Math in Lists and Tables

Inline `$` works inside list items. Never use `$$` display math inside a
table cell — it breaks rendering.

For table cells, prefer plain text over `$...$` — inline math rendering
inside tables is inconsistent across renderers (GitHub, VS Code, Obsidian).
Use plain text or Unicode symbols instead.

**Escape `|` inside table cells** — `$\|\lambda\|$`, not `$|\lambda|$`, or it
breaks the table.

**Never use matrix environments inside inline `$...$`** — `\begin{bmatrix}`
only works inside `$$` blocks; use plain text like `[[2,1],[0,3]]` inline.

```markdown
- The derivative of $f(x) = x^2$ is $f'(x) = 2x$
```

**Table — prefer plain text in cells:**

```markdown
| Condition | Meaning |
| --- | --- |
| rank = min(m, n) | Full rank, no redundancy |
| rank < min(m, n) | Rank deficient, redundant rows |
```

**Table — inline math in cells (only when renderer is known to support it):**

```markdown
| Function | Derivative |
| --- | --- |
| $x^n$ | $nx^{n-1}$ |
| $e^x$ | $e^x$ |
| $\ln x$ | $1/x$ |
```

### Escaping Literal `$`

```markdown
The price is \$9.99.
```

---

## Common LaTeX Reference

**Greek:** `\alpha` `\beta` `\gamma` `\delta` `\theta` `\lambda` `\mu`
`\pi` `\sigma` `\Sigma` `\phi` `\omega` `\Omega`

**Operators:** `\times` `\div` `\pm` `\neq` `\leq` `\geq` `\approx`
`\infty` `\in` `\notin` `\subset` `\cup` `\cap` `\forall` `\exists`
`\to` `\Rightarrow` `\Leftrightarrow`

**Structures:**

| Construct | LaTeX |
| --- | --- |
| Fraction | `\frac{num}{den}` |
| Square root | `\sqrt{x}` |
| nth root | `\sqrt[n]{x}` |
| Superscript | `x^{2}` |
| Subscript | `x_{i}` |
| Sum | `\sum_{i=1}^{n}` |
| Product | `\prod_{i=1}^{n}` |
| Integral | `\int_{a}^{b}` |
| Limit | `\lim_{x \to 0}` |
| Partial derivative | `\partial` |
| Nabla | `\nabla` |
| Hat / Bar / Vector | `\hat{x}` `\bar{x}` `\vec{x}` |
| Absolute value | `\left| x \right|` |
| Norm | `\left\| x \right\|` |
| Matrix | `\begin{bmatrix} \end{bmatrix}` |
| Piecewise | `\begin{cases} \end{cases}` |

**Matrix example:**

```markdown
$$
A = \begin{bmatrix}
  a_{11} & a_{12} \\
  a_{21} & a_{22}
\end{bmatrix}
$$
```

**Every matrix row needs its own line** — `\\` must be followed by a real
newline, or GitHub renders the whole matrix as one row.

**Piecewise example:**

```markdown
$$
f(x) = \begin{cases}
  x^2 & \text{if } x \geq 0 \\
  -x   & \text{if } x < 0
\end{cases}
$$
```

---

## Critical Error Patterns

### 1. Missing Blank Lines Around Blocks

Applies to lists, headings, code blocks, and `$$` math blocks equally.

<!-- markdownlint-disable MD031 MD032 MD022 -->

**Wrong:**

```markdown
Text:
- Item
Text.

Text.
## Head
Text.

Text:
$$
y = mx + b
$$
Note that...
```

<!-- markdownlint-enable MD031 MD032 MD022 -->

**Right:**

```markdown
Text:

- Item

Text.

Text.

## Head

Text.

Text:

$$
y = mx + b
$$

Note that...
```

### 2. Inline Math — Spaces Inside Delimiters

**Wrong:** `$ E = mc^2 $` — **Right:** `$E = mc^2$`

### 3. Math Inside Table Cells

`$$` display math always breaks in table cells. Inline `$...$` is unreliable
across renderers — prefer plain text or Unicode.

**Wrong:** `| Energy | $$E = mc^2$$ |`

**Wrong (unreliable):** `| rank | $\text{rank} = \min(m, n)$ |`

**Right:** `| Energy | $E = mc^2$ |` (only if renderer is confirmed)

**Safest:** `| rank | rank = min(m, n) |` (plain text always works)

### 4. Inconsistent List Markers

<!-- markdownlint-disable MD004 -->

**Wrong:** mixing `- Item` and `* Item` — **Right:** `-` only.

<!-- markdownlint-enable MD004 -->

### 5. Skipping Heading Levels

<!-- markdownlint-disable MD001 -->

**Wrong:** `# Title` → `### Sub` — **Right:** `# Title` → `## Section` → `### Sub`

<!-- markdownlint-enable MD001 -->

### 6. Code Block Without Language

<!-- markdownlint-disable MD040 -->

**Wrong:** ` ```\ncode\n``` ` — **Right:** ` ```python\ncode\n``` `

<!-- markdownlint-enable MD040 -->

### 7. Full-Width Punctuation Adjacent to Inline Math

**Wrong:** `其中 $k$ 为平滑常数，` — the full-width `，` breaks rendering.

**Right:** `其中 $k$ 为平滑常数,` — use ASCII comma instead.

### 8. Tilde Ranges Trigger Strikethrough

Two tildes enclosing text renders as ~~strikethrough~~. Numeric ranges written
as `50~100` will accidentally strikethrough everything between the tildes.

**Wrong:** `召回 50~100 个候选，取最相关的 5~10 个`

**Right:** `召回 50 到 100 个候选，取最相关的 5 到 10 个`

Use "to", "到", or an en-dash `–` instead of `~` for ranges.

### 9. Nested Parentheses Around Math With Its Own Parens

If a `$...$` expression already contains its own closing `)` (e.g.
`\text{Uniform}(a,b)`), wrapping the whole thing in an outer comment
`(...)` stacks two `)` back-to-back against `$`. That double-paren
collision — not a single paren touching `$` — is what breaks rendering.

**Wrong:** `**例子 (连续, $X\sim\text{Uniform}(a,b)$):**`

**Right:** `**例子, 连续, $X\sim\text{Uniform}(a,b)$:**` — drop the outer
parens, use commas instead.

Plain `$...$。` (CJK period right after `$`) renders fine on its own;
the failure mode is specifically two parens colliding, not punctuation
in general.

---

## Pre-Generation Checklist

- [ ] Blank lines before/after every list, heading, code block, `$$` block
- [ ] Heading levels increment by one (1→2→3)
- [ ] All lists use `-`; ordered lists use `1.`
- [ ] All code blocks have a language tag
- [ ] Inline math: no spaces inside `$...$`; ASCII punctuation adjacent to `$`
- [ ] No `$$` inside table cells; prefer plain text over `$...$` in cells
- [ ] Numeric ranges use "to" / "到" / en-dash, never `~` (triggers strikethrough)
- [ ] Lines under 80 chars
- [ ] URLs wrapped in `<>` or `[]()`
- [ ] Document starts with H1
- [ ] File ends with single newline
- [ ] No outer `(...)` wrapped around math that already ends in `)` —
  use commas instead to avoid `)$` double-paren collisions
- [ ] Regular spaces only (no tabs, no `&nbsp;`)

---

## Mental Model

```text
[Text]
↓ blank line ↓
[Heading / List / Code / $$Math]
↓ blank line ↓
[Text]
[EOF + single newline]
```

Every block transition needs a blank line. Inline `$math$` flows with text —
no blank lines needed.

---

## Common Code Languages

`python` `javascript` `typescript` `java` `go` `rust` `cpp` `bash` `sh`
`html` `css` `json` `yaml` `sql` `markdown` `text` `diff`

---

## Validation

```bash
markdownlint filename.md
```

Goal: zero errors. Most common cause of failures: missing blank lines around
blocks, spaces inside `$...$`, or `$$` used in table cells.
