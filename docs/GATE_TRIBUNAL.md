# Gate Tribunal — forensic analysis of six real postings

Six jobs from the 2026-08-24 production run. Five were charged with slipping the hard gates
into Telegram; one was charged and acquitted. Every verdict below was produced by *running the
gate code over the real posting text*, not by reading the regex and reasoning about it. The
source text came from `/tmp/jobsearch_rankset_2026-08-24.json`, the artifact the run itself
gated on.

The headline finding is that the five slips had **three different causes**, not one, and only
one of them is a misfiring pattern:

| # | Job | Current verdict | Cause of the slip |
|---|-----|-----------------|-------------------|
| 1 | Strategic Maintenance — BRP-Rotax | language `PASS` | **Pattern gap.** `REQUIRED_MARKERS` has no "very good". |
| 2 | Supplier Fulfillment Specialist — Baker Hughes | all `PASS` | **No gate exists.** Nothing in the module reads citizenship. |
| 3 | Digital Transformation & Automation Manager — MCS Group | all `PASS` | **No gate exists.** Nothing in the module reads sponsorship. |
| 4 | Vendor Operations Manager — Google | `UNKNOWN` | **No evidence.** Body was never retrieved. Gate logic is correct. |
| 5 | Tecnico/a Operational Excellence — Coca-Cola EP | `UNKNOWN` | **No evidence.** Body was never retrieved. Gate logic is correct. |
| — | Global Service Process & AI Enablement Manager — Leica | language `PASS` | **Correct.** Acquitted, and the behaviour must be preserved. |

## Perpetrator 1 — BRP-Rotax, "Very good German and English skills."

Real text, last line of the *Your Skills And Experiences* block:

> Very good German and English skills

`_language_verdict` walks each sentence and, for every blocked language it finds, classifies the
sentence as optional or required:

```python
if any(mk in n for mk in OPTIONAL_MARKERS):
    optional_hits.append((lang, quote))
elif any(mk in n for mk in REQUIRED_MARKERS):
    hits.append((lang, quote))
```

The `german` alias matched. Then **both** branches missed: `OPTIONAL_MARKERS` has no "very
good", and neither does `REQUIRED_MARKERS`. The sentence was therefore recorded as neither
optional nor required, `hits` stayed empty, and control fell to the tail:

```python
verdict = PASS if (optional_hits or description) else UNKNOWN
```

`description` was non-empty, so the posting was stamped `PASS` with the reason
`"no language condition stated"` — for a posting that states a language condition in plain
words. Measured: `language_verdict(...) -> PASS`.

The gap is not limited to "very good". Probing `REQUIRED_MARKERS` against the intensifiers real
postings actually use, every one of these returned `PASS`:

| Phrase | Current | Should be |
|---|---|---|
| `Very good German and English skills.` | `PASS` | `FAIL` |
| `Excellent German skills.` | `PASS` | `FAIL` |
| `Strong German skills.` | `PASS` | `FAIL` |
| `Working knowledge of German.` | `PASS` | `FAIL` |
| `German needed.` | `PASS` | `FAIL` |
| `Must be a native speaker.` | `PASS` | `FAIL` |
| `Mother tongue German.` | `PASS` | `FAIL` |
| `English and German.` | `PASS` | `FAIL` |
| `English, German.` | `PASS` | `FAIL` |

Two of those deserve separate mention because they are not marker-list gaps:

- **`Must be a native speaker.`** names no language, so the `for lang, aliases in
  BLOCKED_LANGUAGES.items()` loop never enters its body at all. No marker list can fix this;
  the rule has to be language-agnostic.
- **`English and German.`** carries no marker of any kind, required or optional. A bare
  conjunction of two languages in a requirements list is a requirement, and nothing in the
  current design treats it as one.

## Perpetrator 2 — Baker Hughes, "hold an EU passport"

Real text, in *To be successful in this role you will*:

> Be fluent in English (Italian knowledge is also preferred) and hold an EU passport

Measured: `language_verdict(...) -> PASS`, reason `"only optional language preferences
stated"`.

**The language gate was right.** Italian genuinely *is* preferred here — `"preferred"` is an
`OPTIONAL_MARKER`, so the sentence was correctly filed under `optional_hits`. Convicting this
posting on language grounds would have been wrong.

The disqualifier is `hold an EU passport`, and the reason it was missed is total: `evaluate()`
runs exactly four gates —

```python
language   = language_verdict(...)
experience = experience_verdict(...)
seniority  = seniority_verdict(title, body)
technical  = pure_technical_verdict(axes, min_body_domains)
```

— and **none of them reads work authorisation**. A grep of the module for `passport`,
`sponsor`, `citizen`, `visa`, and `right to work` returns nothing. The candidate holds a
Pakistani passport; a hard EU-passport condition is an absolute bar, and it was invisible.

## Perpetrator 3 — MCS Group, "no sponsorship available"

Real text, third line of the posting, before the location block:

> Please not there is no sponsorship available for this role - all candidates must be in a
> commutable distance to Dundalk.

