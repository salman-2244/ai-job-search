#!/usr/bin/env python3
"""
Job ranking engine for Salman Ahmed's automated daily job search pipeline.
Usage: python3 scripts/rank_jobs.py [DATE]
  DATE: Optional date string in YYYY-MM-DD format. If not provided, uses today's date.
"""

import json
import csv
import re
import sys
from datetime import datetime

# ============================================================
# CONFIGURATION
# ============================================================

# Get date from command line or use today
if len(sys.argv) > 1:
    date_str = sys.argv[1]
else:
    date_str = datetime.now().strftime("%Y-%m-%d")

RANKSET_FILE = f'/tmp/jobsearch_rankset_{date_str}.json'
OUTPUT_FILE = f'/tmp/jobsearch_rank_output_{date_str}.json'
NOT_DRAFTED_FILE = f'/tmp/jobsearch_not_drafted_{date_str}.json'

print(f"Processing rankset for date: {date_str}")
print(f"Rankset file: {RANKSET_FILE}")

# ============================================================
# LOAD INPUT FILES
# ============================================================

# Load fetched jobs
try:
    with open(RANKSET_FILE, 'r') as f:
        rankset = json.load(f)
except FileNotFoundError:
    print(f"ERROR: Rankset file not found: {RANKSET_FILE}")
    sys.exit(1)

# Load seen jobs (read-only)
try:
    with open('job_scraper/seen_jobs.json', 'r') as f:
        seen_jobs = json.load(f)
except FileNotFoundError:
    seen_jobs = {"seen": {}}

# Load alert matched (read-only)
try:
    with open('job_scraper/alert_matched.json', 'r') as f:
        alert_matched = json.load(f)
except FileNotFoundError:
    alert_matched = {}

# Load tracker (read-only)
try:
    with open('job_search_tracker.csv', 'r') as f:
        reader = csv.DictReader(f)
        tracker_entries = list(reader)
        # Build set of (company, role) pairs for dedup
        tracker_set = {(row['company'].strip().lower(), row['role'].strip().lower()) for row in tracker_entries}
except FileNotFoundError:
    tracker_set = set()

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_description(job):
    """Get the best available description text."""
    if job.get('description'):
        return job['description']
    elif job.get('description_snippet'):
        return job['description_snippet']
    return None

def check_seniority_gate(title):
    """Check if title contains seniority markers."""
    seniority_markers = ['Senior', 'Sr.', 'Sr', 'Snr', 'Lead', 'Leader', 'Principal', 'Head', 'Director', 'Expert']
    title_lower = title.lower()
    for marker in seniority_markers:
        if marker == 'Sr.':
            if re.search(r'\bsr\.\b', title_lower):
                return False
        elif marker == 'Sr':
            if re.search(r'\bsr\b', title_lower):
                return False
        else:
            if re.search(r'\b' + marker.lower() + r'\b', title_lower):
                # Exception: "Lead" followed by certain words (e.g., "Lead time", "Lead to")
                if marker == 'Lead':
                    next_words = ['time', 'to', 'generation', 'gen', 'management', 'qualification', 'nurturing', 'scoring', 'conversion']
                    pattern = r'\blead\s+(' + '|'.join(next_words) + r')\b'
                    if re.search(pattern, title_lower):
                        continue
                return False
    return True

