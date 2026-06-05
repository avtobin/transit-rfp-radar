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
import subprocess
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import anthropic

# ── Configuration ────────────────────────────────────────────────────────────

AGENCIES = [
    # ── Southern California ───────────────────────────────────────────────────
    {"id": "octa",      "name": "OCTA",                              "portal": "https://cammnet.octa.net/procurements/"},
    {"id": "lbt",       "name": "Long Beach Transit",                "portal": "https://vendors.planetbids.com/portal/28908/bo/bo-search"},
    {"id": "rta",       "name": "Riverside Transit Agency",          "portal": "https://vendors.planetbids.com/portal/55483/bo/bo-search"},
    {"id": "sdmts",     "name": "San Diego MTS",                     "portal": "https://vendors.planetbids.com/portal/14771/bo/bo-search"},
    {"id": "omnitrans", "name": "Omnitrans",                         "portal": "https://vendors.planetbids.com/portal/18046/bo/bo-search"},
    {"id": "nctd",      "name": "North County Transit District",     "portal": "https://vendors.planetbids.com/portal/20134/bo/bo-search"},
    {"id": "foothill",  "name": "Foothill Transit",                  "portal": "https://vendors.planetbids.com/portal/29905/bo/bo-search"},
    {"id": "sunline",   "name": "SunLine Transit",                   "portal": "https://vendors.planetbids.com/portal/56419/bo/bo-search"},
    {"id": "sbcta",     "name": "San Bernardino CTA",                "portal": "https://www.gosbcta.com/doing-business/bids-rfps/"},
    {"id": "gctd",      "name": "Gold Coast Transit",                "portal": "https://www.gctd.org/contact/doing-business/"},
    {"id": "metrolink", "name": "Metrolink",                         "portal": "https://metrolinktrains.com/about/doing-business-with-metrolink/procurement-opportunities/"},
    {"id": "ladot",     "name": "LADOT",                             "portal": "https://www.rampla.org/s/"},
    # ── Northern California ───────────────────────────────────────────────────
    {"id": "bart",      "name": "BART",                              "portal": "https://suppliers.bart.gov/psp/BRFPV91/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL?active=P"},
    {"id": "vta",       "name": "VTA (Santa Clara)",                 "portal": "https://procurement.opengov.com/portal/vta?departmentId=all&status=open&page=1&limit=50&sortField=proposalDeadline&sortDirection=DESC"},
    {"id": "actransit", "name": "AC Transit",                        "portal": "https://actransit.bonfirehub.com/portal/?tab=openOpportunities"},
    # ── Pacific Northwest ─────────────────────────────────────────────────────
    {"id": "kcmetro",   "name": "King County Metro",                 "portal": "https://fa-epvh-saasfaprod1.fa.ocs.oraclecloud.com/fscmUI/faces/NegotiationAbstracts?prcBuId=300000001727151"},
    {"id": "trimet",    "name": "TriMet",                            "portal": "https://bidlocker.us/a/trimet/BidLocker"},
    {"id": "soundtransit", "name": "Sound Transit",                  "portal": "https://www.biddingo.com/soundtransit"},
    {"id": "commtransit",  "name": "Community Transit",              "portal": "https://commtrans.procureware.com/Bids"},
    # ── Mountain / Southwest ──────────────────────────────────────────────────
    {"id": "rtd",       "name": "Denver RTD",                        "portal": "https://procurement.opengov.com/portal/rtd-denver?departmentId=all&status=open"},
    # ── Texas ─────────────────────────────────────────────────────────────────
    {"id": "houston",   "name": "Houston Metro",                     "portal": "https://www.ridemetro.org/about/business-to-business/procurement-opportunities"},
    # ── Mid-Atlantic / Northeast ──────────────────────────────────────────────
    {"id": "wmata",     "name": "WMATA",                             "portal": "https://supplier.wmata.com/psp/supplier_1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL"},
    {"id": "septa",     "name": "SEPTA",                             "portal": "https://wwww.septa.org/procurement/bids/"},
    {"id": "prt",       "name": "Pittsburgh PRT",                    "portal": "https://www.rideprt.org/business-center/procurement/bids-and-rfps/"},
    {"id": "nymta_cd",  "name": "NY MTA — Construction & Development", "portal": "https://www.mta.info/agency/construction-and-development/contracting/current-opportunities"},
    {"id": "nymta_gen", "name": "NY MTA — General Procurement",      "portal": "https://www.mta.info/doing-business-with-us/procurement/current-opportunities"},
    {"id": "nymta_hq",  "name": "NY MTA — Headquarters",             "portal": "https://www.mta.info/doing-business-with-us/procurement/mta-headquarters"},
    {"id": "nymta_nyct","name": "NY MTA — NYC Transit",              "portal": "https://www.mta.info/doing-business-with-us/procurement/new-york-city-transit"},
    {"id": "nymta_lirr","name": "NY MTA — Long Island Rail Road",    "portal": "https://www.mta.info/doing-business-with-us/procurement/long-island-rail-road"},
    {"id": "mbta",      "name": "Boston MBTA",                       "portal": "https://bc.mbta.com/business_center/bidding_solicitations/current_solicitations/"},
    {"id": "njtransit", "name": "NJ Transit",                        "portal": "https://www.njtransit.com/procurement/calendar"},
]

