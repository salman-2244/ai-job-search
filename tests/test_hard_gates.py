"""Guards for the deterministic hard gates.

Every FAIL these functions return removes a job with no model in the loop, so each
test below names the posting whose exact wording motivated the rule. The wordings
are quoted from the 2026-08-19 corpus; they are the regression suite for a filter
that is otherwise invisible when it works.

Three properties matter more than any individual case:

  1. **UNKNOWN is not FAIL.** DHL's language line existed in the 2026-08-18 fetch
     and not in the 2026-08-19 one. A gate that read no-evidence as a failure would
     drop most of a LinkedIn corpus unread.
  2. **Optional beats required, always.** "Hungarian (preferred)" and "French is a
     plus" must pass even though "fluent" or "required" sits nearby.
  3. **A marker qualifies the number it stands next to.** Fressnapf's "Minimum 5-7
     years ... ideally within I2P, Finance Operations" has "ideally" scoping the
     *domain* at the end of the sentence. A sentence-wide ambiguity check let that
     one word excuse an unambiguous five-year floor.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "hard_gates.py"

_spec = importlib.util.spec_from_file_location("hard_gates", SCRIPT)
hg = importlib.util.module_from_spec(_spec)
sys.modules["hard_gates"] = hg
_spec.loader.exec_module(hg)


class LanguageGate(unittest.TestCase):
    """Read the requirement for the ROLE, not the language the ad is written in."""

    def verdict(self, text, title="Business Analyst"):
        return hg.language_verdict(title, text)["verdict"]

    def test_hungarian_required_fails(self):
        """DHL: "Fluent English and Hungarian" — a hard condition A2 cannot meet.

        This is the correction that motivated the whole gate. The old rule returned
        FLAG for a listed language required above the declared level, so DHL passed
        through and a CV was drafted for a role needing fluent Hungarian.
        """
        got = hg.language_verdict("Process Excellence Manager",
                                  "Fluent English and Hungarian.")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["languages_required"], ["hungarian"])
        self.assertIn("Hungarian", got["quote"])

    def test_hungarian_preferred_passes(self):
        """Xylem: "communication skills in English, and Hungarian (preferred)"."""
        self.assertEqual(
            self.verdict("Excellent communication skills in English, and "
                         "Hungarian (preferred)."), hg.PASS)

    def test_an_advantage_passes(self):
        """KPMG: "Hungarian knowledge is an advantage" — the distinction to keep."""
        self.assertEqual(self.verdict("Hungarian knowledge is an advantage."),
                         hg.PASS)

    def test_a_plus_passes(self):
        """Amaris: "English fluent. French or another language is a plus"."""
        self.assertEqual(
            self.verdict("English fluent. French or another official "
                         "international language is a plus."), hg.PASS)

    def test_german_c1_fails(self):
        self.assertEqual(self.verdict("Fluent German (C1) is required."), hg.FAIL)

    def test_spanish_mandatory_fails(self):
        self.assertEqual(self.verdict("Spanish language skills are mandatory."),
                         hg.FAIL)

    def test_optional_in_one_bullet_does_not_excuse_required_in_another(self):
        """Bullets are scanned separately, or one "advantage" pardons the posting."""
        self.assertEqual(self.verdict("Fluent German required. | English is an "
                                      "advantage."), hg.FAIL)

    def test_the_employers_country_is_not_a_language_requirement(self):
        """"Do not reject a job merely because the company operates in one of
        these countries." A Munich role advertised in English passes."""
        self.assertEqual(
            self.verdict("You will join our team in Munich, Germany, working "
                         "in English across our European sites."), hg.PASS)

    def test_english_only_passes(self):
        self.assertEqual(self.verdict("Excellent English communication skills "
                                      "required."), hg.PASS)

    def test_no_text_is_unknown_not_pass_and_not_fail(self):
        """The DHL asymmetry, pinned.

        PASS would repeat the mistake that drafted DHL; FAIL would discard every
        unenriched LinkedIn card. UNKNOWN is what tells the allocator to fetch it.
        """
        self.assertEqual(hg.language_verdict("Data Analyst", "")["verdict"],
                         hg.UNKNOWN)


class PostingLanguageDetection(unittest.TestCase):
    """The AYES case: an Italian posting the sentence rules cannot reach."""

    ITALIAN = (
        "Sei alla ricerca di un lavoro nel settore della moda e del lusso? La "
        "nostra azienda cerca un Data Engineer con esperienza per il nostro "
        "cliente. Il candidato ideale ha una solida conoscenza di SQL e delle "
        "piattaforme di business intelligence. Offriamo un contratto a tempo "
        "indeterminato con un ambiente di lavoro dinamico. Le principali "
        "responsabilita includono la gestione dei dati e delle analisi per i "
        "nostri clienti del settore moda. Sono richieste ottime capacita.")
    ENGLISH = (
        "We are looking for a Business Analyst to join our supply chain team. You "
        "will work with process owners to identify automation opportunities and "
        "build Power BI dashboards. What do you need to do to succeed? You do not "
        "need to be an expert, but you do need curiosity. The role reports into "
        "our operational excellence function and partners with procurement to do "
        "continuous improvement work. We offer hybrid working from our Budapest "
        "office and a strong learning budget. Do apply if this sounds like you.")

    def test_a_long_italian_posting_fails(self):
        got = hg.detect_posting_language("Data Engineer", self.ITALIAN)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["language"], "italian")

    def test_the_full_gate_escalates_it(self):
        self.assertEqual(
            hg.language_verdict("Data Engineer", self.ITALIAN)["verdict"], hg.FAIL)

    def test_an_ordinary_english_posting_passes(self):
        """The false positive this needs a baseline to avoid.

        Several stopword lists collide with English — "die", "van", "per", "na",
        "do". Without measuring English too, this posting scored 10 "Polish"
        stopwords and was flagged as a Polish-language job.
        """
        got = hg.detect_posting_language("Business Analyst", self.ENGLISH)
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertGreater(got["english_hits"], got["hits"])

    def test_a_thin_snippet_is_unknown_not_a_guess(self):
        """AYES reached the ranker on 503 truncated characters of SEO junk.

        There is no honest verdict from that much text, so the detector says so and
        the flag travels to the model rather than a fabricated FAIL.
        """
        got = hg.detect_posting_language("Data Engineer",
                                         "Sei alla ricerca di un lavoro? Data "
                                         "Engineer per il settore moda.")
        self.assertEqual(got["verdict"], hg.UNKNOWN)
        self.assertIn("too thin", got["reason"])

    def test_a_german_sentence_inside_an_english_posting_does_not_fail_it(self):
        mostly_english = (
            "This role is based in Munich. Die Stelle ist auch auf Deutsch "
            "ausgeschrieben. We are looking for a Business Process Analyst to join "
            "our supply chain organisation and work with process owners on "
            "automation opportunities using Power BI and SQL. You will report into "
            "the operational excellence function and partner with procurement "
            "teams across Europe. We offer hybrid working from our Munich office "
            "and a generous annual learning budget for every team member.")
        self.assertEqual(
            hg.detect_posting_language("Analyst", mostly_english)["verdict"],
            hg.PASS)


class APostingWrittenInAnotherLanguage(unittest.TestCase):
    """IHM Business School: 6,001 characters of Swedish that returned PASS.

    The AYES case above put the *thresholds* in. This is the case that showed the
    thresholds were being applied to a table that could not see the language: only
    7 of the 14 `BLOCKED_LANGUAGES` had a stopword list, and Swedish was not one of
    them. So IHM scored 0 hits in every list, and the detector's fallback branch
    reported "reads as English (0 English stopwords vs 0 italian)" — reading the
    absence of a match as evidence of English, on a body containing no English at
    all.

    IHM was independently caught by the truncation cap (its body carried
    `description_truncated`), which is why this went unnoticed. These tests
    therefore avoid that cap deliberately: the language gate has to reach the same
    verdict on its own, or the next Swedish posting that happens to fit under the
    cap walks straight through. That is no longer hypothetical — the cap moved to
    20,000 characters, and IHM's real body measures 19,460, so it now arrives
    *whole* and this gate is the only thing standing between it and a PASS.
    """

    # Verbatim from the 2026-08-23 rankset, row #12. Kept accented, as the pipeline
    # stores it, because `_norm` folding "ä"/"ö"/"å" is part of what is under test.
    IHM = (
        "Om rollen Har du ett starkt teknikintresse och trivs i gränslandet mellan "
        "verksamhet, system och utveckling? Har du erfarenhet av Salesforce, "
        "digitala plattformar eller systemförvaltning och vill arbeta praktiskt med "
        "modern AI, automation, digitala tjänster och AI-agenter? Då kan du vara den "
        "vi söker till vårt Digital Management-team. Vi söker nu en Digital "
        "Operations & AI Agent Specialist som vill vara med och utveckla, driva och "
        "förvalta IHMs digitala miljö. Du kommer att arbeta brett med "
        "verksamhetskritiska system, interna tjänster, integrationer, kundnära "
        "plattformar och AI-baserade lösningar…"
    )
    IHM_TITLE = "Digital Operations & AI Agent Specialist"

    def test_the_ihm_body_is_detected_as_swedish(self):
        got = hg.detect_posting_language(self.IHM_TITLE, self.IHM)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["language"], "swedish",
                         "naming the language is what makes this a FAIL rather than "
                         "a flag; an unnamed one is only ever UNKNOWN")

    def test_the_gate_fails_it_on_language_grounds_alone(self):
        got = hg.language_verdict(self.IHM_TITLE, self.IHM)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertIn("swedish", got["reason"])

    def test_it_fails_without_help_from_the_truncation_cap(self):
        """The whole point: `full_text=True`, no `description_truncated` anywhere.

        If this ever regresses to UNKNOWN the job is still held back, but for the
        wrong reason — and a shorter Swedish posting would then pass.
        """
        got = hg.evaluate({"title": self.IHM_TITLE, "description": self.IHM},
                          {"domain_in_title": True, "domain_from_description": True,
                           "enabler_in_title": True,
                           "enabler_from_description": True})
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertIn("language", got["failed"])
        self.assertEqual(got["evidence_source"], "description",
                         "this row must be judged as a whole body, so the verdict "
                         "cannot be credited to the truncation cap")

    def test_the_truncation_cap_does_not_soften_it(self):
        """A cut body still convicts. `_unverified` downgrades PASS only, so the
        row as it actually arrived — truncated — must still be a FAIL, not the
        UNKNOWN that an indiscriminate cap would produce."""
        got = hg.evaluate({"title": self.IHM_TITLE, "description": self.IHM,
                           "description_truncated": True},
                          {"domain_in_title": True, "domain_from_description": True,
                           "enabler_in_title": True,
                           "enabler_from_description": True})
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertIn("language", got["failed"])

    def test_every_blocked_language_has_a_stopword_list(self):
        """The structural guard that would have caught this in the first place.

        `BLOCKED_LANGUAGES` is the list of languages the profile cannot work in.
        Any entry without a `_STOPWORDS` list is invisible to the detector, so
        adding a language to one table and not the other silently produces the IHM
        bug for that language.
        """
        missing = sorted(set(hg.BLOCKED_LANGUAGES) - set(hg._STOPWORDS))
        self.assertEqual(missing, [],
                         f"blocked but undetectable: {missing}")

    def test_the_other_nordic_bodies_are_caught_too(self):
        """Swedish was the row that surfaced; the gap was the whole tail of the
        table. Danish and Norwegian are near-identical, so the assertion is that
        the posting is rejected, not which of the three is named."""
        danish = (
            "Om jobbet Vi soger en dygtig dataanalytiker til vores team i "
            "Kobenhavn. Du vil arbejde med analyse af store datamaengder og "
            "rapportering til forretningen. Vi forventer at du har erfaring med "
            "SQL og Power BI, og at du har kendskab til automatisering af "
            "processer. Det er ikke et krav at du har arbejdet med maskinlaering, "
            "men det er en fordel. Vi tilbyder fleksible arbejdstider og gode "
            "muligheder for faglig udvikling. Send din ansogning til os hurtigst "
            "muligt, da vi indkalder til samtaler lobende.")
        got = hg.detect_posting_language("Dataanalytiker", danish)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertIn(got["language"], {"danish", "norwegian", "swedish"})