def check_eligibility_gate(job):
    """
    Check eligibility gate for Salman (non-EU national on Hungarian permit).
    Returns: ('PASS'|'FLAG'|'FAIL', gaps_list)
    """
    location = job.get('location', '')
    desc = get_description(job)
    gaps = []

    # Check if location is Hungary
    if 'Hungary' in location or 'Budapest' in location:
        if desc:
            desc_lower = desc.lower()
            citizenship_patterns = [
                r'\bmust be a (citizen|permanent resident|pr)\b',
                r'\b(citizenship|permanent residency|pr status) required\b',
                r'\bsecurity clearance\b',
                r'\bmust hold (hungarian|eu|european) (citizenship|passport)\b',
            ]
            for pattern in citizenship_patterns:
                if re.search(pattern, desc_lower):
                    match = re.search(pattern, desc, re.IGNORECASE)
                    if match:
                        gaps.append(f"Eligibility Gate FAIL: {match.group()}")
                    return ('FAIL', gaps)
        return ('PASS', gaps)

    eu_countries = ['Austria', 'Belgium', 'Bulgaria', 'Croatia', 'Cyprus', 'Czech Republic',
                   'Denmark', 'Estonia', 'Finland', 'France', 'Germany', 'Greece', 'Hungary',
                   'Ireland', 'Italy', 'Latvia', 'Lithuania', 'Luxembourg', 'Malta',
                   'Netherlands', 'Poland', 'Portugal', 'Romania', 'Slovakia', 'Slovenia',
                   'Spain', 'Sweden', 'Switzerland', 'UK', 'United Kingdom', 'Norway',
                   'Iceland', 'Liechtenstein']

    location_has_eu = any(country in location for country in eu_countries)

    # Check for remote roles with EU/worldwide scope
    if location and ('remote' in location.lower() or 'worldwide' in location.lower()):
        if any(x in location.lower() for x in ['eu', 'europe', 'worldwide', 'global']):
            return ('PASS', gaps)

    # Non-Hungarian EU/UK/CH location
    if location_has_eu and 'Hungary' not in location:
        if desc:
            desc_lower = desc.lower()
            # Check for explicit no-sponsorship statements
            no_sponsor_patterns = [
                r'\bno visa sponsorship\b',
                r'\bmust already have the right to work\b',
                r'\bnot eligible for sponsorship\b',
                r'\bno work permit support\b',
                r'\bno relocation support\b',
            ]
            for pattern in no_sponsor_patterns:
                if re.search(pattern, desc_lower):
                    match = re.search(pattern, desc, re.IGNORECASE)
                    if match:
                        gaps.append(f"Sponsorship: posting states '{match.group()}'")
                    return ('FLAG', gaps)

            # Check for citizenship requirements
            citizenship_patterns = [
                r'\bmust be a (citizen|permanent resident|pr)\b',
                r'\b(citizenship|permanent residency|pr status) required\b',
                r'\bsecurity clearance\b',
            ]
            for pattern in citizenship_patterns:
                if re.search(pattern, desc_lower):
                    match = re.search(pattern, desc, re.IGNORECASE)
                    if match:
                        gaps.append(f"Eligibility Gate FAIL: {match.group()}")
                    return ('FAIL', gaps)

        gaps.append("Non-Hungarian EU/UK/CH role: visa sponsorship likely required")
        return ('FLAG', gaps)

    # Outside Europe with no remote-EU option
    europe_mentions = ['Europe', 'EU', 'European'] + eu_countries
    if not any(e in location for e in europe_mentions):
        if 'remote' in location.lower():
            if any(x in location.lower() for x in ['us', 'usa', 'america', 'canada', 'australia', 'asia']):
                return ('FAIL', [f"Location FAIL: {location} is outside Europe with no remote-EU option"])
            return ('PASS', gaps)
        else:
            return ('FAIL', [f"Location FAIL: {location} is outside Europe with no remote-EU option"])

    return ('PASS', gaps)

def check_language_gate(job):
    """
    Check language gate.
    Returns: ('PASS'|'FLAG'|'FAIL', gaps_list)
    Salman's languages: English (professional), Urdu (native), Punjabi (native), Hungarian (A2 - elementary)
    """
    gaps = []
    desc = get_description(job)
    location = job.get('location', '')

    # If no description, check location
    if not desc:
        if 'Hungary' in location or 'Budapest' in location:
            return ('PASS', [])
        return ('PASS', ["No description text to verify language from"])

    desc_lower = desc.lower()

    # Check for hard Hungarian requirements (A2 is NOT working level)
    hungarian_patterns = [
        r'\b(fluent|professional|business level|native|mother tongue)\s+hungarian\b',
        r'\bhungarian\s+(fluent|professional|business level|native|required|mandatory)\b',
    ]

    for pattern in hungarian_patterns:
        if re.search(pattern, desc_lower):
            match = re.search(pattern, desc, re.IGNORECASE)
            if match:
                gaps.append(f"Language Gate FAIL: {match.group()}")
            return ('FAIL', gaps)

    # Check for other languages not in profile - only FAIL if REQUIRED
    # Use careful regex to avoid false positives (e.g., "French, Spanish or Italian is an advantage")
    non_profile_languages = ['German', 'French', 'Spanish', 'Italian', 'Dutch', 'Polish', 'Russian', 'Chinese', 'Arabic']

    for lang in non_profile_languages:
        required_patterns = [
            rf'\b(fluent|professional|business level|native|mother tongue)\s+{lang.lower()}\b',
            rf'\b{lang.lower()}\s+(required|mandatory|must have|must speak|essential)\b',
        ]
        for pattern in required_patterns:
            if re.search(pattern, desc_lower):
                match = re.search(pattern, desc, re.IGNORECASE)
                if match:
                    gaps.append(f"Language Gate FAIL: {match.group()}")
                return ('FAIL', gaps)

    # If Hungarian is mentioned with optional marker, it's PASS
    if 'hungarian' in desc_lower:
        optional_patterns = [
            r'\b(advantage|nice to have|preferred|ideally|a plus|an asset|welcome|beneficial)\b',
        ]
        for opt_pattern in optional_patterns:
            if re.search(opt_pattern, desc_lower):
                return ('PASS', gaps)

    # Check for English requirements above professional level
    english_high_patterns = [
        r'\bnative(-level)?\s+english\b',
        r'\bfluent\s+english\s+and\b',
    ]

    for pattern in english_high_patterns:
        if re.search(pattern, desc_lower):
            match = re.search(pattern, desc, re.IGNORECASE)
            if match:
                gaps.append(f"Language Gate FLAG: {match.group()} (candidate has professional working proficiency)")
            return ('FLAG', gaps)

    return ('PASS', gaps)

