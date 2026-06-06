#!/usr/bin/env python3
"""
ProcureRadar — Pipeline Intelligence
Runs weekly (Monday morning). Scrapes agency lookahead pages,
checks for pipeline-to-active conversions, detects date conflicts,
and sends a weekly pipeline digest email.
"""

import os
import re
import json
import hashlib
import smtplib
import time
import subprocess
import requests
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import anthropic

# ── File paths ─────────────────────────────────────────────────────────────────

PIPELINE_FILE      = Path("data/pipeline.json")
PIPELINE_SEEN_FILE = Path("data/pipeline_seen.json")
RFPS_FILE          = Path("data/rfps.json")  # active RFPs from daily radar

# ── Source hierarchy (lower number = higher confidence) ───────────────────────

SOURCE_RANK = {
    "active_portal": 1,   # RFP is live on procurement portal
    "lookahead":     2,   # Agency published future procurement list
    "board_action":  3,   # Board voted to authorize procurement
    "external_intel":4,   # IMS or third-party intelligence
    "budget_cip":    5,   # Budget/CIP allocation only
}

# ── Lookahead portals to scrape ────────────────────────────────────────────────

LOOKAHEAD_SOURCES = [
    {
        "agency_id":  "octa",
        "agency_name":"Orange County Transportation Authority",
        "url":        "https://cammnet.octa.net/procurements/future/",
        "type":       "lookahead",
        "label":      "OCTA CAMMNet future procurements",
    },
]

# ── Category mapping for pipeline opportunities ───────────────────────────────

OPPORTUNITY_TYPE_COLORS = {
    "PM/PMC":                   {"bg": "#EEEDFE", "text": "#26215C"},
    "CM":                       {"bg": "#E1F5EE", "text": "#085041"},
    "Owner's Advisory":         {"bg": "#FAEEDA", "text": "#633806"},
    "Design/PS&E":              {"bg": "#E6F1FB", "text": "#0C447C"},
    "Capital Delivery Watchlist":{"bg": "#EAF3DE","text": "#27500A"},
    "Needs Review":             {"bg": "#F1EFE8", "text": "#5F5E5A"},
    "Planning/Programming":     {"bg": "#FCE7F3", "text": "#831843"},
}

CONFIDENCE_COLORS = {
    "confirmed": {"bg": "#EAF3DE", "text": "#27500A", "border": "#3B6D11", "label": "Confirmed"},
    "likely":    {"bg": "#FAEEDA", "text": "#633806", "border": "#BA7517", "label": "Likely"},
    "watch":     {"bg": "#F1EFE8", "text": "#5F5E5A", "border": "#888780", "label": "Watch"},
}

SYSTEM_PROMPT = """You are an RFP procurement researcher for Accenture's AEC consulting practice.
You are reviewing a list of upcoming procurement opportunities scraped from an agency's
future procurement lookahead page.

For each opportunity, return structured data in JSON format. Return ONLY valid JSON — no markdown,
no backticks, no explanation.

Format:
{
  "opportunities": [
    {
      "title": "Full procurement title",
      "summary": "1-2 sentence description of the scope",
      "expected_date": "Month YYYY, or Not specified",
      "estimated_value": 000000,
      "opportunity_type": "CM|Advisory|Planning|Design|PM/PMC|Other",
      "relevant": true
    }
  ]
}

Only include opportunities relevant to AEC professional services:
program management, construction management, advisory, planning, zero emission infrastructure,
owner's representative, project controls, grant management, asset management, data analytics.

Return {"opportunities": []} if nothing relevant is found."""


# ── Pipeline data helpers ──────────────────────────────────────────────────────

def load_pipeline() -> list:
    if PIPELINE_FILE.exists():
        return json.loads(PIPELINE_FILE.read_text())
    return []


def save_pipeline(pipeline: list) -> None:
    PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_FILE.write_text(json.dumps(pipeline, indent=2))