class APostingThatIsNotEnglishAndCannotBeNamed(unittest.TestCase):
    """The generalisation: the table can only ever catch languages it lists.

    Extending `_STOPWORDS` fixes Swedish and six others, but the same failure is
    waiting for Turkish, Estonian, Slovak or anything else absent from it. So the
    detector now also measures English on its own terms instead of inferring it
    from "nothing else matched".

    This yields UNKNOWN rather than FAIL on purpose. "This is not English" is a
    reason to look at the posting; only a named language is a reason to reject it.
    """

    TURKISH = (
        "Şirketimiz bünyesinde çalışacak Veri Bilimci arıyoruz. Adayların "
        "üniversite mezunu olması ve analitik düşünme yeteneğine sahip olması "
        "beklenmektedir. Görev tanımı kapsamında büyük veri kümeleri üzerinde "
        "çalışacak, raporlama süreçlerini yürütecek ve iş birimleriyle yakın "
        "ilişki kuracaktır. Tercih edilen nitelikler arasında bulut "
        "teknolojileri bilgisi, makine öğrenmesi tecrübesi ve güçlü iletişim "
        "becerileri yer almaktadır. Esnek çalışma saatleri sunulmaktadır. "
        "Başvurular internet sitemiz üzerinden alınacaktır. Değerlendirme "
        "sürecinde tüm adaylara geri bildirim yapılacaktır."
    )

    def test_an_unlisted_language_is_flagged_not_passed(self):
        got = hg.detect_posting_language("Veri Bilimci", self.TURKISH)
        self.assertEqual(got["verdict"], hg.UNKNOWN)
        self.assertTrue(got["english_absent"])
        self.assertIn("does not read as English", got["reason"])

    def test_it_is_not_given_a_language_it_was_not_matched_to(self):
        """A handful of incidental hits in some list must not be reported as the
        posting's language — that would put a false statement in the report."""
        self.assertIsNone(hg.detect_posting_language("Veri Bilimci",
                                                     self.TURKISH)["language"])

    def test_the_gate_does_not_report_no_condition_stated(self):
        """The exact wording the bug produced. "No language condition stated" is a
        claim about text that was read; nobody read this."""
        got = hg.language_verdict("Veri Bilimci", self.TURKISH)
        self.assertEqual(got["verdict"], hg.UNKNOWN)
        self.assertNotIn("no language condition stated", got["reason"])
        self.assertIn("does not read as English", got["reason"])

    def test_a_thin_snippet_is_still_judged_as_thin_not_as_foreign(self):
        """The AYES discipline is untouched and takes precedence.

        A 200-character card is not evidence of a language either way, and saying
        "this is not English" about it would be the same overreach in a new place.
        """
        got = hg.detect_posting_language("Veri Bilimci", self.TURKISH[:200])
        self.assertEqual(got["verdict"], hg.UNKNOWN)
        self.assertFalse(got["english_absent"])
        self.assertIn("too thin", got["reason"])


class TheLanguageDetectorDoesNotOverFire(unittest.TestCase):
    """Controls. A few foreign tokens are not a foreign posting.

    Company names, city names and a line about a language being nice to have all
    put non-English words into an English body, and none of them mean the posting
    is unreadable. The English-density floor is set well below what real English
    postings reach so these keep passing.
    """

    def verdict(self, text, title="Data Analyst"):
        return hg.language_verdict(title, text)["verdict"]

    # Verbatim from the 2026-08-23 rankset: genuine English, a Polish city name in
    # two spellings, markdown scaffolding, and the *lowest* English-stopword
    # density of any real English body in that corpus (0.194 per token). The
    # threshold sits at 0.06, so this is the row that pins the headroom.
    OCADO = (
        "# Data Analyst (mid level)\n\n**Location:** Krakow, Poland\n\n"
        "**Department:** Inbound\n\n**Data Analyst (Fulfilment - Supply Chain & "
        "Inbound) | Hybrid Working | Kraków**\n\n**Introduction:** We are Ocado "
        "Group, and we're bringing world-class automation to online grocery. Our "
        "Ocado Smart Platform (OSP) combines cutting-edge robotics, AI, and IoT "
        "within our advanced CFCs (Customer Fulfilment Centres). We've mastered "
        "the single pick, transforming online delivery for our global partners. "
        "Join us and be part..."
    )

    def test_the_tersest_real_english_body_still_passes(self):
        got = hg.detect_posting_language("Data Analyst", self.OCADO)
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertFalse(got["english_absent"],
                         "this body sets the floor the threshold was chosen "
                         "against; if it trips, the threshold is too high")

    def test_a_foreign_company_name_does_not_flag_the_posting(self):
        self.assertEqual(self.verdict(
            "Accenture España is hiring a Data Analyst to join our Budapest "
            "delivery centre. You will build Power BI dashboards for supply "
            "chain stakeholders and work with process owners across Europe to "
            "identify automation opportunities. We are looking for someone who "
            "is comfortable with SQL and has an interest in machine learning. "
            "The team works in English and we offer hybrid working from our "
            "office in the city centre, with a generous learning budget for "
            "every member of the team."), hg.PASS)

    def test_a_language_nice_to_have_line_does_not_flag_the_posting(self):
        """The user's own example. This is the optional-marker path, and it must
        not be second-guessed by the detector reading the same words."""
        self.assertEqual(self.verdict(
            "We are looking for a Business Intelligence Analyst to join our "
            "operations team in Budapest. You will own the reporting layer and "
            "partner with finance on forecasting. Hungarian is a plus but not "
            "required; all of our documentation and meetings are in English. "
            "You should be confident with SQL and Power BI, and curious about "
            "automating the manual steps out of a monthly close. We offer "
            "hybrid working and support for professional certification."),
            hg.PASS)

    def test_an_incidental_foreign_phrase_does_not_flag_the_posting(self):
        self.assertEqual(self.verdict(
            "Willkommen bei unserem Team! We are recruiting a Data Engineer for "
            "our Munich hub, and the working language of the team is English. "
            "You will build data pipelines on Azure and work with analysts to "
            "expose clean datasets for reporting. Experience with Python and SQL "
            "is what matters most to us. Our office sits near the "
            "Hauptbahnhof and we offer relocation support for candidates moving "
            "to Germany, as well as a budget for language classes if you want "
            "them."), hg.PASS)

    def test_a_dense_technical_body_is_not_mistaken_for_a_foreign_one(self):
        """The one realistic way the English floor could over-fire: a posting
        written as a keyword list rather than prose. It costs a flag, never a
        rejection — but it should not even flag at this density."""
        got = hg.detect_posting_language(
            "Data Engineer",
            "Requirements: Python, SQL, Spark, Airflow, dbt, Snowflake, Azure "
            "Data Factory, Databricks, Kafka, Terraform, Docker, Kubernetes, "
            "Git, CI/CD, Power BI, Tableau, Looker, pandas, NumPy, scikit-learn, "
            "PyTorch. Nice to have: Scala, Go, Rust. We expect you to have "
            "shipped at least one production pipeline and to be comfortable "
            "reviewing the code of others on the team. Location: Budapest or "
            "remote within the EU. Contract: permanent, full time.")
        self.assertEqual(got["verdict"], hg.PASS)


