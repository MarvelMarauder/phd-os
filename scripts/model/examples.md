# Email Parser — Personal Examples

Add examples here to sharpen the model on your specific context.
Each example teaches the model what a *good* response looks like for your emails.
After adding examples, run `scripts/model/build_model.sh` to rebuild.

Format: `INPUT:` block followed by `OUTPUT:` block.

---

## Example 1 — Advisor email with deadline

INPUT:
FROM: advisor@university.edu
SUBJECT: RE: IRB protocol revision
BODY:
Hi Taylor, thanks for the draft. The IRB committee wants two changes before they'll
approve: (1) clarify the consent procedure for online participants, and (2) add a
data retention section. The resubmission window closes May 20. Let me know if you
have questions. - Prof. Smith

OUTPUT:
{
  "summary": "Advisor requests two specific IRB revisions due May 20 before resubmission window closes.",
  "todos": [
    {
      "task": "Revise IRB: clarify online consent procedure",
      "priority": 1,
      "project_hint": "research",
      "due_suggestion": "2026-05-18"
    },
    {
      "task": "Revise IRB: add data retention section",
      "priority": 1,
      "project_hint": "research",
      "due_suggestion": "2026-05-18"
    },
    {
      "task": "Resubmit revised IRB protocol",
      "priority": 1,
      "project_hint": "research",
      "due_suggestion": "2026-05-20"
    }
  ]
}

---

## Example 2 — Journal alert with papers

INPUT:
FROM: alerts@misq.org
SUBJECT: New MISQ Issue — Vol 50 No 2
BODY:
New papers available in MIS Quarterly:
- "Algorithmic Transparency and User Trust" by Chen et al.
- "AI-Mediated Communication in Organizations" by Park & Lee
- "Platform Governance in Digital Ecosystems" by Zhao et al.

OUTPUT:
{
  "summary": "New MISQ issue with three papers, two of which are relevant to Taylor's research streams.",
  "todos": [
    {
      "task": "Read: Algorithmic Transparency and User Trust (MISQ)",
      "priority": 3,
      "project_hint": "readings",
      "due_suggestion": "this week"
    },
    {
      "task": "Read: AI-Mediated Communication in Organizations (MISQ)",
      "priority": 3,
      "project_hint": "readings",
      "due_suggestion": "this week"
    }
  ]
}

---

## Example 3 — Admin / bureaucracy

INPUT:
FROM: grad-office@university.edu
SUBJECT: Annual Progress Report Due June 1
BODY:
Dear PhD students, please submit your annual progress report by June 1 using the
form at gradschool.university.edu/progress. Your advisor must co-sign by June 7.

OUTPUT:
{
  "summary": "Annual PhD progress report due June 1; advisor co-signature due June 7.",
  "todos": [
    {
      "task": "Submit annual PhD progress report (grad portal)",
      "priority": 2,
      "project_hint": "admin",
      "due_suggestion": "2026-06-01"
    },
    {
      "task": "Ask advisor to co-sign progress report",
      "priority": 2,
      "project_hint": "admin",
      "due_suggestion": "2026-06-01"
    }
  ]
}

---

## Example 4 — FYI only, no tasks

INPUT:
FROM: newsletter@aisnet.org
SUBJECT: AIS Monthly Update — May 2026
BODY:
Greetings from AIS! This month we're featuring profiles of emerging scholars,
a recap of ICIS 2025, and a spotlight on IS research in Southeast Asia...

OUTPUT:
{
  "summary": "AIS monthly newsletter — informational only, no action required.",
  "todos": []
}

---

<!-- Add your own examples below. The more specific to your actual emails, the better. -->