def load_pipeline_seen() -> set:
    if PIPELINE_SEEN_FILE.exists():
        return set(json.loads(PIPELINE_SEEN_FILE.read_text()))
    return set()


def save_pipeline_seen(seen: set) -> None:
    PIPELINE_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PIPELINE_SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def load_active_rfps() -> list:
    if RFPS_FILE.exists():
        return json.loads(RFPS_FILE.read_text())
    return []


def make_pipeline_id(agency_id: str, title: str) -> str:
    clean = re.sub(r"[^\w\s]", "", title.lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    return hashlib.sha256((agency_id + clean).encode()).hexdigest()[:24]


# ── Source conflict detection ──────────────────────────────────────────────────

def resolve_date_conflict(sources: list) -> tuple[str, bool, str]:
    """
    Given a list of source dicts, return:
    - winning_date: the date to use
    - has_conflict: whether sources disagree on date
    - winning_source: type of the winning source
    """
    dated = [s for s in sources if s.get("estimated_date")]
    if not dated:
        return None, False, sources[0]["type"] if sources else "unknown"

    dated.sort(key=lambda s: SOURCE_RANK.get(s["type"], 99))
    winner = dated[0]

    dates = list({s["estimated_date"] for s in dated})
    has_conflict = len(dates) > 1

    return winner["estimated_date"], has_conflict, winner["type"]


def get_confidence_tier(opportunity: dict) -> str:
    """Map opportunity status to confidence tier for email display."""
    status = opportunity.get("status", "budget_signal")
    if status == "active_rfp":
        return "confirmed"
    winning_source = opportunity.get("winning_source", "budget_cip")
    rank = SOURCE_RANK.get(winning_source, 5)
    if rank <= 2:
        return "confirmed"
    elif rank <= 3:
        return "likely"
    else:
        return "watch"


# ── OCTA CAMMNet scraper ───────────────────────────────────────────────────────

def scrape_octa_lookahead(client: anthropic.Anthropic) -> list[dict]:
    """
    Scrape OCTA CAMMNet future procurements page using Claude web search,
    then parse the results into structured opportunity dicts.
    """
    source = LOOKAHEAD_SOURCES[0]
    print(f"  Scraping {source['label']}...")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Go to {source['url']} and extract all upcoming/future procurement "
                    f"opportunities listed on the page. For each one, extract the title, "
                    f"estimated release date, estimated value, and a brief description. "
                    f"Filter for opportunities relevant to AEC professional services consulting."
                )
            }]
        )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        if not text.strip():
            print("  No response from Claude")
            return []

        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]

        parsed = json.loads(clean.strip())
        opps = parsed.get("opportunities", [])

        results = []
        today = date.today().isoformat()
        for opp in opps:
            if not opp.get("relevant", True):
                continue
            opp_id = make_pipeline_id(source["agency_id"], opp.get("title", ""))
            results.append({
                "id":               opp_id,
                "agency":           source["agency_name"],
                "agency_id":        source["agency_id"],
                "title":            opp.get("title", "Untitled"),
                "summary":          opp.get("summary", ""),
                "opportunity_type": opp.get("opportunity_type", "Other"),
                "pursuit_tier":     "Unscored",
                "score":            0,
                "status":           "future_planned",
                "expected_rfp_date":opp.get("expected_date"),
                "estimated_value":  opp.get("estimated_value", 0),
                "contact":          "",
                "incumbent":        "Unknown",
                "why_it_matters":   "",
                "next_action":      "Monitor procurement portal until solicitation is issued.",
                "due_date":         None,
                "solicitation_number": None,
                "sources": [{
                    "type":           source["type"],
                    "label":          source["label"],
                    "url":            source["url"],
                    "scraped":        today,
                    "estimated_date": opp.get("expected_date"),
                    "rank":           SOURCE_RANK[source["type"]],
                }],
                "winning_source":   source["type"],
                "date_conflict":    False,
                "added_date":       today,
                "last_checked":     today,
            })

        print(f"  Found {len(results)} relevant opportunities on CAMMNet lookahead")
        return results

    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"  Error scraping lookahead: {e}")
        return []