class ExperienceGate(unittest.TestCase):
    """Only an explicit, unambiguous 4+ is a discard."""

    def verdict(self, text, title="Analyst"):
        return hg.experience_verdict(title, text)["verdict"]

    def test_the_six_corpus_failures(self):
        """The exact wordings that discarded six of the 25 deep-ranked jobs.

        All six were discarded by the model *after* consuming a slot. Every one of
        these strings was already present at pre-rank time.
        """
        cases = {
            "Fressnapf": "Minimum 5-7 years of experience delivering and owning "
                         "complex solutions, ideally within I2P, Finance Operations.",
            "Thermo Fisher": "5+ years of experience in digital transformation.",
            "HARMAN": "At least 5 years of experience as a Business Analyst.",
            "RED Global": "Tenure: 10+ years BPM/BPI.",
            "Xylem": "Experience 5-8 years in Continuous Improvement / Lean.",
            "Amaris": "At least 15 years of experience in business analysis.",
        }
        for company, text in cases.items():
            with self.subTest(company=company):
                self.assertEqual(self.verdict(text), hg.FAIL)

    def test_fressnapf_ideally_does_not_pardon_the_range(self):
        """"ideally" scopes the domain at the end of the sentence, not the years.

        This was inspected by hand during the 2026-08-19 review and recorded as a
        genuine FAIL. A sentence-wide ambiguity check reverses it, which is why the
        markers are positional.

        `years_required` is 7, where this asserted 5 until 2026-08-24: under the
        ceiling policy "Minimum 5-7 years" is a seven-year ask. The verdict does not
        move — this posting fails on either reading — so what is pinned here is the
        *figure reported*, which is the part a human scanning the deferral list reads.
        """
        got = hg.experience_verdict(
            "I2P Digital Process Architect",
            "Minimum 5-7 years of experience delivering and owning complex "
            "solutions, ideally within I2P, Finance Operations, or Procurement.")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["years_required"], 7)

    def test_siemens_preferably_does_not_pardon_an_at_least_floor(self):
        """Siemens Healthineers' "Project Manager - Supplier Management".

        Reported as a *verified pass* in the 2026-08-23 top 25 — the #1 row — while
        the live posting states "At least 5 years". The gate had the full 6001-char
        enriched body and read the right sentence; it excused it because "preferably"
        landed 44 characters after the figure, inside the 45-char lookahead, and the
        ambiguity branch is tested before the mandatory one. The "preferably"
        qualifies the *industry*, not the years. This is the case that made an
        explicit floor stated ahead of the figure outrank a qualifier stated after
        it — see HARD_FLOOR_MARKERS.
        """
        got = hg.experience_verdict(
            "Project Manager - Supplier Management",
            "Master's degree in Engineering or a related field OR Bachelor's degree "
            "in a relevant discipline combined with significant professional "
            "experience in Supplier Quality, Strategic Procurement, Supply Chain "
            "Management, or a related field. At least 5 years of project management "
            "experience, preferably in a highly regulated industry (e.g. medical "
            "devices, in vitro diagnostics, rail, automotive).")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["years_required"], 5)

    def test_an_ambiguity_marker_still_pardons_a_figure_with_no_stated_floor(self):
        """The other side of the fix: with no explicit floor, the qualifier wins.

        These must keep passing, or catching Siemens would have cost every soft
        "ideally 5 years" a false discard.
        """
        for text in ("Ideally 5 years of experience in analytics.",
                     "5 years, ideally, in a KPI-driven environment.",
                     "Approximately 5 years of relevant experience.",
                     "8 years of experience preferred.",
                     "5+ years or equivalent experience."):
            with self.subTest(text=text):
                self.assertEqual(self.verdict(text), hg.PASS)

    def test_citi_plus_minus_five_years_passes(self):
        """"+/- 5 years of experience in Finance industry" — two markers at once.

        Approximate, and scoped to a sector rather than the analytical core. A naive
        numeric gate discards it; it was the #2 pick of the run at 89.
        """
        got = hg.experience_verdict(
            "Digital Transformation Senior Analyst",
            "+/- 5 years of experience in Finance industry.")
        self.assertEqual(got["verdict"], hg.PASS)

    def test_a_range_is_graded_at_its_ceiling(self):
        """MSCI's "1-3 years" passes; Eaton's "3-5 years" does not.

        "3-5" passed until 2026-08-24, when the floor reading was replaced on
        instruction: a posting advertising 3-5 is competing for someone with five, and
        the low end is the concession it is prepared to make rather than the bar it
        set. "1-3" still passes because its ceiling, not just its floor, is met.
        """
        self.assertEqual(self.verdict("1-3 years of relevant experience."), hg.PASS)
        self.assertEqual(self.verdict("3-5 years of experience required."), hg.FAIL)

    def test_every_separator_reaches_the_same_requirement(self):
        """One requirement figure per range, whatever wrote the separator.

        The en-dash pair is the reason this exists, and the mechanism was worse than
        an inconsistent policy. `_norm` strips any non-ASCII dash to a space, so
        "3–5 years" reached `_YEARS` as "3 5 years" — where the range branch cannot
        match at all and the pattern falls through to the trailing "5 years". The
        en-dash forms were therefore *already* graded at 5 and 8 while their hyphen
        twins were graded at 3 and 6: same policy on paper, opposite figures in
        practice, decided by a typographic choice in the posting. Both now arrive at
        the ceiling by design rather than one of them by accident.
        """
        for text, want in (("3-5 years of experience required.", 5),
                           ("3–5 years of experience required.", 5),
                           ("6-8 years of experience required.", 8),
                           ("6–8 years of experience required.", 8),
                           ("6—8 years of experience required.", 8),
                           ("6−8 years of experience required.", 8),
                           ("6/8 years of experience required.", 8),
                           ("6 to 8 years of experience required.", 8),
                           ("6 up to 8 years of experience required.", 8)):
            with self.subTest(text=text):
                self.assertEqual(
                    hg.experience_verdict("Analyst", text)["years_required"], want)

    def test_norm_folds_every_dash_onto_the_ascii_hyphen(self):
        """The root cause, pinned one level below the gate.

        Asserting only through `experience_verdict` would let a future rewrite of
        `_norm`'s strip silently reintroduce the bug for every *other* pattern that
        depends on a dash surviving — the years gate is simply where it was noticed.
        """
        for dash in ("-", "‐", "‑", "‒", "–", "—", "―", "−", "－"):
            with self.subTest(dash=dash):
                self.assertEqual(hg._norm(f"3{dash}5 years"), " 3-5 years ")

    def test_a_plus_figure_is_unchanged_by_the_ceiling_policy(self):
        """"5+ years" has no upper bound to raise, so it must not move.

        Worth pinning explicitly: the policy is expressed as `max(low, high)`, and a
        regression in the single-figure path (`low == high`) would be invisible in
        every range case above.
        """
        for text in ("5+ years of experience in digital transformation.",
                     "At least 5 years of experience as a Business Analyst."):
            with self.subTest(text=text):
                got = hg.experience_verdict("Analyst", text)
                self.assertEqual(got["verdict"], hg.FAIL)
                self.assertEqual(got["years_required"], 5)

    def test_a_range_topping_out_at_the_profile_passes(self):
        """"2-3 years" against a profile of ~3.7: the ceiling is 3, which is met."""
        got = hg.experience_verdict("Analyst", "2-3 years of experience required.")
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertEqual(got["years_required"], 3)

    def test_two_to_four_years_fails_which_contradicts_the_brief(self):
        """The one case where the ceiling policy contradicts the brief that set it.

        The 2026-08-24 instruction asked for "2-4 years" to PASS, reasoning "it starts
        with 2 so I can take it, 3 is mid". That is floor-or-midpoint reasoning, and
        it cannot coexist with the ceiling rule the same instruction imposes: the
        ceiling is 4, MAX_YEARS_ELIGIBLE is 3, so 4 > 3 and the posting fails. Every
        other case in the brief is satisfied by the strict rule; only this one is not.

        Pinned as FAIL because that is what the code does, not because the question is
        settled. Two amendments would deliver the requested PASS, and both are one
        line in `_experience_verdict`:
          * midpoint allowance — pass when (low + required) / 2 <= MAX_YEARS_ELIGIBLE.
            Matches the stated reasoning exactly: 2-3 -> 2.5 pass, 2-4 -> 3 pass,
            3-5 -> 4 fail, 6-8 -> 7 fail.
          * one-year tolerance — pass when required - 1 <= MAX_YEARS_ELIGIBLE. Same
            four outcomes, but it also softens single figures, so "4 years minimum"
            would start passing. That is a wider change than it looks.
        Either keeps `years_required` at the ceiling; they alter only the comparison.
        Neither moves any row in the 2026-08-23 LinkedIn set — the three ranges there
        (3-5, 5-8, 6-8) fail under all three readings.
        """
        got = hg.experience_verdict("Analyst", "2-4 years of experience required.")
        self.assertEqual(got["years_required"], 4)
        self.assertEqual(got["verdict"], hg.FAIL)

    def test_the_fail_reason_spells_out_a_range_it_read_at_the_ceiling(self):
        """A reason of "5+ years" against a posting printing "3-5" reads as the gate
        misquoting the posting. The reason has to show the policy, not hide it."""
        got = hg.experience_verdict("Analyst", "3-5 years of experience required.")
        self.assertIn("3-5 years", got["reason"])
        self.assertIn("ceiling of 5", got["reason"])
        self.assertIn("3-5 years", got["quote"])

    def test_years_scoped_to_an_adjacent_domain_pass(self):
        self.assertEqual(self.verdict("6 years of experience in the telecom "
                                      "sector."), hg.PASS)

    def test_preferred_years_are_not_a_hard_requirement(self):
        self.assertEqual(self.verdict("8 years of experience preferred."), hg.PASS)
        self.assertEqual(self.verdict("Ideally 6 years in analytics."), hg.PASS)
        self.assertEqual(self.verdict("5 years of experience or equivalent."),
                         hg.PASS)

    def test_a_figure_that_is_not_about_tenure_is_skipped(self):
        """Company age and contract length are not candidate requirements."""
        self.assertEqual(self.verdict("Founded 25 years ago, we lead the market."),
                         hg.PASS)
        self.assertEqual(self.verdict("This is a 2 years fixed-term contract."),
                         hg.PASS)

    def test_seniority_words_alone_do_not_fail_the_gate(self):
        """This gate reads numbers. A grade word is `seniority_verdict`'s business.

        The assertion is unchanged from when "Senior" was not a discard at all,
        because it was never about whether the job is wanted — it is about keeping
        the two filters from overlapping. If the years gate started failing on the
        word, its `years_required` and its quote would both be fiction, and the
        seniority FAIL would be reported under the wrong gate's name.
        """
        self.assertEqual(self.verdict("We are hiring a Senior Business Analyst "
                                      "for our process team.",
                                      title="Senior Business Analyst"), hg.PASS)

    def test_no_text_is_unknown(self):
        self.assertEqual(self.verdict(""), hg.UNKNOWN)


