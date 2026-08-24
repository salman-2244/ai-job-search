# Template: minimal-onepage

- **Type:** Cover letter
- **Source extension:** .tex
- **Engine/toolchain:** lualatex (display label only)
- **Page limit:** 1 page
- **Fonts:** Lato (via the `lato` LaTeX package — system/distribution font, must be installed with your TeX distribution; no bundled font files, no font-path juggling). Deliberately the same family as the `onepage-ats` resume so the two documents pair cleanly.
- **Class/packages:** `article` + `lato`, `xcolor`, `parskip`, `hyperref` (all standard TeX-distribution packages). No custom class — this is self-contained and does **not** depend on `cover.cls`.

## Compile command

    cd <output dir> && lualatex -interaction=nonstopmode Salman-Cover-Letter.tex

The source is named `Salman-Cover-Letter.tex` so the PDF LaTeX emits is `Salman-Cover-Letter.pdf` — the name a recruiter sees on the attachment. Do not name it `cover.tex` or `main.tex`. It never collides with the resume's `Salman-Resume.pdf`, even when both land in one directory.

xelatex also works (fontspec-free here); lualatex is the declared/verified engine and matches the resume.

## Style rules

- **Minimal business-letter layout, exactly one page.** No decorative name banner, no coloured header, no bullet lists. A plain top-left sender/date block, a salutation, block-paragraph body, closing, and a signed name.
- **Block paragraphs** via `parskip`: no first-line indent, vertical space between paragraphs. Separate paragraphs with a blank line in the source.
- **Sender block** (top-left): location, email (as a real mailto link showing the literal address), then the date on its own line. Keep it to these three lines.
- **Recipient block** is optional and commented out in the skeleton — uncomment and fill it only when the posting names a hiring manager and address; otherwise omit it and use the generic salutation.
- **Salutation:** `Dear [Name],` when the posting names a person, else `Dear Hiring Manager,`.
- **Body:** 3–5 short paragraphs, roughly 250–320 words total. Open with the single strongest match to the role and name the company and role explicitly. Address the posting's stated requirements; acknowledge any genuine gap honestly and bridge it rather than hiding it.
- **Accent colour** `#1F3B57` is used only on the signed name, to tie back to the resume. Everything else is black.
- Any mention of agentic coding or AI tooling must name **Claude Code**.
- **No em-dashes** anywhere (house style). Use commas or restructure.

## Known pitfalls

- **Do not let it spill to 2 pages.** If it overflows, trim the body (first cut: a sentence that restates what another paragraph already said; last resort: a whole paragraph). Never shrink the geometry or line spacing to force a fit.
- `parskip` removes paragraph indentation globally and adds `\parskip` between paragraphs — do not also add manual `\\` between body paragraphs, or the spacing doubles. Use a blank line to break paragraphs.
- The signature uses `\\[10pt]` to leave room for a name under "Sincerely,"; if you add a scanned signature image, place it in that gap rather than adding more vertical space.
- Verified via a fully-populated dummy-data instance compiling to exactly 1 page.