def score_technical(job):
    """Score Technical Skills (0-100)."""
    desc = get_description(job)
    if not desc:
        return 70

    desc_lower = desc.lower()

    strong_skills = [
        'python', 'pandas', 'keras', 'sql', 'data science', 'machine learning',
        'generative ai', 'llm', 'fine-tuning', 'data analytics', 'power bi',
        'dax', 'process automation', 'power automate', 'selenium',
        'azure ml studio', 'azure document intelligence', 'vba', 'power query'
    ]

    moderate_skills = [
        'ai product management', 'mlops', 'power apps', 'supply chain analytics',
        'procurement analytics', 'operations analytics', 'business intelligence',
        'financial modeling', 'automation'
    ]

    # Look for "required", "must have", "essential" patterns
    must_have_pattern = r'((?:required|must have|essential|mandatory)[^\n.]*?[\n.])'
    must_haves = re.findall(must_have_pattern, desc_lower, re.IGNORECASE)

    if not must_haves:
        strong_count = sum(1 for skill in strong_skills if skill in desc_lower)
        moderate_count = sum(1 for skill in moderate_skills if skill in desc_lower)
        total = strong_count + moderate_count * 0.5
        return min(100, int(total * 15))

    matched = 0
    total_must_haves = len(must_haves)

    for req in must_haves:
        req_clean = req.strip().lower()
        if any(skill in req_clean for skill in strong_skills):
            matched += 1
        elif any(skill in req_clean for skill in moderate_skills):
            matched += 0.5

    if total_must_haves == 0:
        return 70

    proportion = matched / total_must_haves
    return min(100, int(proportion * 100))

def score_experience(job):
    """Score Experience (0-100)."""
    desc = get_description(job)

    if desc:
        desc_lower = desc.lower()

        year_patterns = [
            (r'\b0-2 years?\b', 100),
            (r'\b(graduate|junior|early[- ]career|entry level)\b', 100),
            (r'\b2-4 years?\b', 85),
            (r'\b4-6 years?\b', 55),
            (r'\b6\+ years?\b', 25),
            (r'\b(senior|staff|principal|head of)\b', 25),
            (r'\b(internship|student|working[- ]student)\b', 30),
            (r'\bformal people management\b', 30),
            (r'\bline management\b', 30),
        ]

        for pattern, score in year_patterns:
            if re.search(pattern, desc_lower):
                # Domain fit adjustment
                domain_keywords = ['telecom', 'aviation', 'supply chain', 'process', 'performance',
                                 'financial', 'operations', 'procurement']
                domain_match = any(kw in desc_lower for kw in domain_keywords)
                if domain_match:
                    if score == 85:
                        return 85
                    elif score == 55:
                        return 55
                return score

    return 85

def score_behavioral(job):
    """Score Behavioral Fit (0-100)."""
    desc = get_description(job)

    if not desc:
        return 70

    desc_lower = desc.lower()

    positive_keywords = [
        'cross-functional', 'high autonomy', 'measurable business impact',
        'product shipping', 'end-to-end', 'ownership', 'stakeholder management',
        'collaboration', 'innovation', 'problem solving', 'decision making',
        'agile', 'continuous improvement', 'transformation'
    ]

    negative_keywords = [
        'maintenance', 'keep the lights on', 'rigid', 'low autonomy',
        'isolated', 'pure research', 'no product exposure'
    ]

    positive_count = sum(1 for kw in positive_keywords if kw in desc_lower)
    negative_count = sum(1 for kw in negative_keywords if kw in desc_lower)

    score = 70 + (positive_count * 5) - (negative_count * 10)

    return max(0, min(100, score))

