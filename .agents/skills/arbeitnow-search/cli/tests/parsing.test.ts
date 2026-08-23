import { describe, test, expect } from "bun:test";
import {
  cleanHtml,
  normalizeSlug,
  toResult,
  toDetail,
  isoFromEpoch,
  matchesQuery,
  matchesTags,
  matchesLocation,
  matchesRemote,
  withinJobAge,
  type ArbeitnowJob,
} from "../src/helpers";

function job(overrides: Partial<ArbeitnowJob> = {}): ArbeitnowJob {
  return {
    slug: "machine-learning-engineer-acme-berlin-233707",
    company_name: "Acme",
    title: "Machine Learning Engineer",
    description: "<p>Build ML pipelines.</p><ul><li>Python &amp; PyTorch</li></ul>",
    remote: true,
    url: "https://acme.com/jobs/1",
    tags: ["Machine Learning", "Python"],
    job_types: ["Full-time"],
    location: "Berlin",
    created_at: 1786516800, // 2026-08-... epoch seconds
    ...overrides,
  };
}

describe("toResult — reshape into the portal-skill contract", () => {
  test("maps slug -> id and created_at -> ISO date", () => {
    const r = toResult(job());
    expect(r.id).toBe("machine-learning-engineer-acme-berlin-233707");
    expect(r.date).toBe(isoFromEpoch(1786516800));
    expect(r.date).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  test("carries the required contract fields", () => {
    const r = toResult(job());
    expect(r).toMatchObject({
      title: "Machine Learning Engineer",
      company: "Acme",
      location: "Berlin",
      url: "https://acme.com/jobs/1",
      remote: true,
    });
    expect(r.tags).toEqual(["Machine Learning", "Python"]);
    expect(r.job_types).toEqual(["Full-time"]);
  });

  test("missing values are null, not omitted", () => {
    const r = toResult(job({ company_name: "", location: "", created_at: 0 }));
    expect(r.company).toBeNull();
    expect(r.location).toBeNull();
    expect(r.date).toBeNull();
  });

  test("falls back to a synthesized url when url is empty", () => {
    const r = toResult(job({ url: "" }));
    expect(r.url).toContain("machine-learning-engineer-acme-berlin-233707");
  });

  test("carries the cleaned description the list endpoint already sent", () => {
    // The regression: `toResult` omitted `description` while the API response
    // carried it, so every Arbeitnow job reached the ranking pipeline with an
    // empty body and could only be scored on its title — and `detail` spent a
    // second request re-fetching text the first response had already delivered.
    const r = toResult(job());
    expect(r.description).toBe("Build ML pipelines.\nPython & PyTorch");
  });

  test("an empty description is null, not the empty string", () => {
    // `null` is what the contract uses for a missing value everywhere else, and
    // the downstream scorer's `job.get("description") or ...` fallback reads both
    // the same way. Consistency, so no consumer has to special-case it.
    expect(toResult(job({ description: "" })).description).toBeNull();
  });
});

describe("toDetail — adds a cleaned description", () => {
  test("strips HTML and decodes entities", () => {
    const d = toDetail(job());
    expect(d.description).toBe("Build ML pipelines.\nPython & PyTorch");
  });

  test("agrees with toResult, so search and detail never disagree", () => {
    // They are the same shape now. A truncation or transform applied to only one
    // of them would make the same posting read differently depending on which
    // command fetched it.
    expect(toDetail(job())).toEqual(toResult(job()));
  });
});

describe("isoFromEpoch", () => {
  test("converts seconds to an ISO string", () => {
    expect(isoFromEpoch(0)).toBeNull(); // 0/negative treated as absent
    expect(isoFromEpoch(1786516800)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  });
  test("null/undefined -> null", () => {
    expect(isoFromEpoch(null)).toBeNull();
    expect(isoFromEpoch(undefined)).toBeNull();
  });
});

describe("cleanHtml", () => {
  test("preserves block breaks", () => {
    expect(cleanHtml("<p>One</p><p>Two</p>")).toBe("One\nTwo");
  });
  test("decodes double-encoded HTML (Arbeitnow's inconsistent shape)", () => {
    // Some Arbeitnow descriptions arrive as entity-encoded HTML, e.g.
    // "&lt;h2&gt;Role&lt;/h2&gt;&lt;p&gt;A &amp;amp; B&lt;/p&gt;".
    const encoded = "&lt;h2&gt;Role&lt;/h2&gt;&lt;p&gt;A &amp;amp; B&lt;/p&gt;";
    expect(cleanHtml(encoded)).toBe("Role\nA & B");
  });
  test("decodes hex numeric entities", () => {
    expect(cleanHtml("Caf&#xE9;")).toBe("Café");
  });
  test("returns null for empty input", () => {
    expect(cleanHtml("")).toBeNull();
    expect(cleanHtml(null)).toBeNull();
    expect(cleanHtml(undefined)).toBeNull();
  });
});

describe("normalizeSlug", () => {
  test("accepts a bare slug", () => {
    expect(normalizeSlug("software-engineer-berlin-233707")).toBe("software-engineer-berlin-233707");
  });
  test("extracts the slug from a /view/<slug> URL", () => {
    expect(normalizeSlug("https://www.arbeitnow.com/view/software-engineer-berlin-233707")).toBe(
      "software-engineer-berlin-233707",
    );
  });
  test("extracts the slug from a /jobs/<slug> URL", () => {
    expect(normalizeSlug("https://www.arbeitnow.com/jobs/software-engineer-berlin-233707")).toBe(
      "software-engineer-berlin-233707",
    );
  });
  test("rejects a non-slug string", () => {
    expect(normalizeSlug("not a slug!")).toBeNull();
    expect(normalizeSlug("")).toBeNull();
  });
});

describe("matchesQuery — client-side AND over the searchable blob", () => {
  test("empty query matches everything", () => {
    expect(matchesQuery(job(), undefined)).toBe(true);
    expect(matchesQuery(job(), "")).toBe(true);
  });
  test("all terms must appear (AND semantics)", () => {
    expect(matchesQuery(job(), "machine learning")).toBe(true);
    expect(matchesQuery(job(), "machine kubernetes")).toBe(false);
  });
  test("matches against the description and tags too", () => {
    expect(matchesQuery(job(), "pytorch")).toBe(true); // from description
    expect(matchesQuery(job(), "python")).toBe(true); // from tags
  });
  test("is case-insensitive", () => {
    expect(matchesQuery(job(), "MACHINE Learning")).toBe(true);
  });
});

describe("matchesTags / matchesLocation / matchesRemote", () => {
  test("tags OR within the facet, case-insensitive", () => {
    expect(matchesTags(job(), [])).toBe(true);
    expect(matchesTags(job(), ["python"])).toBe(true);
    expect(matchesTags(job(), ["rust", "full-time"])).toBe(true); // job_types counted
    expect(matchesTags(job(), ["rust", "go"])).toBe(false);
  });
  test("location is a case-insensitive substring", () => {
    expect(matchesLocation(job(), undefined)).toBe(true);
    expect(matchesLocation(job(), "berlin")).toBe(true);
    expect(matchesLocation(job(), "munich")).toBe(false);
  });
  test("remote/onsite filter over the boolean; no hybrid", () => {
    expect(matchesRemote(job({ remote: true }), "remote")).toBe(true);
    expect(matchesRemote(job({ remote: true }), "onsite")).toBe(false);
    expect(matchesRemote(job({ remote: false }), "onsite")).toBe(true);
    expect(matchesRemote(job(), undefined)).toBe(true);
  });
});

describe("withinJobAge", () => {
  const now = 1_800_000_000_000; // fixed epoch ms
  const nowSec = now / 1000;
  test("unset sentinel (9999) matches everything", () => {
    expect(withinJobAge(job({ created_at: 1 }), 9999, now)).toBe(true);
  });
  test("keeps jobs newer than the window, drops older ones", () => {
    const fresh = job({ created_at: nowSec - 2 * 86400 }); // 2 days old
    const stale = job({ created_at: nowSec - 40 * 86400 }); // 40 days old
    expect(withinJobAge(fresh, 14, now)).toBe(true);
    expect(withinJobAge(stale, 14, now)).toBe(false);
  });
  test("missing timestamp is not filtered out", () => {
    expect(withinJobAge(job({ created_at: 0 }), 14, now)).toBe(true);
  });
});
