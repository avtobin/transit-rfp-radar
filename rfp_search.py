#!/usr/bin/env python3
"""
Transit RFP Radar
Searches 13 transit agency procurement portals daily,
deduplicates against seen RFPs, and sends an email digest.
"""

import os
import json
import hashlib
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import anthropic

# ── Configuration ────────────────────────────────────────────────────────────

AGENCIES = [
    {"id": "lametro",   "name": "LA Metro",                   "portal": "vendor.metro.net"},
    {"id": "octa",      "name": "OCTA",                       "portal": "octa.net/procurement"},
    {"id": "lbt",       "name": "Long Beach Transit",         "portal": "ridelbt.com"},
    {"id": "rta",       "name": "Riverside Transit Agency",   "portal": "riversidetransit.com"},
    {"id": "sdmts",     "name": "San Diego MTS",              "portal": "sdmts.com"},
    {"id": "omnitrans", "name": "Omnitrans",                  "portal": "omnitrans.org"},
    {"id": "nctd",      "name": "North County Transit District", "portal": "gonctd.com"},
    {"id": "kcmetro",   "name": "King County Metro",          "portal": "metro.kingcounty.gov"},
    {"id": "trimet",    "name": "TriMet",                     "portal": "trimet.org"},
    {"id": "nymta",     "name": "New York MTA",               "portal": "mta.info"},
    {"id": "mbta",      "name": "Boston MBTA",                "portal": "mbta.com"},
    {"id": "njtransit", "name": "NJ Transit",                 "portal": "njtransit.com"},
    {"id": "ladot",     "name": "LADOT",                      "portal": "ladot.lacity.org"},
]

KEYWORDS = (
    "program management OR project management OR construction management OR "
    "advisory OR consulting OR zero emission OR ZEB OR battery electric OR "
    "electrification OR EV charging OR EVSE OR microgrid OR renewable energy OR "
    "energy storage OR owner's representative OR project controls"
)

CATEGORY_LABELS = {
    "pm":    "Program / Project Mgmt",
    "cm":    "Construction Mgmt",
    "adv":   "Advisory & Consulting",
    "zev":   "Zero Emissions / EV",
    "micro": "Microgrid / Energy",
}

CATEGORY_COLORS = {
    "pm":    {"bg": "#EEEDFE", "text": "#26215C"},
    "cm":    {"bg": "#E1F5EE", "text": "#085041"},
    "adv":   {"bg": "#FAEEDA", "text": "#633806"},
    "zev":   {"bg": "#EAF3DE", "text": "#27500A"},
    "micro": {"bg": "#FAECE7", "text": "#4A1B0C"},
}

SEEN_FILE = Path("data/seen_rfps.json")

SYSTEM_PROMPT = """You are an RFP procurement researcher for a consulting firm that pursues
program management, construction management, advisory, zero-emissions, and microgrid work
with transit agencies.

Search for active or recently issued RFPs (2024–2025) from the given transit agency.

Return ONLY valid JSON — no markdown, no backticks, no explanation. Format:
{
  "rfps": [
    {
      "title": "Full RFP title",
      "summary": "2-3 sentence description of the scope of work",
      "deadline": "deadline date as Month DD YYYY, or Not specified",
      "issue_date": "issue date as Month DD YYYY, or Not specified",
      "category": "pm|cm|adv|zev|micro",
      "rfp_number": "RFP/IFB/RFQ number, or Not specified",
      "url": "direct URL to RFP posting or procurement page"
    }
  ]
}

Category codes:
  pm    = program management, project management, project controls, PMO
  cm    = construction management, CM at-risk, owner's representative, inspector of record
  adv   = advisory, consulting, strategic planning, technical assistance
  zev   = zero emission vehicles, battery electric buses, electrification, EV charging, EVSE
  micro = microgrid, energy storage, solar, renewable energy, distributed energy resources

Return {"rfps": []} if no matching RFPs are found. Maximum 5 results."""


