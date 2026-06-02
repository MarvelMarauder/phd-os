"""
PhD OS build script.
Reads Obsidian vault data, writes JSON files, copies static site to _site/.
Runs from the repo root in GitHub Actions.
"""

import os
import re
import glob
import json
import shutil
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
import frontmatter

SITE_SRC = "site"
SITE_OUT = "_site"

# ── Discovery config ──────────────────────────────────────────────────────────

# ISSNs for the 11 top IS journals
IS_ISSNS = [
    "0167-9236",  # DSS
    "0960-085X",  # EJIS
    "0378-7206",  # I&M
    "1471-7727",  # I&O
    "1350-1917",  # ISJ
    "1047-7047",  # ISR
    "0268-3962",  # JIT
    "0742-1222",  # JMIS
    "1536-9323",  # JAIS
    "0963-8687",  # JSIS
    "0276-7783",  # MISQ
]

JOURNAL_ABBREVS = {
    "decision support systems": "DSS",
    "european journal of information systems": "EJIS",
    "information and management": "I&M",
    "information & management": "I&M",
    "information and organization": "I&O",
    "information & organization": "I&O",
    "information systems journal": "ISJ",
    "information systems research": "ISR",
    "journal of information technology": "JIT",
    "journal of management information systems": "JMIS",
    "journal of the association for information systems": "JAIS",
    "the journal of strategic information systems": "JSIS",
    "mis quarterly": "MISQ",
    "management information systems quarterly": "MISQ",
}

# Per-stream keyword searches (edit freely)
STREAM_SEARCHES = [
    {"stream": "ai-companions", "label": "AI Companions", "query": "AI companion"},
    {"stream": "judgy-ai",      "label": "JudgyAI",       "query": "algorithmic judgment"},
]

OA_FIELDS  = "display_name,doi,authorships,publication_date,primary_location,abstract_inverted_index,cited_by_count"
OA_MAILTO  = "mailto=2taylorbullock@gmail.com"
OA_BASE    = "https://api.openalex.org/works"

STAGES = [
    "ideation", "lit-search", "fleshing-out", "method",
    "data-collection", "data-analysis", "writeup", "under-review", "published"
]


# ── Papers ────────────────────────────────────────────────────────────────────

def build_papers():
    papers = []
    for filepath in sorted(glob.glob("100 Research/My Papers/*.md")):
        post = frontmatter.load(filepath)
        co_authors = post.get("co-authors", [])
        if isinstance(co_authors, str):
            co_authors = [a.strip() for a in co_authors.split(",") if a.strip()]
        papers.append({
            "title":           post.get("title") or os.path.splitext(os.path.basename(filepath))[0],
            "stream":          post.get("stream", ""),
            "stage":           post.get("stage", "ideation"),
            "co_authors":      co_authors,
            "target_journal":  post.get("target-journal", ""),
            "deadline":        str(post.get("deadline", "") or ""),
            "todoist_project": post.get("todoist-project", ""),
            "status":          post.get("status", "active"),
        })

    papers.sort(key=lambda p: (p["stream"], STAGES.index(p["stage"]) if p["stage"] in STAGES else 99))
    return {"papers": papers}


# ── Literature (read papers) ──────────────────────────────────────────────────

def build_lit():
    papers = []
    for filepath in sorted(glob.glob("100 Research/Source Papers/*.md")):
        if os.path.basename(filepath).startswith("_"):
            continue
        post = frontmatter.load(filepath)
        body = post.content.strip()
        snippet = body.split("\n\n")[0].strip() if body else ""
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "…"
        authors = post.get("authors", [])
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(",") if a.strip()]
        related = post.get("related-papers", [])
        if isinstance(related, str):
            related = [r.strip() for r in related.split(",") if r.strip()]
        keywords = post.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        papers.append({
            "title":          post.get("title") or os.path.splitext(os.path.basename(filepath))[0],
            "authors":        authors,
            "journal":        post.get("journal", ""),
            "year":           post.get("year") or None,
            "doi":            post.get("doi", ""),
            "stream":         post.get("stream", ""),
            "topic":          post.get("topic", ""),
            "keywords":       keywords,
            "related_papers": related,
            "thoughts":       post.get("thoughts", ""),
            "snippet":        snippet,
        })
    papers.sort(key=lambda p: (-(p["year"] or 0), p["title"]))
    return {"papers": papers}


# ── Books ─────────────────────────────────────────────────────────────────────