def score_career(job):
    """Score Career Alignment (0-100) and return track."""
    title = job.get('title', '').lower()
    desc = get_description(job)
    desc_lower = desc.lower() if desc else ''

    tracks = {
        'T1': {'keywords': ['ai engineer', 'ml engineer', 'generative ai', 'llm', 'machine learning engineer', 'ai specialist', 'mlops']},
        'T2': {'keywords': ['data scientist', 'data analyst', 'bi developer', 'bi analyst', 'analytics engineer', 'power bi', 'business analyst']},
        'T3': {'keywords': ['ai product', 'ai solutions', 'ai strategy', 'intelligent automation', 'low-code ai', 'automation engineer']},
        'T4': {'keywords': ['supply chain', 'operations analyst', 'procurement', 'demand planning', 'logistics', 'inventory']},
        'T5': {'keywords': ['performance manager', 'performance analyst', 'process manager', 'process improvement', 'business excellence', 'digital transformation', 'operational excellence']},
    }

    matched_tracks = []

    for track_id, track_info in tracks.items():
        for kw in track_info['keywords']:
            if kw in title or kw in desc_lower:
                if track_id not in matched_tracks:
                    matched_tracks.append(track_id)

    # Also check domain keywords
    domain_t1 = ['ai', 'ml', 'generative', 'llm', 'neural', 'deep learning']
    domain_t2 = ['data', 'analytics', 'bi', 'business intelligence', 'dashboard', 'reporting']
    domain_t3 = ['automation', 'low-code', 'ai product', 'ai solutions', 'ai strategy']
    domain_t4 = ['supply chain', 'operations', 'procurement', 'logistics', 'inventory', 'demand planning']
    domain_t5 = ['performance', 'process', 'excellence', 'transformation', 'continuous improvement']

    if any(kw in title or kw in desc_lower for kw in domain_t1):
        if 'T1' not in matched_tracks: matched_tracks.append('T1')
    if any(kw in title or kw in desc_lower for kw in domain_t2):
        if 'T2' not in matched_tracks: matched_tracks.append('T2')
    if any(kw in title or kw in desc_lower for kw in domain_t3):
        if 'T3' not in matched_tracks: matched_tracks.append('T3')
    if any(kw in title or kw in desc_lower for kw in domain_t4):
        if 'T4' not in matched_tracks: matched_tracks.append('T4')
    if any(kw in title or kw in desc_lower for kw in domain_t5):
        if 'T5' not in matched_tracks: matched_tracks.append('T5')

    if len(matched_tracks) >= 2:
        return (100, '+'.join(sorted(matched_tracks)))
    elif len(matched_tracks) == 1:
        return (95, matched_tracks[0])
    else:
        adjacent_keywords = ['software engineer', 'devops', 'data engineer', 'backend', 'frontend']
        if any(kw in title or kw in desc_lower for kw in adjacent_keywords):
            return (50, 'none')
        else:
            return (30, 'none')

def is_alert_matched(job):
    """Check if job is in alert_matched.json."""
    dedup_key = job.get('dedup_key', '')

    if 'linkedin' in dedup_key:
        match = re.search(r'url:linkedin:(\d+)', dedup_key)
        if match:
            job_id = match.group(1)
            for key, value in alert_matched.items():
                if f'linkedin:{job_id}' in key or job_id in str(value):
                    return True

    url = job.get('url', '')
    for key in alert_matched:
        if url in str(alert_matched[key]):
            return True

    return False

# ============================================================
# MAIN PROCESSING
# ============================================================

print(f"Starting job ranking for {date_str}...")
print(f"Total jobs in rankset: {len(rankset['results'])}")
print(f"Already seen: {len(seen_jobs.get('seen', {}))}")
print(f"Tracker entries: {len(tracker_set)}")