KEYWORDS = (
    "program management OR project management OR construction management OR "
    "advisory OR consulting OR zero emission OR ZEB OR battery electric OR "
    "electrification OR EV charging OR EVSE OR microgrid OR renewable energy OR "
    "energy storage OR owner's representative OR project controls OR "
    "P3 OR public-private partnership OR alternative delivery OR "
    "operations and maintenance OR O&M consulting OR "
    "grant management OR federal funding OR FTA grant OR "
    "asset management OR CMMS OR EAM OR "
    "data analytics OR performance reporting OR business intelligence OR "
    "procurement advisory OR sourcing strategy OR contract management"
)

CATEGORY_LABELS = {
    "pm":    "Program / Project Mgmt",
    "cm":    "Construction Mgmt",
    "adv":   "Advisory & Consulting",
    "zev":   "Zero Emissions / EV",
    "micro": "Microgrid / Energy",
    "p3":    "P3 / Alternative Delivery",
    "om":    "Operations & Maintenance",
    "grant": "Grant Management",
    "asset": "Asset Management",
    "data":  "Data Analytics & Reporting",
    "proc":  "Procurement Advisory",
}

CATEGORY_COLORS = {
    "pm":    {"bg": "#EEEDFE", "text": "#26215C"},
    "cm":    {"bg": "#E1F5EE", "text": "#085041"},
    "adv":   {"bg": "#FAEEDA", "text": "#633806"},
    "zev":   {"bg": "#EAF3DE", "text": "#27500A"},
    "micro": {"bg": "#FAECE7", "text": "#4A1B0C"},
    "p3":    {"bg": "#E6F1FB", "text": "#0C447C"},
    "om":    {"bg": "#FEF3C7", "text": "#78350F"},
    "grant": {"bg": "#FCE7F3", "text": "#831843"},
    "asset": {"bg": "#E0E7FF", "text": "#3730A3"},
    "data":  {"bg": "#F0FDF4", "text": "#14532D"},
    "proc":  {"bg": "#FFF7ED", "text": "#7C2D12"},
}

SEEN_FILE = Path("data/seen_rfps.json")