class SeniorityGate(unittest.TestCase):
    """A senior grade in the title is a discard, read lexically and from the title.

    Added 2026-08-22 on an explicit instruction: "Add a hard gate that excludes any
    job whose title contains 'Senior' (case-insensitive; also catch 'Sr.', 'Sr ',
    'Snr')... this is a separate lexical filter, since a 'Senior X' title can appear
    without an explicit years figure in the body."

    That last clause is the reason it cannot be folded into the experience gate. On
    the 2026-08-22 corpus 99 of 590 titles carry a grade marker, and most of them
    state no years anywhere the pre-rank stage can read.
    """

    def verdict(self, title):
        return hg.seniority_verdict(title)["verdict"]

    def test_the_four_spellings_named_in_the_instruction(self):
        """All four, and all four appear in real corpora rather than only in theory.

        "Sr." and "Sr " are separate cases because the period is what makes the
        abbreviation safe to match — without it "sr" has to be defended against
        "SRE" and "Sri", which is what the boundary test below covers.
        """
        for title in ("Senior ML Ops Engineer",
                      "Sr. Business Systems Analyst",
                      "Sr Program Manager, Global Finance Transformation (GFT)",
                      "Snr Data Analyst"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.FAIL)

    def test_the_markers_are_the_ones_that_were_asked_for(self):
        """Pins the list itself, so a marker cannot be dropped silently.

        Deliberately absent, and each for a reason rather than an oversight:
        "staff" and "chief" were not in the instruction (the corpus's one Staff
        title, "Expert / Staff Data Scientist", is caught on "expert" anyway), and
        "vp"/"vice president" would need a two-word rule this gate does not have.
        """
        self.assertEqual(set(hg.SENIORITY_MARKERS),
                         {"senior", "sr", "snr", "lead", "leader",
                          "principal", "head", "director", "expert"})

    def test_case_does_not_matter(self):
        for title in ("SENIOR DATA ANALYST", "senior data analyst",
                      "SR. DATA ANALYST"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.FAIL)

    def test_the_marker_need_not_lead_the_title(self):
        """"Supply Chain Sr Analyst" and "Test - Supply Chain Senior Business
        Analyst" are both in the 2026-08-22 corpus. A leading-anchor match would
        have let both through while catching the ones that happen to start with it.
        """
        self.assertEqual(self.verdict("Supply Chain Sr Analyst"), hg.FAIL)
        self.assertEqual(self.verdict("Test - Supply Chain Senior Business Analyst"),
                         hg.FAIL)
        self.assertEqual(self.verdict("Manager/ Sr. Manager - Data Product Manager"),
                         hg.FAIL)

    def test_a_separator_counts_as_a_boundary(self):
        """`_norm` keeps `-`, `/` and `&`, so the marker arrives glued to them.

        A dual-grade posting is still a posting for the senior grade as far as this
        filter is concerned; the instruction says any title *containing* the word.
        """
        self.assertEqual(self.verdict("Junior/Senior Analyst"), hg.FAIL)
        self.assertEqual(self.verdict("Sr-Manager, Analytics"), hg.FAIL)

    def test_the_abbreviation_does_not_match_inside_a_word(self):
        """The cost of matching "sr" loosely, spelled out.

        "SRE Manager" is a job this profile might want and "Sri Lanka" is a location,
        not a grade. A substring match on "sr" discards both, and it discards them
        *silently* — they land in the deferred list quoting a seniority marker that is
        not in the title.
        """
        for title in ("SRE Manager", "Sri Lanka Operations Analyst",
                      "Serious Games Designer", "Business Analyst",
                      "AI Engineer", "Data Scientist"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.PASS)

    def test_any_standalone_lead_is_a_grade_however_the_title_compounds_it(self):
        """The generalization, and the reason it is not a list of compounds.

        The 2026-08-22 corpus carries Enablement, Platform Team, Benefits
        Operations, Value Stream, Reporting & Analytics, Global SCM Project and
        Digital Go to Market variants — seven shapes in one day's fetch. Enumerating
        them would have caught those seven and missed the eighth, so the rule is the
        word standing alone rather than any compound built around it.
        """
        for title in ("Team Lead, S&OE", "Program Lead - Digital",
                      "Project Lead Analyst", "Workstream Lead, Finance",
                      "Process Lead", "Functional Lead SAP",
                      "Regional Lead, EMEA Analytics", "Country Lead Hungary",
                      "Business Lead, Data Products", "Operations Lead",
                      "Transformation Lead", "AI ENGINEERING LEAD",
                      "Lead Data Scientist", "Reporting & Analytics Lead"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.FAIL)

    def test_parentheses_around_the_grade_do_not_hide_it(self):
        """Siemens Energy's "(Lead) Project Manager AI Transformation".

        `_norm` strips the parentheses, so the standalone-word rule sees it without
        needing a case for the punctuation. It was the corpus's highest title-only
        scorer on 2026-08-22, which is why it has to be caught here rather than
        discovered at Phase 2 after it has already taken a rank slot.
        """
        got = hg.seniority_verdict("(Lead) Project Manager AI Transformation")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["marker"], "lead")

    def test_the_other_grades_above_this_level(self):
        """Principal, Head, Director, Expert and Leader, all from the real corpus.

        These were previously left to the Experience dimension at Phase 2, which is
        a slot-level mistake: the model spends a rank slot to arrive at 25/100 on a
        posting whose title had already settled it.
        """
        for title, marker in (
                ("Principal Data Scientist", "principal"),
                ("Senior Principal Product Manager - Campaign Platform", "senior"),
                ("Head of Data & AI", "head"),
                ("Head of Planning and Performance Management, EMEA", "head"),
                ("Director, Applied AI Product Manager", "director"),
                ("Associate Director, Finance and Operations", "director"),
                ("Expert AI Engineer (m/w/d)", "expert"),
                ("AI Model Validation Expert", "expert"),
                ("Value Stream leader", "leader"),
                ("Digital Go to Market Leader", "leader")):
            with self.subTest(title=title):
                got = hg.seniority_verdict(title)
                self.assertEqual(got["verdict"], hg.FAIL)
                self.assertEqual(got["marker"], marker)

    def test_leadership_is_not_a_leader(self):
        """The false positive the instruction named explicitly.

        "leadership" is one word and is not "leader", so splitting on separators
        refuses it without a special case. A substring match on "lead" discards all
        three of these, and the first two are roles this profile actively wants.
        """
        for title in ("Leadership Development Program Manager",
                      "Leadership & Culture Analyst",
                      "Misleading Metrics Analyst"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.PASS)

    def test_a_word_that_merely_contains_a_grade_is_not_one(self):
        """Six words a substring match discards and this profile wants kept.

        "Overhead" and "Headcount" are financial-controlling vocabulary — Salman's
        Wizz Air work. "Expertise Centre" is how several European employers name a
        CoE. Losing these to a substring match means losing them in the deferred
        list under a grade the title never claimed.
        """
        for title in ("Overhead Cost Controlling Analyst",
                      "Headcount Planning Analyst",
                      "Headhunter, Technical Recruiting",
                      "Expertise Centre Data Analyst",
                      "Principality of Monaco Finance Analyst",
                      "Downstream Process Engineer"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.PASS)

    def test_lead_as_a_noun_is_not_a_grade(self):
        """The one collision separators cannot settle on their own.

        "Lead time" is core supply-chain vocabulary and "lead-to-cash" is a named
        process — T4 and T5, two of the five target tracks. Both put "lead" at the
        front of the title as its own word, exactly where a grade would sit, so the
        following word is what disambiguates. None of these are in the 2026-08-22
        corpus; the guard is here so the gate cannot start lying later.
        """
        for title in ("Lead Time Reduction Analyst",
                      "Lead-to-Cash Process Manager",
                      "Lead to Cash Transformation Analyst",
                      "Lead Generation Specialist",
                      "Lead Management Analyst, CRM"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.PASS)

    def test_the_noun_exception_does_not_swallow_a_real_lead_grade(self):
        """The exception is keyed to the *next* word, not to the word "lead".

        Without that it would have turned the whole marker off — the failure mode of
        a guard written as a blocklist rather than as a disambiguation.
        """
        for title in ("Lead Data Scientist", "Lead Engineer, Copilot and Agents",
                      "Lead Technology Product Manager", "Lead People Partner"):
            with self.subTest(title=title):
                self.assertEqual(self.verdict(title), hg.FAIL)

    def test_a_grade_word_in_the_body_is_about_somebody_else(self):
        """"You will report to a senior manager" describes the org chart, not the job.

        The gate takes a description argument and ignores it, so this pins the
        decision rather than an accident of the signature. Gating on body text would
        discard postings for the level above the role they advertise.
        """
        got = hg.seniority_verdict(
            "Business Process Analyst",
            "You will report to a Senior Manager and work with our senior "
            "leadership team on process automation.")
        self.assertEqual(got["verdict"], hg.PASS)

    def test_a_fail_quotes_the_title_it_was_read_from(self):
        """Same rule as every other gate here: the evidence travels with the verdict.

        The title is short enough to quote whole, so there is no window to centre —
        but a discard with no quote at all would be the one unauditable gate in the
        module.
        """
        got = hg.seniority_verdict("  Senior   Performance   Test Analyst ")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["quote"], "Senior Performance Test Analyst")
        self.assertEqual(got["marker"], "senior")

    def test_it_never_returns_unknown(self):
        """Because enrichment could not answer it if it did.

        UNKNOWN is the signal "spend a LinkedIn request here". Requests fetch
        descriptions, and this gate reads titles, so an UNKNOWN would be asking for
        evidence that cannot change the verdict. An empty title is malformed, not
        ambiguous, and `has_signal` removes it on other grounds.
        """
        for title in ("", None, "   "):
            with self.subTest(title=title):
                self.assertEqual(hg.seniority_verdict(title)["verdict"], hg.PASS)


class PureTechnicalGate(unittest.TestCase):
    """The role must carry the business component, not merely the employer."""

    def test_a_bare_core_tech_title_fails(self):
        got = hg.pure_technical_verdict({"core_tech_marker": "machine learning "
                                                             "engineer",
                                         "domain_in_title": [],
                                         "domain_from_description": []})
        self.assertEqual(got["verdict"], hg.FAIL)

    def test_a_domain_in_the_title_passes(self):
        got = hg.pure_technical_verdict({"core_tech_marker": "data scientist",
                                         "domain_in_title": ["supply_chain"],
                                         "domain_from_description": []})
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertIn("supply_chain", got["reason"])

    def test_one_boilerplate_mention_does_not_exempt(self):
        """sennder: "Machine Learning Engineer" at a road-freight logistics company.

        The requirement, quoted: a job "should not automatically qualify merely
        because the company operates in Supply Chain."
        """
        got = hg.pure_technical_verdict({"core_tech_marker": "machine learning "
                                                             "engineer",
                                         "domain_in_title": [],
                                         "domain_from_description": ["supply_chain"]})
        self.assertEqual(got["verdict"], hg.FAIL)

    def test_several_body_domains_do_exempt(self):
        got = hg.pure_technical_verdict(
            {"core_tech_marker": "data scientist", "domain_in_title": [],
             "domain_from_description": ["supply_chain", "process", "procurement"]})
        self.assertEqual(got["verdict"], hg.PASS)

    def test_the_threshold_is_configurable(self):
        axes = {"core_tech_marker": "data scientist", "domain_in_title": [],
                "domain_from_description": ["supply_chain"]}
        self.assertEqual(hg.pure_technical_verdict(axes, 1)["verdict"], hg.PASS)
        self.assertEqual(hg.pure_technical_verdict(axes, 3)["verdict"], hg.FAIL)

    def test_no_marker_passes(self):
        got = hg.pure_technical_verdict({"core_tech_marker": None,
                                         "domain_in_title": ["process"],
                                         "domain_from_description": []})
        self.assertEqual(got["verdict"], hg.PASS)

    def test_no_axes_is_unknown(self):
        """A job scored under the old single-axis model has nothing to classify."""
        self.assertEqual(hg.pure_technical_verdict(None)["verdict"], hg.UNKNOWN)