# Step 1: Mark repeats. Nothing is dropped here any more.
#
# This used to skip two classes of job: keys in seen_jobs.json, and company+role pairs
# already in job_search_tracker.csv. Both skips were removed by instruction on
# 2026-09-06 — "Remove the two dedup filters (pre-rank and rank) so already-sent jobs
# are re-included, and add the '🔁 seen X runs ago' marker." A posting that still passes
# the filters is still worth applying to; whether to re-apply is Salman's call.
#
# The tracker skip goes with it for the same reason, and for a second one: it keyed on
# lowercased company+role, so "Data Analyst" at a company Salman applied to once
# suppressed every later "Data Analyst" opening there, including genuinely new ones.
run_ledger = sorted(
    {str(entry.get('rank_date') or '').strip()
     for entry in seen_jobs.get('seen', {}).values()
     if isinstance(entry, dict) and len(str(entry.get('rank_date') or '')) == 10},
    reverse=True,
)

surviving_jobs = []
repeat_count = 0
for job in rankset['results']:
    dedup_key = job.get('dedup_key', '')
    company = job.get('company', '')
    title = job.get('title', '')

    entry = seen_jobs.get('seen', {}).get(dedup_key)
    in_tracker = (company.lower(), title.lower()) in tracker_set

    if entry is not None or in_tracker:
        repeat_count += 1
        last_seen = (str(entry.get('rank_date') or '').strip()
                     if isinstance(entry, dict) else '')
        if last_seen:
            # Distinct ranking runs recorded after `last_seen`. There is no run counter
            # in the repo, only per-key `rank_date`, and runs became on-demand rather
            # than daily — so counting runs is honest where counting days would not be.
            runs_ago = sum(1 for d in run_ledger if d > last_seen)
            label = (f"🔁 seen {runs_ago} run{'s' if runs_ago != 1 else ''} ago"
                     if runs_ago else f"🔁 seen in the last run ({last_seen})")
        else:
            label = "🔁 already in the tracker" if in_tracker else "🔁 seen before"
            runs_ago = None
        job['repeat'] = {'runs_ago': runs_ago, 'last_seen': last_seen or None,
                         'in_tracker': in_tracker, 'label': label}
        print(f"REPEAT ({label}): {company} - {title}")

    surviving_jobs.append(job)

print(f"Jobs to rank: {len(surviving_jobs)} ({repeat_count} re-included repeats)")

# Step 2: Apply gates
jobs_after_gates = []
for job in surviving_jobs:
    title = job.get('title', '')

    # Seniority Gate
    if not check_seniority_gate(title):
        print(f"DROP (Seniority Gate): {job.get('dedup_key')} - {title}")
        continue

    # Eligibility Gate
    elig_verdict, elig_gaps = check_eligibility_gate(job)
    if elig_verdict == 'FAIL':
        print(f"DROP (Eligibility Gate FAIL): {job.get('dedup_key')} - {title}")
        continue

    # Language Gate
    lang_verdict, lang_gaps = check_language_gate(job)
    if lang_verdict == 'FAIL':
        print(f"DROP (Language Gate FAIL): {job.get('dedup_key')} - {title}")
        continue

    # Store gate results
    job['_elig_verdict'] = elig_verdict
    job['_elig_gaps'] = elig_gaps
    job['_lang_verdict'] = lang_verdict
    job['_lang_gaps'] = lang_gaps

    jobs_after_gates.append(job)

print(f"Jobs after gates: {len(jobs_after_gates)}")

# Step 3: Score each job
scored_jobs = []
for job in jobs_after_gates:
    desc = get_description(job)

    tech_score = score_technical(job)
    exp_score = score_experience(job)
    beh_score = score_behavioral(job)
    career_score, track = score_career(job)

    # Weighted overall score
    overall = (tech_score * 0.30) + (exp_score * 0.25) + (beh_score * 0.15) + (career_score * 0.30)

    # Determine verdict
    if overall >= 75:
        verdict = "Strong Fit"
    elif overall >= 60:
        verdict = "Good Fit"
    elif overall >= 45:
        verdict = "Moderate Fit"
    elif overall >= 30:
        verdict = "Weak Fit"
    else:
        verdict = "Poor Fit"

    alert_match = is_alert_matched(job)

    # Store scores
    job['_tech_score'] = tech_score
    job['_exp_score'] = exp_score
    job['_beh_score'] = beh_score
    job['_career_score'] = career_score
    job['_track'] = track
    job['_overall'] = round(overall, 1)
    job['_verdict'] = verdict
    job['_alert_matched'] = alert_match

    scored_jobs.append(job)
    print(f"SCORED: {job['dedup_key']} | {job['title']} | {job['company']} | Score: {overall:.1f} | Verdict: {verdict} | Track: {track}")

print(f"Jobs after scoring: {len(scored_jobs)}")