(The typo "Please not" for "Please note" is in the original.)

Measured: language `PASS`, experience `PASS`, seniority `PASS`.

Same cause as Perpetrator 2 — **the gate does not exist**. This posting is the cleanest possible
statement of the disqualifier, in the plainest possible English, three lines from the top, and
the module had no code path capable of noticing it.

Note for pattern design: the typo matters. Anchoring on `"please note there is no
sponsorship"` would have missed this real posting. The match has to be on the
`no sponsorship available` core, not on the polite preamble wrapped around it.

## Perpetrator 4 — Google, two "5 years" bullets

**The experience gate is not defective.** Run against the requirement lines as quoted:

```
5 years of experience in operations or business management, and vendor management.
5 years of experience using analytics or applying project management tools to address business issues.
```

Measured: `experience_verdict(...) -> FAIL`, reason `"5+ years stated as a hard requirement"`.
Both `5 years` figures were found by `_YEARS`, and the existing `worst = max(found, key=...)`
already implements the take-the-maximum policy.

So why did it reach Telegram? Because the run never had the text:

```
Vendor Operations Manager   overall=UNKNOWN  failed=[]  evidence_source=none
```

`description` was absent, `description_snippet` was absent, and the daily report labelled the
row `⚠️ unverified (no posting text)` with a score of 60. The gate returned `UNKNOWN`, which is
first-class in this module and deliberately **not** treated as failure, so the row survived.

This was re-checked live against LinkedIn through the authenticated browser. The posting is
marked *Promoted by hirer · Responses managed off LinkedIn*, and its description container
mounts but never populates:

```
[id^="JobDetails_AboutTheJob"]  found: true   innerText: 0   textContent: 0   innerHTML: 1456
```

1456 bytes of nested skeleton `<div>`s with no text node. Confirmed after a full reload, a
scroll-into-view, an expand-button click, and extended hydration waits, using the pipeline's own
primary selector rather than the legacy fallbacks. The body is genuinely not served.

**Consequence for the charge sheet:** hardening the experience regex would not have stopped this
job. It is an *enrichment coverage* failure wearing an experience-gate costume. The gate work
below still matters — it guarantees the correct verdict the moment text exists — but the
open defect for this row is that no text was obtained.

## Perpetrator 5 — Coca-Cola Europacific Partners, Spanish description

Identical shape to Perpetrator 4:

```
Tecnico/a Operational Excellence   overall=UNKNOWN  failed=[]  evidence_source=none
```

Same live re-check, same result — `JobDetails_AboutTheJob_4456302370` present, `textContent`
length 0, `innerHTML` 1456 bytes of skeleton.

And as with Google, **the gate already works** when given Spanish prose of realistic length:

```
detect_posting_language(...) -> FAIL
  reason: 35 portuguese stopwords (19% of the text) against 1 English
```

The outcome is right, but the evidence is wrong, and that is a genuine defect: the stopword
lists for Spanish and Portuguese overlap heavily, so on Spanish input the tallies come out

| language | hits |
|---|---|
| portuguese | 35 |
| spanish | 24 |
| italian | 8 |
| english | 1 |

`lang = max(counts, key=counts.get)` picks **portuguese** for a Spanish posting. A gate that
rejects a job while naming the wrong language is hard to trust and hard to audit, so LAW 3
should report the language it actually detected.

## The acquittal — Leica Microsystems, "English and preferably German"

Real text, in *The Essential Requirements Of The Job Include*:

> English and preferably German

Measured: `language_verdict(...) -> PASS`, reason `"only optional language preferences
stated"`. **Not guilty.** German is explicitly preferred, not required, and the profile's
English is sufficient.

The mechanism that produces this acquittal is the branch ordering — `OPTIONAL_MARKERS` is tested
**before** `REQUIRED_MARKERS`:

```python
if any(mk in n for mk in OPTIONAL_MARKERS):      # "preferably" wins here
    optional_hits.append((lang, quote))
elif any(mk in n for mk in REQUIRED_MARKERS):
    hits.append((lang, quote))
```

This ordering is load-bearing and must survive the LAW 2 rewrite. The sentence contains
`"preferably"` (optional) and, once LAW 2 adds bare-conjunction detection, would also match
`"English and <language>"` (required). If required were tested first, or if the new
conjunction rule were allowed to bypass the optional check, **Leica would flip to `FAIL`** —
converting a correct acquittal into a false conviction. Optional-wins-over-required is the
invariant that keeps `English required, German preferred` and `English (German a plus)`
working too.

## Experience-gate defects found while probing

Perpetrator 4 exonerated the experience gate on *its* text, but systematic probing found three
real gaps, all confirmed by measurement:

| Phrase | Current | Reason given | Should be |
|---|---|---|---|
| `5 years in ops. 5 years in analytics.` | `PASS` | years stated without a mandatory marker | `FAIL` |
| `5 years of relevant background.` | `PASS` | years stated without a mandatory marker | `FAIL` |
| `A proven history of 5 years.` | `PASS` | no years requirement stated | `FAIL` |

