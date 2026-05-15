import os
import glob
import frontmatter
import json
from datetime import datetime

# Read all my paper notes
papers = []
for filepath in glob.glob("100 Research/My Papers/*.md"):
    post = frontmatter.load(filepath)
    papers.append({
        "title": post.get("title", os.path.basename(filepath)),
        "stream": post.get("stream", ""),
        "stage": post.get("stage", ""),
        "co_authors": post.get("co-authors", ""),
        "deadline": str(post.get("deadline", "")),
        "status": post.get("status", "active")
    })

# Sort by stream then title
papers.sort(key=lambda x: (x["stream"], x["title"]))

# Stage order for visual pipeline
stage_order = [
    "ideation", "lit-search", "fleshing-out", "method",
    "data-collection", "data-analysis", "writeup", "under-review", "published"
]

stage_colors = {
    "ideation": "#E6F1FB",
    "lit-search": "#E1F5EE", 
    "fleshing-out": "#EAF3DE",
    "method": "#FAEEDA",
    "data-collection": "#EEEDFE",
    "data-analysis": "#FAECE7",
    "writeup": "#FFF3CD",
    "under-review": "#F8D7DA",
    "published": "#D4EDDA"
}

# Build HTML
papers_html = ""
for p in papers:
    if p["status"] != "active":
        continue
    color = stage_colors.get(p["stage"], "#f5f5f5")
    deadline_str = f"<span class='deadline'>Due: {p['deadline']}</span>" if p["deadline"] and p["deadline"] != "None" else ""
    papers_html += f"""
    <div class='paper-card'>
        <div class='paper-title'>{p['title']}</div>
        <div class='paper-meta'>
            <span class='stream'>{p['stream']}</span>
            <span class='stage' style='background:{color}'>{p['stage']}</span>
            {deadline_str}
        </div>
        <div class='authors'>{p['co_authors']}</div>
    </div>
    """

html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
<title>PhD OS Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9f9f9; color: #1a1a1a; padding: 24px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
  .date {{ font-size: 13px; color: #888; margin-bottom: 24px; }}
  h2 {{ font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; color: #888; margin-bottom: 12px; }}
  .section {{ margin-bottom: 32px; }}
  .paper-card {{ background: white; border: 0.5px solid #e0e0e0; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; }}
  .paper-title {{ font-size: 14px; font-weight: 500; margin-bottom: 6px; }}
  .paper-meta {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }}
  .stream {{ font-size: 11px; background: #eeedfe; color: #3c3489; padding: 2px 7px; border-radius: 4px; font-weight: 500; }}
  .stage {{ font-size: 11px; padding: 2px 7px; border-radius: 4px; font-weight: 500; }}
  .deadline {{ font-size: 11px; color: #888; padding: 2px 0; }}
  .authors {{ font-size: 12px; color: #888; }}
  .updated {{ font-size: 11px; color: #bbb; margin-top: 40px; }}
</style>
</head>
<body>
<h1>PhD OS</h1>
<div class='date'>{datetime.now().strftime('%A, %B %d %Y')}</div>

<div class='section'>
  <h2>Active Papers</h2>
  {papers_html}
</div>

<div class='section'>
  <h2>Tasks</h2>
  <p style='font-size:13px;color:#888;'>Todoist integration coming in Project 7.</p>
</div>

<p class='updated'>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>
</body>
</html>
"""

os.makedirs("_site", exist_ok=True)
with open("_site/index.html", "w") as f:
    f.write(html)

print(f"Built dashboard with {len(papers)} papers")