class QuotedEvidence(unittest.TestCase):
    """A FAIL's quote has to contain the wording the FAIL was reached on.

    An unauditable discard is worse than a wrong one: nobody can tell the two
    apart. Both cases below are real, from the 2026-08-21 live-enrichment run.
    """

    # No sentence ender anywhere in this string — no period, bullet, pipe or
    # newline. That is the shape LinkedIn's `detail` endpoint actually returns:
    # bullets joined with nothing between them, so a 4,800-character posting is
    # one single span and the head of that span is a company introduction.
    PREAMBLE = (
        "We are one of the fastest growing airlines in Europe and we are looking "
        "for a colleague to join our analytics team in Budapest, where you will "
        "support crew training planning with data, build the reporting that the "
        "operation uses every morning, and work with stakeholders across Flight "
        "Operations, Crew Planning and Training Delivery on a portfolio of "
        "continuous improvement initiatives that we measure in hours saved "
    )

    def test_the_preamble_outruns_the_quote_window(self):
        """Otherwise this class passes on a span the old head-slice quoted fine."""
        self.assertGreater(len(self.PREAMBLE), 300)
        self.assertEqual(len(hg._sentences(self.PREAMBLE)), 1)

    def test_the_experience_quote_contains_the_years_figure(self):
        """Wizz Air at offset ~2,700 of 4,473; Morgan Stanley at ~2,400 of 4,828.

        Both were correctly discarded and both quoted an opening paragraph that
        says nothing about years — the verdict right and the evidence useless.
        """
        cases = {
            "Wizz Air": ("Experience 4-6+ years of overall experience with 3+ "
                         "years in planning, analytics, operations or "
                         "performance management", "4-6+ years"),
            "Morgan Stanley": ("What you will bring 5+ years of experience in "
                               "Finance, Business Intelligence, Reporting or "
                               "Data Analytics", "5+ years"),
        }
        for company, (tail, trigger) in cases.items():
            with self.subTest(company=company):
                got = hg.experience_verdict("Data Analyst", self.PREAMBLE + tail)
                self.assertEqual(got["verdict"], hg.FAIL)
                self.assertIn(trigger, got["quote"])

    def test_the_language_quote_contains_the_language_name(self):
        """The same span shape with the hard language condition at the far end."""
        got = hg.language_verdict(
            "Data Analyst",
            self.PREAMBLE + "Requirements fluent German is required for the "
                            "daily work with our stakeholders in Munich")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertIn("German", got["quote"])

    def test_a_quote_never_outgrows_the_window(self):
        """The window is centred, not widened — the report renders it in a table."""
        got = hg.experience_verdict(
            "Data Analyst",
            self.PREAMBLE + "Requirements 7+ years of experience " + self.PREAMBLE)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertIn("7+ years", got["quote"])
        self.assertLessEqual(len(got["quote"]), 300)