# Step 4: Apply document-generation gate
# Qualifies if: score >= 75 OR (score >= 60 AND alert_matched)
gate_qualifying = []
not_drafted = []

for job in scored_jobs:
    score = job['_overall']
    alert_match = job['_alert_matched']

    if score >= 75:
        job['_gate_reason'] = "score>=75"
        gate_qualifying.append(job)
    elif score >= 60 and alert_match:
        job['_gate_reason'] = "alert_matched+score>=60"
        gate_qualifying.append(job)
    else:
        # Score >= 60 but not alert matched -> not drafted
        if score >= 60:
            not_drafted.append(job)

print(f"Gate qualifying jobs: {len(gate_qualifying)}")
print(f"Not drafted (score >= 60 but not alert matched): {len(not_drafted)}")

# Step 5: Sort and limit to top 5
# Sort by: score (desc), alert_matched (desc), description length (desc)
gate_qualifying.sort(key=lambda x: (-x['_overall'], -int(x['_alert_matched']), -len(get_description(x) or '')),)
gate_qualifying = gate_qualifying[:5]

# Step 6: Build output
output = []
for job in gate_qualifying:
    desc = get_description(job)

    # Determine location gate
    location = job.get('location', '')
    if 'Hungary' in location or 'Budapest' in location:
        location_gate = "PASS"
    elif any(x in location for x in ['EU', 'Europe', 'worldwide', 'global', 'remote']):
        location_gate = "PASS"
    else:
        location_gate = "FLAG"

    language_gate = job.get('_lang_verdict', 'PASS')

    # Build strengths and gaps
    strengths = []
    gaps = []

    # Add eligibility and language gaps
    gaps.extend(job.get('_elig_gaps', []))
    gaps.extend(job.get('_lang_gaps', []))

    # Add technical strengths
    desc_lower = desc.lower() if desc else ''
    tech_keywords = ['python', 'sql', 'machine learning', 'ai', 'generative', 'llm', 'power bi', 'automation', 'azure']
    for kw in tech_keywords:
        if kw in desc_lower:
            strengths.append(f"{kw.title()} mentioned in posting")

    # Add experience strengths
    if job['_exp_score'] >= 85:
        strengths.append("Experience requirements within profile range")

    # Add career alignment
    if job['_career_score'] >= 90:
        strengths.append(f"Strong career alignment with track {job['_track']}")

    # Add gaps for low scores
    if job['_tech_score'] < 60:
        gaps.append("Technical skills gap")
    if job['_exp_score'] < 55:
        gaps.append("Experience gap")

    # Sponsorship note for FLAG locations
    if location_gate == "FLAG":
        gaps.append("Visa sponsorship likely required for non-Hungarian role")

    output.append({
        "key": job['dedup_key'],
        "title": job['title'],
        "company": job['company'],
        "url": job['url'],
        "location": job['location'],
        "portal": job['portal'],
        "track": job['_track'],
        "score": round(job['_overall'], 1),
        "verdict": job['_verdict'],
        "scores": {
            "technical": job['_tech_score'],
            "experience": job['_exp_score'],
            "behavioral": job['_beh_score'],
            "career": job['_career_score']
        },
        "alert_matched": job['_alert_matched'],
        "gate_reason": job['_gate_reason'],
        "strengths": strengths[:3],  # Limit to top 3
        "gaps": gaps[:3],  # Limit to top 3
        "location_gate": location_gate,
        "language_gate": language_gate,
        "posting_text": desc
    })

# Step 7: Build not-drafted list
not_drafted_output = []
for job in not_drafted:
    not_drafted_output.append({
        "key": job['dedup_key'],
        "title": job['title'],
        "company": job['company'],
        "url": job['url'],
        "location": job['location'],
        "portal": job['portal'],
        "track": job['_track'],
        "score": round(job['_overall'], 1),
        "verdict": job['_verdict']
    })

# ============================================================
# OUTPUT
# ============================================================

print("\n" + "="*80)
print("FINAL OUTPUT:")
print("="*80)
print(json.dumps(output, indent=2))

# Save outputs
with open(OUTPUT_FILE, 'w') as f:
    json.dump(output, f, indent=2)

with open(NOT_DRAFTED_FILE, 'w') as f:
    json.dump(not_drafted_output, f, indent=2)

print(f"\nDone!")
print(f"Qualifying jobs: {len(output)} written to {OUTPUT_FILE}")
print(f"Not drafted list: {len(not_drafted_output)} jobs written to {NOT_DRAFTED_FILE}")