- The first two miss because `MANDATORY_MARKERS` keys on the literal word `experience`. Strip
  that word — say `5 years in ops`, or swap in the synonym `background` — and a hard requirement
  reads as incidental prose.
- The third is subtler and is a **conflict**, not a gap: `"proven"` *is* in
  `MANDATORY_MARKERS`, but `"history"` is in `_NOT_TENURE`, which exists to stop the gate
  tripping over "our company history". The suppressor wins and the years figure is discarded.
  LAW 4 names `proven history` as a requirement phrase, so the two lists genuinely disagree and
  the fix has to resolve the precedence rather than just append a marker.

Confirmed **already correct**, and therefore to be pinned by tests rather than changed:
`5+ years`, `at least 5 years`, `minimum 5 years`, `up to 5 years` (ceiling 5), `3-5 years`
(ceiling 5 → `FAIL`), `2-3 years` (`PASS`), `2 years in X. 3 years in Y.` (max 3 → `PASS`),
and bullet prefixes `•`, `-`, `*`, `1.`.

## What the four laws must therefore change

1. **LAW 1 — sponsorship & citizenship.** Net-new gate; nothing to repair, everything to build.
   Convicts Perpetrators 2 and 3. Must match on the disqualifying core, not on polite
   preambles, because a real posting spelled it `Please not`.
2. **LAW 2 — multilingual requirement.** Add the missing intensifiers; add a language-agnostic
   native-speaker rule; add `mother tongue`; treat a bare `English and <language>` conjunction
   as required. Preserve optional-wins-over-required, or Leica flips.
3. **LAW 3 — description language.** Already rejects non-English bodies. Fix the
   Spanish-reported-as-Portuguese misattribution so the evidence names the right language.
4. **LAW 4 — experience.** Already handles ranges, ceilings, `+`, bullets, and the maximum
   across multiple figures. Close the three gaps above: `years in <domain>` without the word
   "experience", the `background` synonym, and the `proven history` / `_NOT_TENURE` conflict.

## Standing caveat on UNKNOWN

Two of the five perpetrators were never gated at all. No change to a regex reaches them,
because the module's asymmetry is deliberate: a wrongly-kept job costs a slot, a wrongly-dropped
job is invisible, so absent evidence yields `UNKNOWN` and `UNKNOWN` is not failure. Tightening
the gates raises the ceiling on what *can* be caught; it does nothing about postings whose text
never arrives. Those two rows are an enrichment-coverage problem and are reported as such.

## Verdict — what the four laws actually did

Measured against the same artifact the run gated on, after the rewrite:

| job | before | after | failed | evidence |
|---|---|---|---|---|
| BRP-Rotax | `PASS` | **`FAIL`** | `language` | "Very good German and English skills" |
| Baker Hughes | `PASS` | **`FAIL`** | `sponsorship` | "…and hold an EU passport" |
| MCS Group | `PASS` | **`FAIL`** | `sponsorship` | "Please not there is no sponsorship available…" |
| Google | `UNKNOWN` | `UNKNOWN` | — | no text; `FAIL` when given its bullets, `years_required=5` |
| Coca-Cola EP | `UNKNOWN` | `UNKNOWN` | — | no text; `FAIL` as `spanish` when given a Spanish body |
| Leica | `PASS` | **`PASS`** | — | acquittal held on all five gates |

Across the whole 21-row corpus exactly **3 rows changed verdict** — those three. The other 18
are byte-identical, which is the number that matters: every rule added here widens the net, and
the net is what removes jobs with no model in the loop.

Four gaps surfaced only when the tests were written, and all four were real rather than test
error — each is now a pinned case:

- `"This role is not eligible for immigration sponsorship."` — the role is the grammatical
  subject, so no verb of refusal appears and every "we do not sponsor" pattern missed it.
- `"Applicants must be EU citizens."` — the pattern read `citizen` singular. Real postings
  address a pool.
- `"Only Irish applicants will be considered."` — needed a demonym anchor, because
  `only … applicants` alone matches *"only shortlisted applicants will be contacted"*, which is
  boilerplate on half the corpus.
- `"A valid work permit is required."` — the same bare shape as Baker Hughes, with the demand
  carried by "is required" and no modal verb near the candidate.

Widening LAW 1 also forced two pardons, for the symmetric reason. `"happy to provide visa
sponsorship"` states an offer as a noun, and `"we will help you obtain a work permit"` states one
as assistance; without both, an employer's own promise to sponsor sat in the same posting as a
papers clause and the clause won.

One regression was introduced and caught by the existing suite: suppressing company-age prose
with prepositional phrases like `in business` ate Amaris's *"At least 15 years of experience in
business analysis"*. The suppressor now runs in the **lookbehind only**, on the asymmetry that a
corporate subject precedes a years figure while a requirement's domain follows it.

**The two UNKNOWN rows are unchanged and remain the open defect.** Both gates convict their text
the moment text exists; neither row has text. That is enrichment coverage, not gate policy.