def _fetch_gb_cover(isbn, title, author):
    """Fetch cover from Google Books API.

    Strategy: ISBN lookup first (most accurate edition), then title+author
    with orderBy=newest so the most recent edition's cover wins.
    zoom=1 in the URL is the thumbnail (128px); replace with zoom=0 for the
    full-size original, which Google Books serves at ~500px.
    """
    def _get_cover_url(query, order=""):
        order_param = f"&orderBy={order}" if order else ""
        url = (f"https://www.googleapis.com/books/v1/volumes"
               f"?q={query}&maxResults=3{order_param}"
               f"&fields=items(volumeInfo(imageLinks,publishedDate))&printType=books")
        req = urllib.request.Request(url, headers={"User-Agent": "PhD-OS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        items = data.get("items", [])
        if not items:
            return ""
        # Among results, prefer the most recently published edition
        items_sorted = sorted(
            items,
            key=lambda x: x.get("volumeInfo", {}).get("publishedDate", "0"),
            reverse=True,
        )
        for item in items_sorted:
            links = item.get("volumeInfo", {}).get("imageLinks", {})
            for key in ("extraLarge", "large", "medium", "thumbnail", "smallThumbnail"):
                if links.get(key):
                    # zoom=0 returns the largest available image (~500px)
                    return (links[key]
                            .replace("http://", "https://")
                            .replace("zoom=1", "zoom=0")
                            .replace("zoom=5", "zoom=0"))
        return ""

    try:
        if isbn:
            url = _get_cover_url(f"isbn:{isbn}")
            if url:
                return url
        # Fall back to title+author, newest edition first
        q = urllib.parse.quote(f'intitle:"{title}" inauthor:"{author}"')
        return _get_cover_url(q, order="newest")
    except Exception:
        pass
    return ""


def _fetch_ol_cover(title, author):
    """Open Library fallback when Google Books has nothing."""
    try:
        q = urllib.parse.quote(f"{title} {author}".strip())
        url = f"https://openlibrary.org/search.json?q={q}&fields=cover_i&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "PhD-OS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        cover_i = ((data.get("docs") or [{}])[0]).get("cover_i")
        if cover_i:
            return f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
    except Exception:
        pass
    return ""


def _book_date(val):
    if not val or val is False:
        return ""
    if val is True:
        return "yes"   # started/finished but no date recorded
    return str(val).strip()


def build_books():
    books = []
    for filepath in sorted(glob.glob("400 Personal/Books/*.md")):
        if os.path.basename(filepath).startswith("_"):
            continue
        post = frontmatter.load(filepath)
        body = post.content.strip()
        snippet = body.split("\n\n")[0].strip() if body else ""
        if len(snippet) > 280:
            snippet = snippet[:280].rsplit(" ", 1)[0] + "…"
        tags = post.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        title  = post.get("title") or os.path.splitext(os.path.basename(filepath))[0]
        author = post.get("author", "")

        # Cover: use manually-set cover_url from frontmatter only.
        # Client-side JS (books.html / index.html) fetches covers dynamically
        # from Google Books API, always getting the newest edition.
        cover_url = str(post.get("cover_url", "") or "").strip()
        isbn      = str(post.get("isbn", "") or "").strip()

        books.append({
            "title":     title,
            "author":    author,
            "status":    post.get("status", "want-to-read"),
            "rating":    post.get("rating") or None,
            "started":   _book_date(post.get("started")),
            "finished":  _book_date(post.get("finished")),
            "tags":      tags,
            "snippet":   snippet,
            "isbn":      isbn,
            "cover_url": cover_url,
        })
    return {"books": books}


# ── Recharge ──────────────────────────────────────────────────────────────────

def build_recharge():
    path = "500 Recharge/Recharge List.md"
    sections = []
    if not os.path.exists(path):
        return {"sections": sections}

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Strip YAML frontmatter if present
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL).strip()

    current_title = None
    current_items = []

    for line in content.splitlines():
        heading = re.match(r"^#{1,3}\s+(.+)", line)
        item    = re.match(r"^[-*]\s+(.+)", line)

        if heading:
            if current_title is not None:
                sections.append({"title": current_title, "items": current_items})
            current_title = heading.group(1).strip()
            current_items = []
        elif item and current_title is not None:
            current_items.append(item.group(1).strip())

    if current_title is not None:
        sections.append({"title": current_title, "items": current_items})

    return {"sections": sections}


# ── Discovery (OpenAlex, server-side) ────────────────────────────────────────

def reconstruct_abstract(inv_index):
    if not inv_index:
        return ""
    pos_word = []
    for word, positions in inv_index.items():
        for p in positions:
            pos_word.append((p, word))
    pos_word.sort()
    return " ".join(w for _, w in pos_word)


def _journal_abbrev(source_name):
    return JOURNAL_ABBREVS.get((source_name or "").lower().strip(), "")


def _oa_paper(p):
    src     = (p.get("primary_location") or {}).get("source") or {}
    journal = src.get("display_name", "")
    doi_raw = p.get("doi") or ""
    doi     = doi_raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    authors = [
        a["author"]["display_name"]
        for a in (p.get("authorships") or [])
        if a.get("author", {}).get("display_name")
    ][:6]
    abstract_raw = reconstruct_abstract(p.get("abstract_inverted_index"))
    abstract = (abstract_raw[:400].rsplit(" ", 1)[0] + "…") if len(abstract_raw) > 400 else abstract_raw
    pub_date = p.get("publication_date", "")
    return {
        "title":          p.get("display_name", ""),
        "doi":            doi,
        "authors":        authors,
        "year":           int(pub_date[:4]) if pub_date else None,
        "pub_date":       pub_date,
        "journal":        journal,
        "journal_abbrev": _journal_abbrev(journal),
        "abstract":       abstract,
        "cited_by_count": p.get("cited_by_count", 0),
    }


def _oa_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PhD-OS/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _oa_journal_papers(sort, per_page, days_back, label):
    issn_filter = "|".join(IS_ISSNS)
    cutoff      = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = (f"{OA_BASE}?filter=primary_location.source.issn:{issn_filter}"
           f",from_publication_date:{cutoff}"
           f"&sort={sort}&per_page={per_page}"
           f"&select={OA_FIELDS}&{OA_MAILTO}")
    try:
        data   = _oa_get(url)
        papers = [_oa_paper(p) for p in data.get("results", []) if p.get("display_name")]
        print(f"  discover/{label}: {len(papers)} papers")
        return papers
    except Exception as e:
        print(f"  WARNING: OpenAlex fetch failed ({label}): {e}")
        return []


def _oa_stream_papers(query, label):
    url = (f"{OA_BASE}?search={urllib.parse.quote(query)}"
           f"&sort=publication_date:desc&per_page=5"
           f"&select={OA_FIELDS}&{OA_MAILTO}")
    try:
        data   = _oa_get(url)
        papers = [_oa_paper(p) for p in data.get("results", []) if p.get("display_name")]
        print(f"  discover/{label}: {len(papers)} papers")
        return papers
    except Exception as e:
        print(f"  WARNING: OpenAlex fetch failed ({label}): {e}")
        return []


def build_dismissed():
    path = "100 Research/Source Papers/_dismissed.md"
    if not os.path.exists(path):
        return []
    post = frontmatter.load(path)
    items = post.get("dismissed", []) or []
    if isinstance(items, str):
        items = [i.strip() for i in items.splitlines() if i.strip()]
    return [str(i).lower().strip() for i in items if i]


def build_discover():
    sections = []

    # Recent issues from IS journals (last 6 months, newest first)
    sections.append({
        "type":   "journals",
        "label":  "Recent Issues — IS Journals",
        "papers": _oa_journal_papers("publication_date:desc", 10, 180, "recent-issues"),
    })

    time.sleep(1)

    # Most-cited papers from IS journals in the last 12 months
    sections.append({
        "type":   "trending",
        "label":  "Trending This Year",
        "papers": _oa_journal_papers("cited_by_count:desc", 6, 365, "trending"),
    })

    # Per-stream keyword searches
    for s in STREAM_SEARCHES:
        time.sleep(1)
        sections.append({
            "type":   "stream",
            "stream": s["stream"],
            "label":  s["label"],
            "papers": _oa_stream_papers(s["query"], s["stream"]),
        })

    return {
        "sections":   sections,
        "dismissed":  build_dismissed(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Build ─────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(SITE_OUT, exist_ok=True)

    # Copy static site files
    if os.path.isdir(SITE_SRC):
        for item in os.listdir(SITE_SRC):
            src = os.path.join(SITE_SRC, item)
            dst = os.path.join(SITE_OUT, item)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
        print(f"Copied {SITE_SRC}/ → {SITE_OUT}/")
    else:
        print(f"WARNING: {SITE_SRC}/ not found — skipping static file copy")

    # Write papers.json
    papers_data = build_papers()
    with open(os.path.join(SITE_OUT, "papers.json"), "w", encoding="utf-8") as f:
        json.dump(papers_data, f, indent=2, ensure_ascii=False)
    print(f"papers.json: {len(papers_data['papers'])} papers")

    # Write discover.json
    print("Fetching discovery papers from OpenAlex…")
    discover_data = build_discover()
    with open(os.path.join(SITE_OUT, "discover.json"), "w", encoding="utf-8") as f:
        json.dump(discover_data, f, indent=2, ensure_ascii=False)

    # Write lit.json
    lit_data = build_lit()
    with open(os.path.join(SITE_OUT, "lit.json"), "w", encoding="utf-8") as f:
        json.dump(lit_data, f, indent=2, ensure_ascii=False)
    print(f"lit.json: {len(lit_data['papers'])} papers")

    # Write books.json
    books_data = build_books()
    with open(os.path.join(SITE_OUT, "books.json"), "w", encoding="utf-8") as f:
        json.dump(books_data, f, indent=2, ensure_ascii=False)
    print(f"books.json: {len(books_data['books'])} books")

    # Write recharge.json
    recharge_data = build_recharge()
    with open(os.path.join(SITE_OUT, "recharge.json"), "w", encoding="utf-8") as f:
        json.dump(recharge_data, f, indent=2, ensure_ascii=False)
    total_items = sum(len(s["items"]) for s in recharge_data["sections"])
    print(f"recharge.json: {len(recharge_data['sections'])} sections, {total_items} items")


if __name__ == "__main__":
    main()