# ── Conversion detection ───────────────────────────────────────────────────────

def check_conversions(pipeline: list, active_rfps: list) -> list[dict]:
    """
    Compare pipeline titles against active RFPs to detect conversions.
    Returns list of converted opportunity dicts with matched RFP info.
    """
    conversions = []

    def normalize(s):
        return re.sub(r"[^\w\s]", "", (s or "").lower()).strip()

    for opp in pipeline:
        if opp.get("status") == "active_rfp":
            continue
        opp_norm = normalize(opp["title"])
        for rfp in active_rfps:
            rfp_norm = normalize(rfp.get("title", ""))
            # Simple word overlap score
            opp_words = set(opp_norm.split())
            rfp_words = set(rfp_norm.split())
            if len(opp_words) == 0:
                continue
            overlap = len(opp_words & rfp_words) / len(opp_words)
            if overlap >= 0.6:
                conversions.append({
                    "pipeline_id":    opp["id"],
                    "pipeline_title": opp["title"],
                    "agency":         opp["agency"],
                    "rfp_title":      rfp.get("title"),
                    "rfp_number":     rfp.get("rfp_number"),
                    "rfp_deadline":   rfp.get("deadline"),
                    "rfp_url":        rfp.get("url", "#"),
                    "added_date":     opp.get("added_date", ""),
                    "overlap_score":  round(overlap, 2),
                })
                break

    return conversions


# ── Merge new lookahead items into pipeline ────────────────────────────────────

def merge_pipeline(existing: list, new_items: list, seen: set) -> tuple[list, list]:
    """
    Merge newly scraped items into existing pipeline.
    Returns updated pipeline and list of genuinely new items.
    """
    existing_ids = {o["id"] for o in existing}
    newly_added = []

    for item in new_items:
        if item["id"] not in existing_ids and item["id"] not in seen:
            existing.append(item)
            seen.add(item["id"])
            newly_added.append(item)
            print(f"    + NEW pipeline item: {item['title']}")
        else:
            # Update last_checked on existing item
            for opp in existing:
                if opp["id"] == item["id"]:
                    opp["last_checked"] = date.today().isoformat()
                    # Check for date conflicts with new source data
                    new_source = item["sources"][0]
                    existing_types = {s["type"] for s in opp.get("sources", [])}
                    if new_source["type"] not in existing_types:
                        opp["sources"].append(new_source)
                        winning_date, has_conflict, winning_source = resolve_date_conflict(opp["sources"])
                        opp["date_conflict"]    = has_conflict
                        opp["winning_source"]   = winning_source
                        if winning_date:
                            opp["expected_rfp_date"] = winning_date
                    break

    return existing, newly_added


# ── Email builder ──────────────────────────────────────────────────────────────

def _fmt_value(v) -> str:
    if not v:
        return "Value TBD"
    try:
        return f"${int(v):,}"
    except:
        return str(v)


def build_conversion_banner(conversions: list) -> str:
    if not conversions:
        return ""
    items_html = "".join(f"""
      <tr style="border-bottom:1px solid #d4edbc;">
        <td style="padding:8px 14px;font-size:12px;color:#27500A;font-weight:500;">{c['agency']}</td>
        <td style="padding:8px 14px;font-size:12px;color:#27500A;">{c['pipeline_title']}</td>
        <td style="padding:8px 14px;font-size:12px;color:#27500A;white-space:nowrap;">
          {f"Due: {c['rfp_deadline']}" if c.get('rfp_deadline') and c['rfp_deadline'] != 'Not specified' else 'No deadline listed'}
        </td>
        <td style="padding:8px 14px;font-size:12px;">
          <a href="{c['rfp_url']}" style="color:#185FA5;text-decoration:none;">View RFP →</a>
        </td>
      </tr>""" for c in conversions)

    return f"""
    <div style="margin:18px 20px 0;background:#EAF3DE;border:1px solid #97C459;border-radius:8px;overflow:hidden;">
      <div style="padding:10px 14px;background:#C0DD97;border-bottom:1px solid #97C459;
                  font-size:12px;font-weight:600;color:#173404;display:flex;align-items:center;gap:8px;">
        &#8594; {len(conversions)} Pipeline opportunit{'y' if len(conversions)==1 else 'ies'} converted to active RFP
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#d4edbc;">
            <th style="padding:6px 14px;font-size:10px;color:#27500A;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Agency</th>
            <th style="padding:6px 14px;font-size:10px;color:#27500A;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Opportunity</th>
            <th style="padding:6px 14px;font-size:10px;color:#27500A;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Deadline</th>
            <th style="padding:6px 14px;font-size:10px;color:#27500A;text-align:left;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">Link</th>
          </tr>
        </thead>
        <tbody>{items_html}</tbody>
      </table>
    </div>"""


