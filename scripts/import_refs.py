#!/usr/bin/env python3
"""
import_refs.py — Bulk-import a formatted reference list as Obsidian notes.

Paste an entire reference list (APA, Chicago, MLA, numbered, or plain) and
this script creates a .md note for each paper in 100 Research/Source Papers/.

For each reference it:
  1. Extracts the DOI if present (covers https://doi.org/…, doi:…, bare 10.x/…)
  2. Looks up full metadata via CrossRef (bibliographic) + OpenAlex (keywords)
  3. Falls back to OpenAlex title-search if no DOI
  4. Writes a .md note with YAML frontmatter (title, authors, journal, year,
     doi, stream, topic, keywords, thoughts, related-papers) + abstract body

Usage:
  python3 scripts/import_refs.py refs.txt
  python3 scripts/import_refs.py refs.txt --dry-run   # preview, no files written
  cat refs.txt | python3 scripts/import_refs.py

After running, push to main and GitHub Actions will pick up the new notes.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

VAULT_DIR  = Path(__file__).parent.parent
PAPERS_DIR = VAULT_DIR / "100 Research" / "Source Papers"
RATE_SECS  = 0.8   # pause between API calls — be polite


# ── HTTP helper ───────────────────────────────────────────────────────────────

def fetch_json(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PhD-OS/1.0 (mailto:2taylorbullock@gmail.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ── DOI extraction ────────────────────────────────────────────────────────────

def extract_doi(text):
    m = re.search(r'\b(10\.\d{4,}/[^\s"\'<>)\]]+)', text)
    if m:
        return m.group(1).rstrip(".,;")
    return None


# ── Reference parsing ─────────────────────────────────────────────────────────

def split_references(text):
    """Split a block of text into individual references."""
    text = text.strip()

    # Blank-line separated (most common for pasted lists)
    chunks = [c for c in re.split(r'\n{2,}', text) if c.strip()]
    if len(chunks) >= 3:
        return [c.replace("\n", " ").strip() for c in chunks]

    # Numbered: "1." / "[1]" at the start of a line
    chunks = [c for c in re.split(r'\n(?=\s*(?:\[\d+\]|\d+\.)\s)', text) if c.strip()]
    if len(chunks) >= 3:
        return [c.replace("\n", " ").strip() for c in chunks]

    # APA / Chicago line-wrapped: a new reference starts on any line that
    # begins with "LastName," AND contains "(YYYY" — join continuation lines.
    lines = [l.strip() for l in text.splitlines()]
    refs, current = [], []
    for line in lines:
        if not line:
            continue
        if re.match(r'^\d+\.?\s*$', line):   # stray page numbers from PDF copy-paste
            continue
        if re.match(r'^[A-ZÀ-ɏ][A-Za-zÀ-ɏ\'\-]+,\s+', line) and re.search(r'\(\d{4}', line):
            if current:
                refs.append(' '.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        refs.append(' '.join(current))
    if refs:
        return refs

    # One reference per line fallback
    return [line for line in lines if line]


def extract_title(ref):
    """Try to pull a title from a formatted reference string."""
    # Chicago / MLA: title in "double quotes" or "smart quotes"
    m = re.search(r'[“‘"]([^”’"]{10,220})[”’"]', ref)
    if m:
        return m.group(1).strip()

    # APA: after (YYYY[a-z]?). and before the next ". Capital"
    m = re.search(r'\(\d{4}[a-z]?\)\.\s+(.+?)(?:\.\s+[A-Z]|\.\s*$)', ref, re.DOTALL)
    if m:
        t = m.group(1).strip()
        if 10 < len(t) < 300:
            return t

    # Numbered reference: strip leading number, try first long segment
    ref_clean = re.sub(r'^\s*(?:\[\d+\]|\d+\.)\s*', '', ref)
    segments  = [s.strip() for s in re.split(r'\.\s+', ref_clean)]
    candidates = [s for s in segments if 15 < len(s) < 300]
    if candidates:
        return max(candidates, key=len)

    return None


# ── Metadata APIs ─────────────────────────────────────────────────────────────

def crossref_by_doi(doi):
    try:
        url  = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        data = fetch_json(url)
        m    = data.get("message", {})
        parts = (m.get("published") or m.get("published-print") or {}) \
                .get("date-parts", [[None]])[0]
        return {
            "title":    stripxml((m.get("title") or [""])[0]),
            "authors":  _cr_authors(m.get("author")),
            "journal":  (m.get("container-title") or [""])[0],
            "year":     str(parts[0]) if parts and parts[0] else "",
            "doi":      m.get("DOI") or doi,
            "abstract": stripxml(m.get("abstract", "")),
            "keywords": m.get("subject", []),  # journal-level; overridden by OpenAlex
        }
    except Exception:
        return None


def openalex_by_doi(doi):
    try:
        url  = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}?select=keywords,concepts,abstract_inverted_index,authorships,publication_year,primary_location,ids"
        data = fetch_json(url)
        return _oa_parse(data)
    except Exception:
        return None


def openalex_search(title):
    try:
        q    = urllib.parse.quote(title[:120])
        url  = f"https://api.openalex.org/works?filter=title.search:{q}&select=title,keywords,concepts,abstract_inverted_index,authorships,publication_year,primary_location,ids&per-page=1"
        data = fetch_json(url)
        results = data.get("results") or []
        if results:
            return _oa_parse(results[0])
    except Exception:
        pass
    return None


def _oa_parse(w):
    """Extract useful fields from an OpenAlex work object."""
    kws = [k.get("keyword") or k for k in (w.get("keywords") or []) if k]
    if not kws:
        # fall back to mid-level concepts (level 2–3, score > 0.3)
        kws = [
            c["display_name"]
            for c in sorted(w.get("concepts") or [], key=lambda c: -c.get("score", 0))
            if c.get("level", 0) >= 2 and c.get("score", 0) > 0.3
        ][:10]

    doi = ((w.get("ids") or {}).get("doi") or "").replace("https://doi.org/", "")

    # Reconstruct abstract from inverted index if present
    abstract = ""
    inv = w.get("abstract_inverted_index")
    if inv:
        try:
            pos_word = [(p, word) for word, positions in inv.items() for p in positions]
            abstract = " ".join(w for _, w in sorted(pos_word))[:1200]
        except Exception:
            pass

    year = w.get("publication_year")
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name", "")

    authors = []
    for a in (w.get("authorships") or []):
        name = (a.get("author") or {}).get("display_name", "")
        if name:
            parts = name.strip().split()
            authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name)

    return {
        "keywords": [str(k) for k in kws],
        "abstract": abstract,
        "doi":      doi,
        "year":     str(year) if year else "",
        "journal":  venue,
        "authors":  authors,
        "title":    "",  # don't override CrossRef title with OA title
    }


def _cr_authors(author_list):
    parts = []
    for a in (author_list or []):
        family = a.get("family", "")
        given  = a.get("given", "")
        if family and given:
            parts.append(f"{family}, {given}")
        elif family:
            parts.append(family)
    return parts


def stripxml(s):
    return re.sub(r'<[^>]+>', '', s or '').strip()


# ── Note building ─────────────────────────────────────────────────────────────

def build_note(meta):
    title    = (meta.get("title") or "Untitled").replace('"', '\\"')
    authors  = meta.get("authors") or []
    journal  = (meta.get("journal") or "").replace('"', '\\"')
    year     = meta.get("year") or ""
    doi      = meta.get("doi") or ""
    keywords = meta.get("keywords") or []
    abstract = meta.get("abstract") or ""

    auth_yaml = ("authors:\n" + "\n".join(f'  - "{a}"' for a in authors)) if authors else "authors: []"
    kw_yaml   = ("keywords:\n" + "\n".join(f'  - "{k}"' for k in keywords[:12])) if keywords else "keywords: []"

    lines = [
        "---",
        f'title: "{title}"',
        auth_yaml,
        f'journal: "{journal}"',
        f"year: {year or 'null'}",
        f"doi: {doi or 'null'}",
        "stream: null",
        'topic: ""',
        kw_yaml,
        'thoughts: ""',
        "related-papers: []",
        "---",
        "",
    ]
    if abstract:
        lines += ["## Abstract", "", abstract, ""]

    return "\n".join(lines)


def safe_filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:100] + ".md"


# ── Main processing ───────────────────────────────────────────────────────────

def process(text, dry_run=False):
    refs = split_references(text)
    print(f"Found {len(refs)} reference(s).\n")
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)

    created, skipped, failed = [], [], []

    for i, ref in enumerate(refs, 1):
        label = ref[:90] + ("…" if len(ref) > 90 else "")
        print(f"[{i}/{len(refs)}] {label}")

        doi  = extract_doi(ref)
        meta = {}

        if doi:
            print(f"  DOI: {doi}")
            cr = crossref_by_doi(doi)
            time.sleep(RATE_SECS)
            oa = openalex_by_doi(doi)
            time.sleep(RATE_SECS)
            if cr:
                meta = cr
                # Merge OpenAlex keywords/abstract on top of CrossRef bibliographic data
                if oa:
                    if oa.get("keywords"):
                        meta["keywords"] = oa["keywords"]
                    if oa.get("abstract") and not meta.get("abstract"):
                        meta["abstract"] = oa["abstract"]
                print(f"  → {meta['title'][:70]}")
            elif oa and oa.get("doi"):
                meta = oa
                print(f"  → (CrossRef miss, OpenAlex only)")
            else:
                print(f"  → Not found — skipping")
                failed.append(ref[:80])
                continue
        else:
            title = extract_title(ref)
            if not title:
                print(f"  Could not extract title — skipping")
                failed.append(ref[:80])
                continue
            print(f"  No DOI — searching: {title[:70]}")
            oa = openalex_search(title)
            time.sleep(RATE_SECS)
            if oa and (oa.get("keywords") or oa.get("doi")):
                # Try CrossRef for better bibliographic data if we got a DOI from OA
                if oa.get("doi"):
                    cr = crossref_by_doi(oa["doi"])
                    time.sleep(RATE_SECS)
                    if cr:
                        meta = cr
                        meta["keywords"] = oa.get("keywords") or cr.get("keywords", [])
                        if oa.get("abstract") and not meta.get("abstract"):
                            meta["abstract"] = oa["abstract"]
                    else:
                        meta = oa
                else:
                    meta = oa
                # Fill in title from parsed reference if OA didn't return one
                if not meta.get("title") or meta["title"] == "Untitled":
                    meta["title"] = title
                print(f"  → {meta.get('title', title)[:70]}")
            else:
                # Nothing found — create a minimal note from parsed text
                print(f"  Not found in APIs — creating stub note")
                meta = {"title": title, "authors": [], "journal": "", "year": "",
                        "doi": "", "keywords": [], "abstract": ""}

        if not meta.get("title"):
            meta["title"] = extract_title(ref) or "Untitled"

        fname = safe_filename(meta["title"])
        fpath = PAPERS_DIR / fname

        if fpath.exists():
            print(f"  Already exists: {fname}")
            skipped.append(meta["title"])
        else:
            note = build_note(meta)
            if not dry_run:
                fpath.write_text(note, encoding="utf-8")
            action = "Would create" if dry_run else "Created"
            print(f"  {action}: {fname}")
            created.append(meta["title"])

        print()

    print("─" * 50)
    print(f"{'[DRY RUN] ' if dry_run else ''}Created: {len(created)}  |  Already existed: {len(skipped)}  |  Failed: {len(failed)}")
    if failed:
        print("\nCould not process:")
        for f in failed:
            print(f"  • {f}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        path = Path(args[0])
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        text = path.read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        print("Paste your reference list below, then press Ctrl+D:\n")
        text = sys.stdin.read()

    if not text.strip():
        print("No references provided.")
        sys.exit(1)

    process(text, dry_run=dry_run)


if __name__ == "__main__":
    main()