class Combined(unittest.TestCase):
    """`evaluate` is what the caller uses; the four verdicts must combine sanely."""

    AXES = {"core_tech_marker": None, "domain_in_title": ["process"],
            "domain_from_description": []}

    def test_all_clear_is_pass(self):
        got = hg.evaluate({"title": "Automation Business Analyst",
                           "description": "You will work with process owners on "
                                          "automation. 2 years of experience."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.PASS)
        self.assertEqual(got["failed"], [])

    def test_any_failure_fails_and_is_named(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description": "At least 8 years of experience required."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertEqual(got["failed"], ["experience"])

    def test_multiple_failures_are_all_named(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description": "Fluent German required. At least 8 "
                                          "years of experience required."},
                          self.AXES)
        self.assertEqual(sorted(got["failed"]), ["experience", "language"])

    def test_a_senior_title_fails_the_combined_block(self):
        """The gate has to be wired in, not merely written.

        `evaluate` is the only entry point `prerank_jobs.py` calls, so a
        `seniority_verdict` that passes its own tests and is absent from here would
        filter nothing at all.
        """
        got = hg.evaluate({"title": "Senior Business Analyst",
                           "description": "You will work with process owners on "
                                          "automation. 2 years of experience."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertEqual(got["failed"], ["seniority"])
        self.assertEqual(got["seniority"]["marker"], "senior")

    def test_a_senior_title_fails_a_card_that_would_otherwise_be_unknown(self):
        """And it saves the enrichment request rather than spending one to confirm.

        A thin card normally returns UNKNOWN so Phase 1c will fetch its body. When
        the title alone settles it, fetching buys nothing — the discard is already
        decided on evidence in hand.
        """
        got = hg.evaluate({"title": "Sr. Continuous Improvement Manager"}, self.AXES)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertEqual(got["failed"], ["seniority"])
        self.assertEqual(got["evidence_chars"], 0)

    def test_a_thin_card_is_unknown_so_enrichment_can_target_it(self):
        """The whole reason the gates moved before the cut.

        UNKNOWN is the signal that spending a LinkedIn request on this job would buy
        information. PASS would hide the risk; FAIL would discard it unread.
        """
        got = hg.evaluate({"title": "Continuous Improvement Manager"}, self.AXES)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["evidence_chars"], 0)

    def test_the_snippet_is_used_when_there_is_no_full_description(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description_snippet": "Fluent German is required."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertGreater(got["evidence_chars"], 0)


class TestAnUnenrichedJobCanNeverPass(unittest.TestCase):
    """The fail-open that put unread jobs in the shortlist labelled PASS.

    `evaluate` took `description or description_snippet` as its evidence and the
    two gates that can pass on silence tested that string for truthiness. A ~500
    character search-card snippet is truthy, so "no language condition stated" and
    "no years requirement stated" came back PASS — from text that stops before most
    postings reach their requirements section. Measured on the real 2026-08-23
    rankset: 11 of 25 rows rendered PASS on both gates with an empty `description`
    and a snippet of 475-503 characters. Nobody had read any of them.

    The rule that replaces it: a snippet can convict, never acquit.
    """

    AXES = {"domain_in_title": ["supply_chain"], "domain_from_description": [],
            "enabler_in_title": [], "enabler_from_description": []}

    # Long enough to be a plausible card snippet, and it states no blocker — which
    # is the whole point. Under the old rule its truthiness alone bought a PASS.
    SNIPPET = (
        "We are a fast-growing logistics group operating across eleven European "
        "markets. As Business Analyst you will join our supply chain excellence "
        "team, working with operations and finance stakeholders to surface "
        "improvement opportunities, build reporting, and support decisions with "
        "data. You will report to the Head of Supply Chain Performance and work "
        "in a hybrid setup from our Budapest office. What you will do: analyse "
        "end-to-end flows, maintain dashboards, and partner with regional..."
    )

    def test_a_snippet_only_job_is_unverified_on_both_gates(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description_snippet": self.SNIPPET}, self.AXES)
        self.assertEqual(got["language"]["verdict"], hg.UNKNOWN)
        self.assertEqual(got["experience"]["verdict"], hg.UNKNOWN)
        self.assertEqual(got["overall"], hg.UNKNOWN)

    def test_the_same_text_as_a_fetched_body_does_pass(self):
        """The control. The downgrade must key on provenance, not on the words."""
        got = hg.evaluate({"title": "Business Analyst",
                           "description": self.SNIPPET}, self.AXES)
        self.assertEqual(got["language"]["verdict"], hg.PASS)
        self.assertEqual(got["experience"]["verdict"], hg.PASS)
        self.assertEqual(got["overall"], hg.PASS)

    def test_no_unenriched_job_can_render_as_pass(self):
        """The invariant, swept over every shape an unread job arrives in.

        `evidence_source` names the provenance, so this asserts the property the
        display surfaces depend on: overall PASS implies a fetched body.
        """
        titles = ["Business Analyst", "Data Scientist", "Process Manager",
                  "Continuous Improvement Manager", "Operations Analyst"]
        bodies = [None, "", "Short blurb...", self.SNIPPET, self.SNIPPET * 2]
        for title in titles:
            for body in bodies:
                job = {"title": title}
                if body is not None:
                    job["description_snippet"] = body
                got = hg.evaluate(job, self.AXES)
                with self.subTest(title=title, chars=len(body or "")):
                    self.assertNotEqual(
                        got["overall"], hg.PASS,
                        "an unenriched job rendered as PASS")
                    self.assertNotEqual(got["evidence_source"], "description")

    def test_a_snippet_still_fails_on_stated_evidence(self):
        """The asymmetry, restated as its own case so a future edit cannot lose it.

        Downgrading silence must not downgrade a statement. Each of these snippets
        says something disqualifying, and saying it in 500 characters makes it no
        less said.
        """
        cases = [
            ("Business Analyst", "Fluent German is required.", "language"),
            ("Business Analyst",
             "What you bring: at least 8+ years of experience in supply chain "
             "analytics is required for this role.", "experience"),
        ]
        for title, snippet, axis in cases:
            got = hg.evaluate({"title": title, "description_snippet": snippet},
                              self.AXES)
            with self.subTest(axis=axis):
                self.assertEqual(got["overall"], hg.FAIL)
                self.assertIn(axis, got["failed"])

    def test_evidence_source_distinguishes_the_three_provenances(self):
        for job, want in (
            ({"title": "Data Scientist"}, "none"),
            ({"title": "Data Scientist", "description_snippet": "x" * 400},
             "description_snippet"),
            ({"title": "Data Scientist", "description": "x" * 400}, "description"),
            # A fetched body wins even when a snippet is also present, which is the
            # case for every enriched LinkedIn card.
            ({"title": "Data Scientist", "description": "x" * 400,
              "description_snippet": "x" * 100}, "description"),
        ):
            with self.subTest(want=want):
                self.assertEqual(hg.evaluate(job, self.AXES)["evidence_source"],
                                 want)

    def test_the_unverified_reason_says_why(self):
        """A bare UNKNOWN is not enough — the report has to explain the label."""
        got = hg.evaluate({"title": "Business Analyst",
                           "description_snippet": self.SNIPPET}, self.AXES)
        self.assertIn("truncated card snippet", got["language"]["reason"])
        self.assertIn("truncated card snippet", got["experience"]["reason"])

    def test_a_textless_job_keeps_its_own_reason(self):
        """No text at all is a different fact from a truncated snippet."""
        got = hg.evaluate({"title": "Business Analyst"}, self.AXES)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertNotIn("truncated card snippet", got["language"]["reason"])
        self.assertEqual(got["evidence_source"], "none")

    def test_the_downgrade_is_reachable_from_the_gate_functions_directly(self):
        """`evaluate` is not the only caller; the flag has to work on its own."""
        self.assertEqual(
            hg.language_verdict("Business Analyst", self.SNIPPET,
                                full_text=False)["verdict"], hg.UNKNOWN)
        self.assertEqual(
            hg.experience_verdict("Business Analyst", self.SNIPPET,
                                  full_text=False)["verdict"], hg.UNKNOWN)
        # Default stays "trust the text" so existing callers are unchanged.
        self.assertEqual(
            hg.language_verdict("Business Analyst", self.SNIPPET)["verdict"],
            hg.PASS)


class TestATruncatedBodyCanNeverPass(unittest.TestCase):
    """The second half of the same fail-open, on the path the snippet fix missed.

    Capping snippets graded evidence by *which field* carried it: a non-empty
    `description` meant "a body was fetched", so it was trusted whole. But
    `enrich_linkedin.merge_detail` cuts bodies at `max_description_chars(job)` and
    sets `description_truncated` precisely so the loss is not invisible — and
    nothing downstream ever read that field. It was a dead flag. So a body ending
    mid-sentence in an ellipsis counted as a complete posting.

    The cap was 6000 when this was measured and is now 20000 for LinkedIn, which
    moves the threshold without touching the rule under test: these cases assert on
    the *flag*, not on any length, so they stay valid at any cap.

    Measured on the real 2026-08-23 rankset, 3 of 25 rows carried the flag and all
    3 rendered `overall: PASS`:

      #1  Siemens Healthineers  Project Manager - Supplier Management
      #2  Diligent              Product Operations Manager
      #12 IHM Business School   Digital Operations & AI Agent Specialist

    Siemens is a FAIL on stated evidence once HARD_FLOOR_MARKERS is applied, and a
    cut body convicting is fine — it said what it said. The other two passed on
    *silence*: "no years requirement stated", "no language condition stated", from
    text whose requirements section was below the cut. Those two are the rows this
    class pins. A 6001-character body is far better evidence than a 500-character
    card snippet, but "better" is not "complete", and only complete acquits.
    """

    AXES = {"domain_in_title": ["supply_chain"], "domain_from_description": [],
            "enabler_in_title": [], "enabler_from_description": []}

    # The head of each real body, verbatim. Both ran to exactly 6001 characters and
    # ended in "…"; what is kept here is the part the gates actually read and found
    # nothing disqualifying in, which is the whole point — the excerpt has to be
    # genuinely silent on years and language for the test to mean anything. (The
    # doubled "and and" in the Diligent text is in the posting.)
    DILIGENT = (
        "Here's a Summary Of The Role Data is only powerful when everyone agrees "
        "what it means. This is your chance to build a world class product "
        "behavioural data system and process from the ground up. We're rebuilding "
        "the internal product our teams use to measure product success and end-user "
        "value across all our 24+ products in four business units. You'll design "
        "the single source of truth: the metric definitions, the telemetry "
        "standards, the data contract engineering instruments against, the change "
        "governance processes and tooling, and the models that turn behaviour into "
        "portfolio insight. You'll educate and support the teams using this internal "
        "behavioural data product to set expectations and and…"
    )
    # Swedish, for a Swedish business school. When this class was written the
    # language gate returned "no language condition stated" on it; that gap is now
    # fixed, so this body convicts on language before the truncation cap is
    # reached. It is kept here because it is a real row that must never pass — but
    # the cap itself is demonstrated on DILIGENT, which is English. See
    # APostingWrittenInAnotherLanguage for the language side.
    IHM = (
        "Om rollen Har du ett starkt teknikintresse och trivs i gränslandet mellan "
        "verksamhet, system och utveckling? Har du erfarenhet av Salesforce, "
        "digitala plattformar eller systemförvaltning och vill arbeta praktiskt med "
        "modern AI, automation, digitala tjänster och AI-agenter? Då kan du vara den "
        "vi söker till vårt Digital Management-team. Vi söker nu en Digital "
        "Operations & AI Agent Specialist som vill vara med och utveckla, driva och "
        "förvalta IHMs digitala miljö. Du kommer att arbeta brett med "
        "verksamhetskritiska system, interna tjänster, integrationer, kundnära "
        "plattformar och AI-baserade lösningar…"
    )

    def row(self, title, body, *, truncated=True):
        """The row shape `prerank_jobs.py` hands to `evaluate`."""
        job = {"title": title, "description": body}
        if truncated:
            job["description_truncated"] = True
        return hg.evaluate(job, self.AXES)

    def test_the_diligent_row_is_unverified_not_a_pass(self):
        """#2 of the 2026-08-23 top 25, reported as a verified pass."""
        got = self.row("Product Operations Manager", self.DILIGENT)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["language"]["verdict"], hg.UNKNOWN)
        self.assertEqual(got["experience"]["verdict"], hg.UNKNOWN)
        self.assertEqual(got["failed"], [])

    def test_the_ihm_row_is_unverified_not_a_pass(self):
        """#12 of the same run, also reported as a verified pass.

        It is now stopped harder than the cap stops it. When this test was first
        written the language gate read 6,001 characters of Swedish as "no language
        condition stated", so the truncation cap was the only thing standing
        between this row and a verified pass, and the test pinned UNKNOWN. The
        detector has since been taught the languages it was missing, so the row
        fails outright on language.

        The assertion kept from before is the one that matters and is stated first:
        not a PASS, by any route.
        """
        got = self.row("Digital Operations & AI Agent Specialist", self.IHM)
        self.assertNotEqual(got["overall"], hg.PASS)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertEqual(got["language"]["verdict"], hg.FAIL)
        self.assertIn("language", got["failed"])
        self.assertEqual(got["experience"]["verdict"], hg.UNKNOWN,
                         "the cap is still doing its job on the axis the posting "
                         "says nothing about")

    def test_the_same_bodies_without_the_flag_still_pass(self):
        """The control. The cap must key on the flag, not on these words.

        Without this, the test above would also be satisfied by a gate that had
        started failing the posting for some unrelated reason.

        Only DILIGENT can serve as this control now. IHM was withdrawn from it
        deliberately: it fails on language with or without the flag, which is
        correct but makes it useless for isolating what the flag does.
        """
        got = self.row("Product Operations Manager", self.DILIGENT, truncated=False)
        self.assertEqual(got["overall"], hg.PASS)
        self.assertEqual(got["evidence_source"], "description")

    def test_the_reason_names_the_cut_and_not_a_card_snippet(self):
        """The report explains the label, so the explanation has to be true.

        These rows were never card snippets — 6001 characters of fetched body is a
        different fact, and reusing the snippet wording would have put a false
        statement in the report.
        """
        got = self.row("Product Operations Manager", self.DILIGENT)
        for axis in ("language", "experience"):
            with self.subTest(axis=axis):
                self.assertIn("cut off", got[axis]["reason"])
                self.assertNotIn("card snippet", got[axis]["reason"])

    def test_the_provenance_is_distinguishable_from_a_whole_body(self):
        """`run_daily.sh`'s gate_label branches on this to label the row honestly.

        Without a distinct value a cut body renders as a bare "unverified" that
        looks like "nothing was decided", losing the one fact that explains it.
        """
        got = self.row("Product Operations Manager", self.DILIGENT)
        self.assertEqual(got["evidence_source"], "description_truncated")
        self.assertEqual(got["evidence_chars"], len(self.DILIGENT))

    def test_a_cut_body_still_convicts_on_stated_evidence(self):
        """The asymmetry, carried over to this path: truncation pardons nothing.

        Siemens Healthineers (#1) is the case — its body was also truncated, and it
        states "At least 5 years". A cut body downgrades a PASS earned by silence
        and leaves a FAIL earned by a statement exactly where it is.
        """
        cases = [
            ("Project Manager - Supplier Management",
             "At least 5 years of project management experience, preferably in a "
             "highly regulated industry…", "experience"),
            ("Business Analyst", "Fluent German is required…", "language"),
        ]
        for title, body, axis in cases:
            with self.subTest(axis=axis):
                got = self.row(title, body)
                self.assertEqual(got["overall"], hg.FAIL)
                self.assertIn(axis, got["failed"])

    def test_no_truncated_body_passes_at_any_length(self):
        """The invariant, swept over lengths, so the fix is not a 6001-char special.

        The cap is about completeness, not size: a body cut at 500 characters and
        one cut at 12000 are both missing their tail.
        """
        silent = ("We are a growing European logistics group and you will join our "
                  "supply chain excellence team to build reporting with operations "
                  "and finance stakeholders. ")
        for length in (200, 500, 2000, 6001, 12000):
            body = (silent * (length // len(silent) + 1))[:length - 1] + "…"
            with self.subTest(chars=length):
                got = self.row("Business Analyst", body)
                self.assertNotEqual(got["overall"], hg.PASS,
                                    "a truncated body rendered as PASS")

    def test_an_absent_or_false_flag_is_not_treated_as_truncated(self):
        """Rows from portals that never set the field must be unaffected.

        22 of the 25 rows on the 2026-08-23 run had no flag at all, and the other
        aggregators do not write one. Reading a missing key as "truncated" would
        have downgraded the entire corpus to unverified.
        """
        body = "A complete posting body with no blocker stated anywhere in it."
        for flag in ({}, {"description_truncated": False}):
            with self.subTest(flag=flag):
                got = hg.evaluate({"title": "Business Analyst",
                                   "description": body, **flag}, self.AXES)
                self.assertEqual(got["overall"], hg.PASS)
                self.assertEqual(got["evidence_source"], "description")

    def test_a_truncated_flag_on_a_snippet_only_row_keeps_the_snippet_wording(self):
        """No `description`, so the body came from the card; the flag is not about it.

        `description_truncated` describes the fetched body. When there is none, the
        row is a snippet row and must read as one.
        """
        got = hg.evaluate({"title": "Business Analyst",
                           "description_snippet": "A pleasant blurb about us...",
                           "description_truncated": True}, self.AXES)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["evidence_source"], "description_snippet")
        self.assertIn("card snippet", got["language"]["reason"])


class TestUnverifiedJobsStayTargetableByEnrichment(unittest.TestCase):
    """The downgrade must grow the enrichment pool, not shrink the shortlist.

    `enrich_linkedin.gate_unknown` keys on `gates["overall"] == "UNKNOWN"`, so a job
    flipped from PASS to UNKNOWN becomes eligible for a fetch — which is the correct
    consequence and the reason no fourth verdict constant was introduced. A job that
    is merely unverified must never be discarded; only FAIL discards.
    """

    AXES = {"domain_in_title": ["supply_chain"], "domain_from_description": [],
            "enabler_in_title": [], "enabler_from_description": []}

    def test_a_downgraded_job_reads_as_a_gate_unknown_enrichment_target(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description_snippet": "A pleasant blurb about us..."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["failed"], [],
                         "unverified is not a failure and must discard nothing")


class SponsorshipGate(unittest.TestCase):
    """The bar this profile cannot clear by trying harder.

    The candidate holds a Pakistani passport. A posting that refuses sponsorship, or
    that demands a passport, citizenship or pre-existing work rights, is an absolute
    disqualifier — unlike a language line, there is no "preferred" reading of it. Two
    real 2026-08-24 postings reached Telegram because no gate in the module read work
    authorisation at all; both are pinned below.

    The gate distinguishes two bars, recorded in `bar`:

      * ``sponsorship_denied`` — the employer says it will not sponsor. Nothing
        pardons this.
      * ``authorisation_wall`` — the employer demands papers. An explicit offer of
        sponsorship elsewhere in the same posting pardons it, because the wall is
        then describing the destination state rather than a precondition.
    """

    def verdict(self, text, title="Business Analyst"):
        return hg.sponsorship_verdict(title, text)["verdict"]

    def test_mcs_group_no_sponsorship_available(self):
        """MCS Group, third line of the posting — the cleanest possible statement.

        The typo "Please not" for "Please note" is in the original, which is why the
        pattern anchors on the ``no sponsorship available`` core and not on the
        polite preamble wrapped around it.
        """
        got = hg.sponsorship_verdict(
            "Digital Transformation & Automation Manager",
            "Please not there is no sponsorship available for this role - all "
            "candidates must be in a commutable distance to Dundalk.")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["bar"], "sponsorship_denied")
        self.assertIn("no sponsorship available", got["quote"])
        self.assertFalse(got["sponsorship_offered"],
                         "a denial sentence must never be read as an offer")

    def test_baker_hughes_eu_passport(self):
        """Baker Hughes: "...and hold an EU passport".

        Stated bare, with no "must" — the demand is carried by the list it sits in.
        The language gate correctly passes this sentence (Italian genuinely *is*
        preferred), so the passport clause is the only thing standing between this
        posting and a drafted CV.
        """
        got = hg.sponsorship_verdict(
            "Supplier Fulfillment Specialist",
            "Be fluent in English (Italian knowledge is also preferred) and hold "
            "an EU passport")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["bar"], "authorisation_wall")
        self.assertIn("passport", got["quote"])

    def test_the_denials_that_must_all_convict(self):
        for text in ("Please note we do not offer visa sponsorship for this "
                     "position.",
                     "We are unable to sponsor candidates for this role.",
                     "Sponsorship is not available.",
                     "No visa sponsorship is provided.",
                     "We cannot sponsor work permits at this time.",
                     "This role is not eligible for immigration sponsorship.",
                     "Candidates must be able to work without sponsorship."):
            with self.subTest(text):
                self.assertEqual(self.verdict(text), hg.FAIL)

    def test_the_walls_that_must_all_convict(self):
        for text in ("You must have the right to work in the EU.",
                     "Applicants must be EU citizens.",
                     "Must hold an EU passport.",
                     "You must be a national of an EU member state.",
                     "Only Irish applicants will be considered.",
                     "Candidates must have permanent residency in Germany.",
                     "You must be legally authorized to work in the United "
                     "States without sponsorship.",
                     "Applicants must already have the right to work in the UK.",
                     "You must have unrestricted work authorisation.",
                     "A valid work permit is required."):
            with self.subTest(text):
                self.assertEqual(self.verdict(text), hg.FAIL)

    def test_a_negated_requirement_is_pardoned(self):
        """"No citizenship requirement" is the opposite of a citizenship bar.

        The negator is read in a short run-up window before the match, so the
        pardon cannot be borrowed from an unrelated earlier clause.
        """
        for text in ("No passport or citizenship requirement - we welcome all "
                     "applicants.",
                     "You do not need a work permit before applying.",
                     "We consider applicants regardless of citizenship.",
                     "Applications are welcome irrespective of nationality."):
            with self.subTest(text):
                self.assertEqual(self.verdict(text), hg.PASS)

    def test_an_explicit_offer_pardons_a_wall_but_never_a_denial(self):
        offer = ("We are happy to provide visa sponsorship and relocation "
                 "support. A valid work permit is required before your start "
                 "date.")
        self.assertEqual(self.verdict(offer), hg.PASS,
                         "an offer reframes a papers clause as the destination")
        denial = ("We provide visa sponsorship for most roles. For this role "
                  "there is no sponsorship available.")
        got = hg.sponsorship_verdict("Business Analyst", denial)
        self.assertEqual(got["verdict"], hg.FAIL,
                         "a boilerplate offer must not pardon a stated refusal")
        self.assertEqual(got["bar"], "sponsorship_denied")

    def test_an_ordinary_posting_states_no_bar(self):
        got = hg.sponsorship_verdict(
            "Data Analyst",
            "You will build dashboards in Power BI and partner with supply "
            "chain stakeholders across our European sites. We offer a hybrid "
            "working model and a competitive package.")
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertEqual(got["reason"], "no sponsorship or citizenship bar stated")

    def test_no_text_is_unknown_not_pass_and_not_fail(self):
        """Same asymmetry as every other gate: absent evidence is not innocence."""
        self.assertEqual(hg.sponsorship_verdict("Data Analyst", "")["verdict"],
                         hg.UNKNOWN)

    def test_a_card_snippet_cannot_clear_the_bar(self):
        """A truncated card is where a sponsorship clause hides — it is usually
        in the small print at the bottom, which a snippet never reaches."""
        got = hg.sponsorship_verdict("Data Analyst",
                                     "Join our team and make an impact.",
                                     full_text=False)
        self.assertEqual(got["verdict"], hg.UNKNOWN)

    def test_a_stated_bar_still_convicts_from_a_snippet(self):
        """The downgrade only softens PASS. Evidence that is present is used."""
        got = hg.sponsorship_verdict("Data Analyst",
                                     "No visa sponsorship available.",
                                     full_text=False)
        self.assertEqual(got["verdict"], hg.FAIL)


class TheGateTribunal(unittest.TestCase):
    """The six 2026-08-24 postings that put the gates on trial.

    Five reached Telegram and should not have; one reached Telegram and was right
    to. Each row below is the exact wording from
    ``/tmp/jobsearch_rankset_2026-08-24.json``, the artifact the run itself gated
    on, so this class is the regression suite for the tribunal's four laws.

    Two of the five are pinned as UNKNOWN rather than FAIL on purpose. Google and
    Coca-Cola were never gated at all: LinkedIn served 1456-byte skeletons and no
    body was ever retrieved, so those rows are an enrichment-coverage defect, not a
    gate defect. The gate returns the right verdict the moment text exists, which
    is what the two ``*_with_text`` tests below assert.
    """

    # Coca-Cola's real body was never served, so this is a representative Spanish
    # posting of realistic length rather than the original text. What it pins is
    # the LAW 3 behaviour the missing body would have triggered.
    SPANISH = (
        "En Coca-Cola Europacific Partners buscamos un Tecnico/a de Operational "
        "Excellence para nuestra planta. Tu mision sera liderar los proyectos de "
        "mejora continua y el desarrollo de los indicadores de gestion de la "
        "fabrica. Que buscamos en ti: titulacion universitaria en ingenieria, "
        "experiencia previa en entornos industriales y conocimientos de "
        "metodologias Lean y Six Sigma. Ofrecemos un plan de desarrollo "
        "profesional, formacion continua y un paquete retributivo competitivo, "
        "ademas de las ventajas sociales de la compania. Si te apasiona la mejora "
        "de los procesos y quieres formar parte de un equipo diverso, inscribete "
        "en la oferta y te contaremos mucho mas."
    )

    GOOGLE = ("5 years of experience in operations or business management, and "
              "vendor management.\n"
              "5 years of experience using analytics or applying project "
              "management tools to address business issues.")

    def test_perpetrator_1_brp_rotax_very_good_german(self):
        """"Very good German and English skills" — the one true pattern gap.

        ``REQUIRED_MARKERS`` had no "very good", ``OPTIONAL_MARKERS`` had none
        either, so the sentence was filed as neither and the posting was stamped
        PASS with the reason "no language condition stated".
        """
        got = hg.language_verdict("Strategic Maintenance - Data Analytics & "
                                  "Asset Strategy (m/f/d)",
                                  "Very good German and English skills")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["languages_required"], ["german"])
        self.assertIn("German", got["quote"])

    def test_perpetrator_2_baker_hughes_is_caught_by_sponsorship_not_language(self):
        """Both halves of the verdict matter.

        Convicting this posting on language grounds would have been *wrong* —
        Italian is genuinely preferred. The passport clause is the disqualifier,
        and it needed a gate that did not exist.
        """
        text = ("Be fluent in English (Italian knowledge is also preferred) and "
                "hold an EU passport")
        self.assertEqual(hg.language_verdict("Supplier Fulfillment Specialist",
                                             text)["verdict"], hg.PASS)
        self.assertEqual(hg.sponsorship_verdict("Supplier Fulfillment Specialist",
                                                text)["verdict"], hg.FAIL)

    def test_perpetrator_3_mcs_group_no_sponsorship(self):
        got = hg.sponsorship_verdict(
            "Digital Transformation & Automation Manager",
            "Please not there is no sponsorship available for this role - all "
            "candidates must be in a commutable distance to Dundalk.")
        self.assertEqual(got["verdict"], hg.FAIL)

    def test_perpetrator_4_google_with_text_reads_the_maximum_figure(self):
        """Two bullets, each stating five years. The policy is the maximum.

        The gate was never defective here — it was never given the text. Given the
        text it takes ``max(found)``, so neither bullet can be excused by the other.
        """
        got = hg.experience_verdict("Vendor Operations Manager", self.GOOGLE)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["years_required"], 5)

    def test_perpetrator_4_google_as_the_run_actually_saw_it(self):
        """No description, no snippet — UNKNOWN, and UNKNOWN is not failure.

        Pinned so the enrichment-coverage defect stays visible as itself. If this
        ever flips to FAIL, a gate has started convicting on absent evidence.
        """
        got = hg.evaluate({"title": "Vendor Operations Manager"}, None)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["failed"], [])

    def test_perpetrator_5_coca_cola_with_text_is_read_as_spanish(self):
        """The body is Spanish, and the evidence must say *spanish*.

        Before LAW 3 the overlapping stopword lists made a Spanish posting report
        as Portuguese — a gate that names the wrong language is hard to audit.
        """
        detected = hg.detect_posting_language("Tecnico/a Operational Excellence",
                                              self.SPANISH)
        self.assertEqual(detected["language"], "spanish")
        self.assertEqual(detected["verdict"], hg.FAIL)
        got = hg.language_verdict("Tecnico/a Operational Excellence",
                                  self.SPANISH)
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertEqual(got["reason"], "posting is written in spanish")

    def test_perpetrator_5_coca_cola_as_the_run_actually_saw_it(self):
        got = hg.evaluate({"title": "Tecnico/a Operational Excellence"}, None)
        self.assertEqual(got["overall"], hg.UNKNOWN)
        self.assertEqual(got["failed"], [])

    def test_the_acquittal_leica_english_and_preferably_german(self):
        """NOT GUILTY. "English and preferably German" is a preference.

        This is the case the LAW 2 conjunction rule most endangers: the sentence
        matches "English and <language>" *and* contains "preferably". Optional is
        tested before required, so the preference wins. If this flips to FAIL the
        rewrite has converted a correct acquittal into a false conviction.
        """
        got = hg.language_verdict(
            "Global Service Process & AI Enablement Manager",
            "The essential requirements of the job include: English and "
            "preferably German.")
        self.assertEqual(got["verdict"], hg.PASS)
        self.assertEqual(got["reason"], "only optional language preferences stated")
        self.assertEqual(got["languages_optional"], ["german"])
        self.assertEqual(got["languages_required"], [])

    def test_the_acquittal_survives_every_other_gate_too(self):
        got = hg.evaluate(
            {"title": "Global Service Process & AI Enablement Manager",
             "description": "The essential requirements of the job include: "
                            "English and preferably German. 3 years of "
                            "experience in service process management. We offer "
                            "relocation support and visa sponsorship."},
            {"domain_in_title": ["process"], "domain_from_description": [],
             "enabler_in_title": ["ai"], "enabler_from_description": []})
        self.assertEqual(got["overall"], hg.PASS)
        self.assertEqual(got["failed"], [])