def build_opp_row(opp: dict) -> str:
    tier      = get_confidence_tier(opp)
    colors    = CONFIDENCE_COLORS[tier]
    opp_type  = opp.get("opportunity_type", "Other")
    type_col  = OPPORTUNITY_TYPE_COLORS.get(opp_type, {"bg": "#F1EFE8", "text": "#5F5E5A"})
    date_str  = opp.get("expected_rfp_date") or "No date yet"
    value_str = _fmt_value(opp.get("estimated_value"))
    conflict  = opp.get("date_conflict", False)

    conflict_html = ""
    if conflict:
        sources = opp.get("sources", [])
        dated   = [s for s in sources if s.get("estimated_date")]
        dated.sort(key=lambda s: SOURCE_RANK.get(s["type"], 99))
        conflict_html = f"""
        <div style="background:#FAEEDA;border-top:1px solid #FAC775;padding:7px 14px;
                    font-size:11px;color:#633806;display:flex;gap:6px;align-items:flex-start;">
          <span style="font-weight:600;">&#9888; Date conflict</span>
          <span>·</span>
          <span>{"  vs  ".join(f"{s['label']}: {s['estimated_date']}" for s in dated)} · Using {dated[0]['label'] if dated else 'best available'}</span>
        </div>"""

    source = opp.get("sources", [{}])[0]
    source_label = source.get("label", "Unknown source")
    source_url   = source.get("url", "#")
    source_date  = source.get("scraped", "")

    incumbent = opp.get("incumbent", "Unknown")
    contact   = opp.get("contact", "")
    next_action = opp.get("next_action", "")

    return f"""
    <div style="margin:0 16px 10px;border:1px solid #e2e0d8;
                border-left:3px solid {colors['border']};border-radius:0 8px 8px 0;
                overflow:hidden;background:#fff;">
      {conflict_html}
      <div style="padding:12px 14px;">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:6px;">
          <div style="display:flex;gap:5px;flex-wrap:wrap;">
            <span style="font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;
                         background:{colors['bg']};color:{colors['text']};">{colors['label']}</span>
            <span style="font-size:10px;font-weight:500;padding:2px 8px;border-radius:4px;
                         background:{type_col['bg']};color:{type_col['text']};">{opp_type}</span>
          </div>
          <span style="font-size:11px;color:#888;white-space:nowrap;">{opp.get('agency','')}</span>
        </div>
        <div style="font-size:13px;font-weight:600;color:#1a1a1a;line-height:1.4;margin-bottom:5px;">
          {opp.get('title','Untitled')}
        </div>
        {f'<div style="font-size:12px;color:#555;line-height:1.6;margin-bottom:8px;">{opp.get("summary","")}</div>' if opp.get("summary") else ''}
        <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:11px;color:#666;">
          <span>&#128197; {date_str}</span>
          <span>&#36; {value_str}</span>
          <span style="color:#888;">Source: <a href="{source_url}" style="color:#185FA5;text-decoration:none;">{source_label}</a> · {source_date}</span>
        </div>
      </div>
      <div style="background:#f8f8f4;border-top:1px solid #e8e8e4;padding:7px 14px;
                  font-size:11px;color:#666;">
        {f'<span>&#128100; {contact}</span> · ' if contact else ''}
        <span>Incumbent: {incumbent}</span>
        {f' · <span style="color:#444;">{next_action}</span>' if next_action else ''}
      </div>
    </div>"""


