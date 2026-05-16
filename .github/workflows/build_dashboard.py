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
from datetime import datetime, timezone
import frontmatter

SITE_SRC = "site"
SITE_OUT = "_site"

# ── Discovery queries (edit these to tune your paper feed) ────────────────────
DISCOVER_QUERIES = [
    {
        "stream": "ai-companions",
        "label":  "AI Companions",
        "query":  "(AI companion) OR (social robot) OR (human-AI relationship)",
    },
    {
        "stream": "judgy-ai",
        "label":  "JudgyAI",
        "query":  "(AI judgment) OR (AI evaluation)",
    },
]

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
        papers.append({
            "title":          post.get("title") or os.path.splitext(os.path.basename(filepath))[0],
            "authors":        authors,
            "journal":        post.get("journal", ""),
            "year":           post.get("year") or None,
            "doi":            post.get("doi", ""),
            "stream":         post.get("stream", ""),
            "related_papers": related,
            "thoughts":       post.get("thoughts", ""),
            "snippet":        snippet,
        })
    papers.sort(key=lambda p: (-(p["year"] or 0), p["title"]))
    return {"papers": papers}


# ── Books ─────────────────────────────────────────────────────────────────────

def build_books():
    books = []
    for filepath in sorted(glob.glob("400 Personal/Books/*.md")):
        if os.path.basename(filepath).startswith("_"):
            continue  # skip templates
        post = frontmatter.load(filepath)
        body = post.content.strip()
        # First paragraph as snippet, capped at 280 chars
        snippet = body.split("\n\n")[0].strip() if body else ""
        if len(snippet) > 280:
            snippet = snippet[:280].rsplit(" ", 1)[0] + "…"
        tags = post.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        books.append({
            "title":    post.get("title") or os.path.splitext(os.path.basename(filepath))[0],
            "author":   post.get("author", ""),
            "status":   post.get("status", "want-to-read"),
            "rating":   post.get("rating") or None,
            "started":  str(post.get("started", "") or ""),
            "finished": str(post.get("finished", "") or ""),
            "tags":     tags,
            "snippet":  snippet,
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


# ── Discovery (Semantic Scholar, server-side to avoid CORS) ──────────────────

def build_discover():
    SS = "https://api.semanticscholar.org/graph/v1/paper/search"
    FIELDS = "title,authors,year,abstract,venue,externalIds,publicationDate"
    streams = []
    for i, q in enumerate(DISCOVER_QUERIES):
        if i > 0:
            time.sleep(1.5)  # stay well within 100 req/5 min
        url = (f"{SS}?query={urllib.parse.quote(q['query'])}"
               f"&fields={FIELDS}&sort=publicationDate:desc&limit=8")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PhD-OS/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            papers = [p for p in data.get("data", []) if p.get("title") and p.get("year")]
            print(f"  discover/{q['stream']}: {len(papers)} papers")
        except Exception as e:
            print(f"  WARNING: discover fetch failed for {q['stream']}: {e}")
            papers = []
        streams.append({"stream": q["stream"], "label": q["label"], "papers": papers})
    return {
        "streams": streams,
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
    print("Fetching discovery papers from Semantic Scholar…")
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