SYSTEM_PROMPT = """You are an RFP procurement researcher for Accenture's AEC consulting practice,
which pursues professional services contracts with transit agencies.

Search for active or recently issued RFPs from the given transit agency.

Return ONLY valid JSON — no markdown, no backticks, no explanation. Format:
{
  "rfps": [
    {
      "title": "Full RFP title",
      "summary": "2-3 sentence description of the scope of work",
      "deadline": "deadline date as Month DD YYYY, or Not specified",
      "issue_date": "issue date as Month DD YYYY, or Not specified",
      "category": "pm|cm|adv|zev|micro|p3|om|grant|asset|data|proc",
      "rfp_number": "RFP/IFB/RFQ number, or Not specified",
      "url": "direct URL to RFP posting or procurement page"
    }
  ]
}

Category codes:
  pm    = program management, project management, project controls, PMO, schedule management
  cm    = construction management, CM advisory, owner's representative, inspector of record
  adv   = advisory, consulting, strategic planning, technical assistance, organizational assessment
  zev   = zero emission vehicles, battery electric buses, electrification, EV charging, EVSE, ZEB
  micro = microgrid, energy storage, solar, renewable energy, distributed energy resources
  p3    = public-private partnership, P3, alternative delivery, concession, DBFOM, DBFM
  om    = operations and maintenance consulting, O&M advisory, service delivery, workforce, safety management
  grant = grant management, federal funding strategy, FTA grants, RAISE, CRISI, funding advisory
  asset = asset management systems, CMMS, EAM, asset lifecycle, inventory management
  data  = data analytics, performance reporting, business intelligence, dashboards, KPIs, data strategy
  proc  = procurement advisory, sourcing strategy, contract management, vendor management, supply chain

IMPORTANT: Include both professional services/consulting RFPs AND construction contracts (CMAR, design-build, general contractor). Seeing construction awards helps anticipate upcoming advisory, program management, and owner's representative opportunities that typically follow.

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


# ── Git helpers ───────────────────────────────────────────────────────────────

def git_push_seen() -> None:
    """Pull latest then push updated seen_rfps.json to avoid conflicts."""
    try:
        subprocess.run(["git", "config", "user.name", "rfp-radar[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "rfp-radar[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(SEEN_FILE)], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if result.returncode == 0:
            print("No changes to seen_rfps.json — skipping push.")
            return
        subprocess.run(["git", "commit", "-m", "Update seen_rfps.json [skip ci]"], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("seen_rfps.json pushed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Git push warning (non-fatal): {e}")


# ── Anthropic API call ────────────────────────────────────────────────────────

def search_agency(client: anthropic.Anthropic, agency: dict) -> list[dict]:
    """Search one agency for matching RFPs using Claude with web search."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
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

        text = "".join(
            block.text for block in response.content
            if hasattr(block, "text")
        )

        if not text.strip():
            print(f"  [{agency['name']}] No text response returned")
            return []

        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]

        parsed = json.loads(clean.strip())
        rfps = parsed.get("rfps", [])

        for rfp in rfps:
            rfp["agency"]      = agency["name"]
            rfp["agency_id"]   = agency["id"]
            rfp["_id"]         = make_rfp_id(agency["name"], rfp.get("title", ""))
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
    cat        = rfp.get("category", "adv")
    colors     = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["adv"])
    cat_label  = CATEGORY_LABELS.get(cat, cat)
    deadline   = rfp.get("deadline", "Not specified")
    rfp_num    = rfp.get("rfp_number", "Not specified")
    url        = rfp.get("url", "#")
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
    count     = len(new_rfps)
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
    <div style="background:#1a1a1a;padding:24px 28px;">
      <h1 style="color:#fff;margin:0;font-size:18px;font-weight:500;letter-spacing:-0.01em;">
        🚌 Transit RFP Radar
      </h1>
      <p style="color:#aaa;margin:6px 0 0;font-size:13px;">{run_date}</p>
    </div>
    <div style="padding:18px 28px;background:#f8f8f4;border-bottom:1px solid #e8e8e4;
                display:flex;gap:32px;">
      <div>
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">New RFPs</div>
        <div style="font-size:28px;font-weight:600;color:{'#185FA5' if count > 0 else '#1a1a1a'};">{count}</div>
      </div>
      <div>
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:0.05em;">Agencies searched</div>
        <div style="font-size:28px;font-weight:600;color:#1a1a1a;">{agencies_searched}</div>
      </div>
    </div>
    {body_content}
    <div style="padding:16px 28px;border-top:1px solid #e8e8e4;font-size:11px;color:#aaa;line-height:1.6;">
      Monitoring: LA Metro · OCTA · Long Beach Transit · Riverside Transit Agency ·
      San Diego MTS · Omnitrans · NCTD · King County Metro · TriMet ·
      NY MTA · MBTA · NJ Transit · LADOT
      <br>Categories: Program Mgmt · Construction Mgmt · Advisory · Zero Emissions · Microgrid/Energy ·
      P3 · Operations & Maintenance · Grant Management · Asset Management · Data Analytics · Procurement Advisory
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
    to_addresses  = [a.strip() for a in os.environ["DIGEST_TO_EMAIL"].split(",")]

    subject = f"Transit RFP Digest - {run_date} ({new_count} new)"

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

    print(f"Email sent to {', '.join(to_addresses)}")


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

        if i < len(AGENCIES) - 1:
            time.sleep(8)

    print(f"\n{len(new_rfps)} new RFP(s) found across {len(AGENCIES)} agencies")

    save_seen(seen)
    git_push_seen()

    html = build_email_html(new_rfps, run_date, len(AGENCIES))
    send_email(html, len(new_rfps), run_date)

    print("\nDone.")


if __name__ == "__main__":
    main()