class TheLawsEdgeCases(unittest.TestCase):
    """The named edge cases from the tribunal brief, both directions.

    The must-PASS half is the more important one. Every rule added by the four laws
    widens the net, and a gate that convicts an eligible job removes it silently.
    """

    def test_the_wordings_that_must_all_fail(self):
        for gate, text in (
                (hg.language_verdict, "Candidates must be a native speaker."),
                (hg.language_verdict, "Mother tongue German."),
                (hg.language_verdict, "Native level Dutch is expected."),
                (hg.language_verdict, "English and German."),
                (hg.sponsorship_verdict,
                 "Please note we do not offer visa sponsorship for this "
                 "position."),
                (hg.sponsorship_verdict,
                 "You must have the right to work in the EU."),
                (hg.experience_verdict, "3-5 years of experience in analytics."),
                (hg.experience_verdict,
                 "5 years in ops. 5 years in analytics."),
                (hg.experience_verdict, "5 years of relevant background."),
                (hg.experience_verdict, "A proven history of 5 years.")):
            with self.subTest(f"{gate.__name__}: {text}"):
                self.assertEqual(gate("Business Analyst", text)["verdict"],
                                 hg.FAIL)

    def test_the_wordings_that_must_all_pass(self):
        for gate, text in (
                (hg.experience_verdict, "2-3 years of experience in analytics."),
                (hg.experience_verdict,
                 "2 years of experience in X. 3 years of experience in Y."),
                (hg.language_verdict, "English (German a plus)."),
                (hg.language_verdict, "English required, German preferred."),
                (hg.language_verdict,
                 "We are looking for a native English speaker."),
                (hg.language_verdict,
                 "We are an English-speaking company with German and French "
                 "offices."),
                (hg.language_verdict,
                 "You will support our German and Austrian markets."),
                (hg.language_verdict,
                 "Strong analytical skills and very good stakeholder "
                 "management."),
                (hg.sponsorship_verdict,
                 "We provide visa sponsorship and relocation support."),
                (hg.sponsorship_verdict,
                 "We consider applicants regardless of citizenship."),
                (hg.sponsorship_verdict,
                 "Only shortlisted applicants will be contacted."),
                (hg.sponsorship_verdict,
                 "We will support your relocation and help you obtain a work "
                 "permit; a work permit is required to start."),
                (hg.sponsorship_verdict,
                 "You will work with our German and Austrian colleagues on "
                 "European rollouts."),
                (hg.sponsorship_verdict,
                 "A valid driving licence is required for site visits.")):
            with self.subTest(f"{gate.__name__}: {text}"):
                self.assertEqual(gate("Business Analyst", text)["verdict"],
                                 hg.PASS)

    def test_a_company_age_is_not_a_tenure_requirement(self):
        """The regression that the Amaris case caught during the rewrite.

        Suppressing company-age prose with prepositional phrases like "in
        business" silently ate Amaris's "At least 15 years of experience in
        business analysis" — a fifteen-year floor read as boilerplate. The
        suppressor is therefore checked in the *lookbehind only*: a corporate
        subject precedes the figure, a requirement's domain follows it.
        """
        floor = hg.experience_verdict(
            "Business Analyst",
            "At least 15 years of experience in business analysis.")
        self.assertEqual(floor["verdict"], hg.FAIL)
        self.assertEqual(floor["years_required"], 15)
        for prose in ("Our company has 18 years in business.",
                      "We have grown over 20 years in the market.",
                      "The company was founded 30 years ago."):
            with self.subTest(prose):
                self.assertEqual(
                    hg.experience_verdict("Business Analyst", prose)["verdict"],
                    hg.PASS)

    def test_a_native_demand_needs_the_demand_and_not_just_the_word(self):
        """"native English speaker" passes; "must be a native speaker" fails.

        The rule is language-agnostic because its trigger sentence often names no
        language at all — which is exactly why it must not fire on every use of
        the word "native".
        """
        self.assertEqual(
            hg.language_verdict("Data Analyst",
                                "Native English speaker preferred.")["verdict"],
            hg.PASS)
        got = hg.language_verdict("Data Analyst", "Must be a native speaker.")
        self.assertEqual(got["verdict"], hg.FAIL)
        self.assertIn("native-speaker", got["languages_required"])

    def test_the_conjunction_rule_is_anchored_on_adjacency(self):
        """"English and German" is a requirement. Two languages in one paragraph
        are not. The pattern requires them adjacent, optionally joined by one
        connective — co-occurrence anywhere in the sentence is not enough."""
        self.assertEqual(
            hg.language_verdict("Data Analyst",
                                "English and German.")["verdict"], hg.FAIL)
        self.assertEqual(
            hg.language_verdict(
                "Data Analyst",
                "You will report in English to stakeholders across our German "
                "and Nordic sites.")["verdict"], hg.PASS)