def build_pipeline_email(
    pipeline:    list,
    conversions: list,
    new_items:   list,
    run_date:    str,
) -> str:

    confirmed = [o for o in pipeline if get_confidence_tier(o) == "confirmed" and o.get("status") != "active_rfp"]
    likely    = [o for o in pipeline if get_confidence_tier(o) == "likely"]
    watch     = [o for o in pipeline if get_confidence_tier(o) == "watch"]
    conflicts  = [o for o in pipeline if o.get("date_conflict")]

    def section(title, color, icon, items):
        if not items:
            return ""
        rows = "".join(build_opp_row(o) for o in items)
        return f"""
        <div style="padding:14px 16px 6px;font-size:11px;font-weight:600;color:{color};
                    text-transform:uppercase;letter-spacing:0.06em;border-top:1px solid #e8e8e4;
                    display:flex;align-items:center;gap:6px;">
          <span>{icon}</span> {title}
        </div>
        {rows}"""

    body = (
        section("Confirmed — agency published lookahead", "#27500A", "&#10003;", confirmed) +
        section("Likely — board approved funding", "#854F0B",         "&#9679;",  likely) +
        section("Watch — budget signal only",      "#5F5E5A",         "&#9675;",  watch)
    )

    conflicts_note = ""
    if conflicts:
        conflicts_note = f"""
        <div style="margin:16px 20px 0;background:#FAEEDA;border:1px solid #FAC775;
                    border-radius:8px;padding:10px 14px;font-size:12px;color:#633806;">
          <span style="font-weight:600;">&#9888; {len(conflicts)} opportunit{'y' if len(conflicts)==1 else 'ies'} with conflicting source dates</span>
          — shown above with amber conflict banner. Verify with agency before committing capture resources.
        </div>"""

    new_note = ""
    if new_items:
        titles = "".join(f"<li style='margin-bottom:3px;'>{i['agency']} · {i['title']}</li>" for i in new_items[:5])
        new_note = f"""
        <div style="margin:16px 20px 0;background:#E6F1FB;border:1px solid #85B7EB;
                    border-radius:8px;padding:10px 14px;font-size:12px;color:#0C447C;">
          <span style="font-weight:600;">+ {len(new_items)} new item{'s' if len(new_items)!=1 else ''} added to pipeline this week</span>
          <ul style="margin:6px 0 0 16px;padding:0;color:#185FA5;">{titles}</ul>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:720px;margin:32px auto;background:#fff;border-radius:10px;
              overflow:hidden;border:1px solid #e0e0d8;">
    <div style="background:#1a1a1a;padding:24px 28px;">
      <h1 style="color:#fff;margin:0;font-size:18px;font-weight:500;letter-spacing:-0.01em;">
        ProcureRadar — Pipeline Intelligence
      </h1>
      <p style="color:#aaa;margin:6px 0 0;font-size:13px;">Weekly digest · {run_date}</p>
    </div>
    <div style="padding:16px 28px;background:#f8f8f4;border-bottom:1px solid #e8e8e4;
                display:flex;gap:28px;flex-wrap:wrap;">
      <div>
        <div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Confirmed</div>
        <div style="font-size:24px;font-weight:600;color:#3B6D11;">{len(confirmed)}</div>
      </div>
      <div>
        <div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Likely</div>
        <div style="font-size:24px;font-weight:600;color:#BA7517;">{len(likely)}</div>
      </div>
      <div>
        <div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Watch</div>
        <div style="font-size:24px;font-weight:600;color:#888780;">{len(watch)}</div>
      </div>
      <div>
        <div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Converted to RFP</div>
        <div style="font-size:24px;font-weight:600;color:#185FA5;">{len(conversions)}</div>
      </div>
      <div>
        <div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Total tracked</div>
        <div style="font-size:24px;font-weight:600;color:#1a1a1a;">{len(pipeline)}</div>
      </div>
    </div>
    {build_conversion_banner(conversions)}
    {new_note}
    {conflicts_note}
    {body}
    <div style="padding:16px 28px;border-top:1px solid #e8e8e4;font-size:11px;color:#aaa;line-height:1.7;">
      Monitoring: OCTA · LA Metro · Foothill Transit · Long Beach Transit · Riverside Transit Agency<br>
      Sources: CAMMNet future procurements · FY2027 capital budgets · Board meeting agendas<br>
      Next refresh: Monday, {run_date}
    </div>
  </div>
</body>
</html>"""


