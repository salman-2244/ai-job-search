# Template: onepage-ats

- **Type:** CV
- **Source extension:** .tex
- **Engine/toolchain:** lualatex (display label only)
- **Page limit:** 1 page
- **Fonts:** Lato (via the `lato` LaTeX package) — clean, professional sans-serif, ATS-safe with full Unicode coverage, matches the cover letter's Lato pairing for a cohesive document set. System/distribution font; no bundled font files or font-path juggling needed.
- **Class/packages:** `article` + `lato`, `fontawesome5`, `enumitem`, `xcolor`, `tabularx`, `array`, `needspace`, `ifthen`, `hyperref` (all standard TeX-distribution packages)

## Compile command

    cd <output dir> && lualatex -interaction=nonstopmode Salman-Resume.tex

The source is named `Salman-Resume.tex` so the PDF LaTeX emits is `Salman-Resume.pdf` — the name a recruiter sees on the attachment. Do not name it `main.tex`.

pdflatex can fail on modern MiKTeX with fontawesome5 font-expansion errors; lualatex compiles the same source cleanly. xelatex also works, but lualatex is the declared/verified engine.

## Style rules

- **Single column, one page.** This is an ATS-first layout: a single column guarantees the extracted text's reading order matches the visual order. Do not add a sidebar or a second column.
- **Accent colour** `#1F3B57` (dark slate blue) on the name, section titles, section rules, entry organisation, and bullet markers. Colour is rendering-only and never affects text extraction; keep body text black.
- **Section order** (drop the last ones first when trimming to fit one page): SUMMARY → PROFESSIONAL EXPERIENCE → SKILLS → PROJECTS → EDUCATION → LANGUAGES → AWARDS & CERTIFICATIONS. Reorder only to surface the most posting-relevant section higher.
- **Section headers** are `\cvsection{\faIcon}{TITLE IN CAPS}` — a fontawesome icon + bold accent title + a full-width rule. The icon is decoration; the CAPS text is the real, ATS-readable heading. Use the posting's own term in a heading where truthfully applicable (a posting hiring for "MLOps" should find a heading or bullet containing "MLOps").
- **Entries** use `\cventry{Role}{Organisation}{Date range}{Location}` — bold role plus italic accent organisation at left ("Role, Organisation"), muted "date | location" flush right on the **same line** — followed by a `cvbullets` environment. Bold the metric/keyword inside each bullet (`\textbf{~35\%}`). The single-line header is deliberate: it saves roughly two lines per entry versus a stacked title/date/org block, and that reclaimed space belongs to experience bullets. **Bold budget:** aim for ~1 `\textbf{}` call per experience/project bullet — more crowds the page and weakens emphasis. Skill-group labels are always bold (exempt).
- **Keyword population (per posting).** Extract the top 5–8 hard-skill keywords from the job description (e.g. "Supply Chain", "Process Optimization", "Data Analytics", "Six Sigma", "SAP", "Python", "Stakeholder Management", "Lean") and weave them into the **existing** Experience and Projects bullets, replacing generic phrasing with the posting's own term where it truthfully describes the same work ("managed external partners" → "managed external vendors and partners"). Never add a fake job or a fake project. If a keyword fits no existing bullet, append **at most one** concise bullet to the most relevant experience entry, and only if the profile genuinely supports it — a keyword with no real work behind it stays absent. The page limit is unchanged: still exactly one page after tailoring.
- **Spend the whole page.** A one-pager with white space at the bottom is a wasted page, not a tidy one. If content ends short, add substantive bullets to the most relevant roles (or restore a trimmed section) until the page is genuinely full; conversely, trim by relevance rather than shrinking type. Aim to fill to the bottom margin.
- **Skills** use `\skillgroup{Category}{item | item | item}` — one labelled, pipe-separated line per category. Grouping packs more ATS keywords than a single flat flow while staying scannable.
- **Languages** use `\lang{Name}{filled-dots-0-5}{Text level}` — the 5-dot rating is visual; the parenthetical text level (e.g. "Professional working proficiency") is what an ATS reads, so it is always present.
- **Dates:** write real month/year ranges (e.g. `Sep 2025 -- Present`). Use `--` (en-dash) for ranges. No em-dashes anywhere.
- **Contact line:** exactly **one** line, in this order: email, phone, location, LinkedIn handle, Portfolio. Items are separated by `\contactsep` (a 7pt space, deliberately tighter than `\quad`) so all five fit inside `\textwidth` without an overfull box. Email, phone and location are printed as literal text next to their icons — never carried by an icon or a link target alone. The Portfolio item is the one exception: its visible label is the word "Portfolio" and the URL lives in the link target, which is intended. There is no GitHub item. Keeping this to one line is what frees the second header line for experience and project content, so do not restore the two-line form; if an added item overflows, shorten an item's text rather than widening the margins.

## Known pitfalls

- **Do not let it spill to 2 pages.** This template is one page by contract. When content overflows, use relevance-weighted cutting (see `05-cv-templates.md`): score each line by relevance to the posting, uniqueness, and whether the cover letter depends on it; cut the lowest first. Older/less-relevant bullets go first. Never shrink the geometry or font to force a fit.
- `\rating{n}` takes an integer 0–5; the empty dots use colour `dotoff`. The dots are fontawesome `\faCircle` glyphs — they extract as harmless glyph-name noise, which is fine because the text level beside them carries the real information.
- `cvbullets` is a thin wrapper over `itemize`; nesting a second `cvbullets` inside a bullet works but tightens spacing — avoid deep nesting on a one-pager.
- `\cventry`'s right column is a fixed 7.2cm; a very long "date | location" string can wrap. Keep locations to "City, Country".
- Verified via a fully-populated real-data instance compiling to exactly 1 page with clean `pdftotext -layout` extraction (literal email/phone, in-order sections).