class TheCombinedBlockRunsFiveGates(unittest.TestCase):
    """`evaluate()` gained a fifth gate, and the consumers read it by name.

    `prerank_jobs.gate_reason` iterates ``verdict["failed"]`` and looks each name up
    in the block, so a gate that fails without appearing in both places renders no
    reason in the daily report.
    """

    AXES = {"domain_in_title": ["supply_chain"], "domain_from_description": [],
            "enabler_in_title": [], "enabler_from_description": []}

    def test_the_block_carries_all_five_gates(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description": "You will build Power BI dashboards "
                                          "for our supply chain teams."},
                          self.AXES)
        for gate in ("language", "experience", "sponsorship", "seniority",
                     "pure_technical"):
            with self.subTest(gate):
                self.assertIn(gate, got)
                self.assertIn("verdict", got[gate])

    def test_a_sponsorship_failure_is_named_in_the_failed_list(self):
        got = hg.evaluate({"title": "Business Analyst",
                           "description": "You will build Power BI dashboards "
                                          "for our supply chain teams. Please "
                                          "note there is no sponsorship "
                                          "available for this role."},
                          self.AXES)
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertIn("sponsorship", got["failed"])
        self.assertEqual(got["sponsorship"]["bar"], "sponsorship_denied")

    def test_every_named_failure_resolves_to_a_block_with_a_reason(self):
        """The contract `gate_reason` depends on, pinned against all five gates."""
        got = hg.evaluate({"title": "Senior Data Scientist",
                           "description": "Fluent German required. 8 years of "
                                          "experience in ML. We do not sponsor "
                                          "visas."},
                          {"domain_in_title": [], "domain_from_description": [],
                           "enabler_in_title": [], "enabler_from_description": []})
        self.assertEqual(got["overall"], hg.FAIL)
        self.assertTrue(got["failed"])
        for name in got["failed"]:
            with self.subTest(name):
                self.assertIn(name, got)
                self.assertTrue(got[name].get("reason"))


if __name__ == "__main__":
    unittest.main()