# ── Email sender ───────────────────────────────────────────────────────────────

def send_email(html: str, conversions: int, run_date: str) -> None:
    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port     = int(os.environ.get("SMTP_PORT", "587").strip())
    smtp_user     = os.environ["SMTP_USER"].strip()
    smtp_password = os.environ["SMTP_PASSWORD"].strip()
    to_addresses  = [a.strip() for a in os.environ["DIGEST_TO_EMAIL"].split(",")]

    subject = f"ProcureRadar Pipeline — {run_date}"
    if conversions:
        subject = f"&#8594; {conversions} converted · " + subject

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(to_addresses)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_addresses, msg.as_string())

    print(f"Pipeline digest sent to {', '.join(to_addresses)}")


# ── Git push ───────────────────────────────────────────────────────────────────

def git_push() -> None:
    try:
        subprocess.run(["git", "config", "user.name",  "rfp-radar[bot]"],                          check=True)
        subprocess.run(["git", "config", "user.email", "rfp-radar[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(PIPELINE_FILE), str(PIPELINE_SEEN_FILE)], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if result.returncode == 0:
            print("No pipeline changes — skipping push.")
            return
        subprocess.run(["git", "commit", "-m", "Update pipeline.json [skip ci]"], check=True)
        subprocess.run(["git", "pull",   "--rebase", "origin", "main"],           check=True)
        subprocess.run(["git", "push",   "origin",   "main"],                     check=True)
        print("pipeline.json pushed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Git push warning (non-fatal): {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    client   = anthropic.Anthropic(api_key=api_key)
    run_date = datetime.now().strftime("%A, %B %-d, %Y")
    print(f"\n=== ProcureRadar Pipeline Intelligence — {run_date} ===\n")

    pipeline     = load_pipeline()
    seen         = load_pipeline_seen()
    active_rfps  = load_active_rfps()

    print(f"Pipeline opportunities loaded: {len(pipeline)}")
    print(f"Active RFPs loaded: {len(active_rfps)}\n")

    # 1. Check for pipeline → active RFP conversions
    print("Checking for pipeline conversions...")
    conversions = check_conversions(pipeline, active_rfps)
    for c in conversions:
        print(f"  ✓ CONVERTED: {c['pipeline_title']} → {c['rfp_title']}")
        for opp in pipeline:
            if opp["id"] == c["pipeline_id"]:
                opp["status"] = "active_rfp"
                opp["last_checked"] = date.today().isoformat()

    print(f"  {len(conversions)} conversion(s) found\n")

    # 2. Scrape OCTA CAMMNet lookahead
    print("Scraping lookahead sources...")
    new_scraped = scrape_octa_lookahead(client)
    pipeline, new_items = merge_pipeline(pipeline, new_scraped, seen)
    print(f"  {len(new_items)} new pipeline item(s) added\n")

    # 3. Save and push
    save_pipeline(pipeline)
    save_pipeline_seen(seen)
    git_push()

    # 4. Build and send email
    html = build_pipeline_email(pipeline, conversions, new_items, run_date)
    send_email(html, len(conversions), run_date)

    print("\nDone.")


if __name__ == "__main__":
    main()