# ── Deduplication helpers ─────────────────────────────────────────────────────

def make_rfp_id(agency_name: str, title: str) -> str:
    raw = (agency_name + title).lower().replace(" ", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


# ── Anthropic API call ────────────────────────────────────────────────────────

def search_agency(client: anthropic.Anthropic, agency: dict) -> list[dict]:
    """Search one agency for matching RFPs using Claude with web search."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Search for active RFPs from {agency['name']} "
                    f"(procurement portal: {agency['portal']}) "
                    f"matching these keywords: {KEYWORDS}"
                )
            }]
        )

        # Extract text blocks from response (web search returns mixed content blocks)
        text = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        )

        if not text.strip():
            print(f"  [{agency['name']}] No text response returned")
            return []

        # Strip any accidental markdown fences
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]

        parsed = json.loads(clean.strip())
        rfps = parsed.get("rfps", [])

        # Attach agency metadata and generate stable IDs
        for rfp in rfps:
            rfp["agency"] = agency["name"]
            rfp["agency_id"] = agency["id"]
            rfp["_id"] = make_rfp_id(agency["name"], rfp.get("title", ""))
            rfp["_found_date"] = datetime.now().strftime("%Y-%m-%d")

        print(f"  [{agency['name']}] {len(rfps)} RFP(s) found")
        return rfps

    except json.JSONDecodeError as e:
        print(f"  [{agency['name']}] JSON parse error: {e}")
        return []
    except Exception as e:
        print(f"  [{agency['name']}] Error: {e}")
        return []


# ── Email builder ─────────────────────────────────────────────────────────────

def build_rfp_row(rfp: dict) -> str:
    cat = rfp.get("category", "pm")
    colors = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["pm"])
    cat_label = CATEGORY_LABELS.get(cat, cat)
    deadline = rfp.get("deadline", "Not specified")
    rfp_num = rfp.get("rfp_number", "Not specified")
    url = rfp.get("url", "#")

    deadline_color = "#993C1D" if deadline != "Not specified" else "#888"

    return f"""
    <tr>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;width:150px;">
        <span style="display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;
                     font-weight:600;background:{colors['bg']};color:{colors['text']};">
          {cat_label}
        </span>
        <div style="margin-top:6px;font-size:12px;color:#555;font-weight:500;">
          {rfp.get('agency', '')}
        </div>
      </td>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;">
        <div style="font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4;">
          {rfp.get('title', 'Untitled RFP')}
        </div>
        <div style="margin-top:6px;font-size:13px;color:#555;line-height:1.6;">
          {rfp.get('summary', '')}
        </div>
      </td>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;
                 white-space:nowrap;font-size:12px;color:#555;min-width:120px;">
        {f'<div style="color:#888;">#{rfp_num}</div>' if rfp_num != "Not specified" else ""}
        <div style="margin-top:4px;color:{deadline_color};font-weight:{'500' if deadline != 'Not specified' else '400'};">
          {"Due: " + deadline if deadline != "Not specified" else "No deadline listed"}
        </div>
        <div style="margin-top:8px;">
          <a href="{url}" style="color:#185FA5;font-size:12px;text-decoration:none;">
            View RFP →
          </a>
        </div>
      </td>
    </tr>"""


def build_email_html(new_rfps: list[dict], run_date: str, agencies_searched: int) -> str:
    count = len(new_rfps)
    rows_html = "".join(build_rfp_row(r) for r in new_rfps) if new_rfps else ""

    if new_rfps:
        body_content = f"""
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:#fafafa;">
              <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;
                         text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid #e8e8e4;">
                Category / Agency
              </td>
              <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;
                         text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid #e8e8e4;">
                RFP
              </td>
              <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;
                         text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid #e8e8e4;">
                Details
              </td>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>"""
    else:
        body_content = """
        <div style="padding:48px 28px;text-align:center;color:#888;">
          <div style="font-size:32px;margin-bottom:12px;">✓</div>
          <div style="font-size:15px;font-weight:500;color:#555;">All caught up</div>
          <div style="font-size:13px;margin-top:6px;">No new RFPs found across all agencies today.</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:720px;margin:32px auto;background:#fff;border-radius:10px;
              overflow:hidden;border:1px solid #e0e0d8;">

    <!-- Header -->
    <div style="background:#1a1a1a;padding:24px 28px;">
      <h1 style="color:#fff;margin:0;font-size:18px;font-weight:500;letter-spacing:-0.01em;">
        🚌 Transit RFP Radar
      </h1>
      <p style="color:#aaa;margin:6px 0 0;font-size:13px;">{run_date}</p>
    </div>

    <!-- Stats bar -->
    <div style="padding:18px 28px;background:#f8f8f4;border-bottom:1px solid #e8e8e4;
                display:flex;gap:32px;">
      <div>
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">
          New RFPs
        </div>
        <div style="font-size:28px;font-weight:600;color:{'#185FA5' if count > 0 else '#1a1a1a'};">
          {count}
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">
          Agencies searched
        </div>
        <div style="font-size:28px;font-weight:600;color:#1a1a1a;">{agencies_searched}</div>
      </div>
    </div>

    <!-- RFP table or empty state -->
    {body_content}

    <!-- Footer -->
    <div style="padding:16px 28px;border-top:1px solid #e8e8e4;font-size:11px;color:#aaa;
                line-height:1.6;">
      Monitoring: LA Metro · OCTA · Long Beach Transit · Riverside Transit Agency ·
      San Diego MTS · Omnitrans · NCTD · King County Metro · TriMet ·
      NY MTA · MBTA · NJ Transit · LADOT
      <br>Categories: Program Mgmt · Construction Mgmt · Advisory · Zero Emissions · Microgrid/Energy
    </div>
  </div>
</body>
</html>"""


# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(html: str, new_count: int, run_date: str) -> None:
    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port     = int(os.environ.get("SMTP_PORT", "587").strip())
    smtp_user     = os.environ["SMTP_USER"].strip()
    smtp_password = os.environ["SMTP_PASSWORD"].strip()
    to_address    = os.environ["DIGEST_TO_EMAIL"].strip()

    subject = f"Transit RFP Digest - {run_date} ({new_count} new)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = smtp_user
    msg["To"]      = to_address
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_address, msg.as_string())

    print(f"Email sent to {to_address}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    client = anthropic.Anthropic(api_key=api_key)
    seen   = load_seen()

    run_date = datetime.now().strftime("%A, %B %-d, %Y")
    print(f"\n=== Transit RFP Radar — {run_date} ===\n")
    print(f"Seen RFPs in store: {len(seen)}\n")

    new_rfps = []

    for i, agency in enumerate(AGENCIES):
        print(f"[{i+1}/{len(AGENCIES)}] Searching {agency['name']}...")
        rfps = search_agency(client, agency)

        for rfp in rfps:
            if rfp["_id"] not in seen:
                new_rfps.append(rfp)
                seen.add(rfp["_id"])
                print(f"    ✓ NEW: {rfp.get('title', 'Untitled')}")
            else:
                print(f"    – Already seen: {rfp.get('title', 'Untitled')}")

        # Be polite to the API between agencies
        if i < len(AGENCIES) - 1:
            time.sleep(2)

    print(f"\n{len(new_rfps)} new RFP(s) found across {len(AGENCIES)} agencies")

    # Save updated seen list
    save_seen(seen)
    print(f"Seen store updated ({len(seen)} total)")

    # Build and send email
    html = build_email_html(new_rfps, run_date, len(AGENCIES))
    send_email(html, len(new_rfps), run_date)

    print("\nDone.")


if __name__ == "__main__":
    main()
