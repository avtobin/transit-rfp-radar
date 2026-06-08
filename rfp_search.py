#!/usr/bin/env python3
"""
Transit RFP Radar - HTML Scraper Version
Scrapes procurement portals directly using Playwright (no web search).
Must run from a laptop/desktop with a residential IP.
"""

import os
import re
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

# ── File paths ────────────────────────────────────────────────────────────────

SEEN_FILE = Path("data/seen_rfps.json")
RFPS_FILE = Path("data/rfps.json")

# ── Agency configuration ──────────────────────────────────────────────────────
# scrape_type: playwright | static | mta

AGENCIES = [
    {"id": "octa",        "name": "OCTA",                                "url": "https://procurement.opengov.com/portal/octa?status=open",                                                                   "scrape_type": "playwright"},
    {"id": "lbt",         "name": "Long Beach Transit",                  "url": "https://vendors.planetbids.com/portal/28908/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "rta",         "name": "Riverside Transit Agency",            "url": "https://vendors.planetbids.com/portal/55483/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "sdmts",       "name": "San Diego MTS",                       "url": "https://vendors.planetbids.com/portal/14771/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "omnitrans",   "name": "Omnitrans",                           "url": "https://vendors.planetbids.com/portal/18046/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "nctd",        "name": "North County Transit District",       "url": "https://vendors.planetbids.com/portal/20134/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "foothill",    "name": "Foothill Transit",                    "url": "https://vendors.planetbids.com/portal/29905/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "sunline",     "name": "SunLine Transit",                     "url": "https://vendors.planetbids.com/portal/56419/bo/bo-search?stage_id=2",                                                                   "scrape_type": "playwright"},
    {"id": "sbcta",       "name": "San Bernardino CTA",                  "url": "https://www.gosbcta.com/doing-business/bids-rfps/",                                                                          "scrape_type": "static"},
    {"id": "metrolink",   "name": "Metrolink",                           "url": "https://metrolinktrains.com/about/doing-business-with-metrolink/procurement-opportunities/",                                 "scrape_type": "static"},
    {"id": "ladot",       "name": "LADOT",                               "url": "https://www.rampla.org/s/",                                                                                                  "scrape_type": "playwright"},
    {"id": "lametro",     "name": "LA Metro",                            "url": "https://business.metro.net/webcenter/portal/VendorPortal",                                                                   "scrape_type": "playwright"},
    {"id": "bart",        "name": "BART",                                "url": "https://suppliers.bart.gov/psp/BRFPV91/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL?active=P",                       "scrape_type": "playwright"},
    {"id": "vta",         "name": "VTA (Santa Clara)",                   "url": "https://procurement.opengov.com/portal/vta?status=open",                                                                     "scrape_type": "playwright"},
    {"id": "actransit",   "name": "AC Transit",                          "url": "https://actransit.bonfirehub.com/portal/?tab=openOpportunities",                                                             "scrape_type": "playwright"},
    {"id": "kcmetro",     "name": "King County Metro",                   "url": "https://fa-epvh-saasfaprod1.fa.ocs.oraclecloud.com/fscmUI/faces/NegotiationAbstracts?prcBuId=300000001727151",               "scrape_type": "playwright"},
    {"id": "trimet",      "name": "TriMet",                              "url": "https://bidlocker.us/a/trimet/BidLocker",                                                                                    "scrape_type": "playwright"},
    {"id": "soundtransit","name": "Sound Transit",                       "url": "https://www.biddingo.com/soundtransit",                                                                                      "scrape_type": "playwright"},
    {"id": "commtransit", "name": "Community Transit",                   "url": "https://commtrans.procureware.com/Bids",                                                                                     "scrape_type": "playwright"},
    {"id": "rtd",         "name": "Denver RTD",                          "url": "https://procurement.opengov.com/portal/rtd-denver?status=open",                                                              "scrape_type": "playwright"},
    {"id": "houston",     "name": "Houston Metro",                       "url": "https://www.ridemetro.org/about/business-to-business/procurement-opportunities",                                             "scrape_type": "static"},
    {"id": "wmata",       "name": "WMATA",                               "url": "https://supplier.wmata.com/psp/supplier_1/SUPPLIER/ERP/c/AUC_MANAGE_BIDS.AUC_RESP_INQ_AUC.GBL",                            "scrape_type": "playwright"},
    {"id": "septa",       "name": "SEPTA",                               "url": "https://www.septa.org/procurement/bids/",                                                                                   "scrape_type": "static"},
    {"id": "prt",         "name": "Pittsburgh PRT",                      "url": "https://www.rideprt.org/business-center/procurement/bids-and-rfps/",                                                        "scrape_type": "static"},
    {"id": "nymta_cd",    "name": "NY MTA Construction & Development",   "url": "https://www.mta.info/agency/construction-and-development/contracting/current-opportunities",                                 "scrape_type": "mta"},
    {"id": "nymta_gen",   "name": "NY MTA General Procurement",          "url": "https://www.mta.info/doing-business-with-us/procurement/current-opportunities",                                              "scrape_type": "mta"},
    {"id": "nymta_lirr",  "name": "NY MTA Long Island Rail Road",        "url": "https://www.mta.info/doing-business-with-us/procurement/long-island-rail-road",                                             "scrape_type": "mta"},
    {"id": "nymta_nyct",  "name": "NY MTA NYC Transit",                  "url": "https://www.mta.info/doing-business-with-us/procurement/new-york-city-transit",                                             "scrape_type": "mta"},
    {"id": "nymta_hq",    "name": "NY MTA Headquarters",                 "url": "https://www.mta.info/doing-business-with-us/procurement/mta-headquarters",                                                  "scrape_type": "mta"},
    {"id": "mbta",        "name": "Boston MBTA",                         "url": "https://bc.mbta.com/business_center/bidding_solicitations/current_solicitations/",                                          "scrape_type": "static"},
    {"id": "njtransit",   "name": "NJ Transit",                          "url": "https://www.njtransit.com/procurement/calendar",                                                                            "scrape_type": "static"},
]

MTA_FAMILY = {"nymta_cd", "nymta_gen", "nymta_hq", "nymta_nyct", "nymta_lirr"}

RELEVANT_KEYWORDS = [
    "program management", "project management", "construction management",
    "advisory", "consulting", "zero emission", "zeb", "battery electric",
    "electrification", "ev charging", "evse", "charging infrastructure",
    "electric bus", "microgrid", "renewable energy", "energy storage",
    "owner's representative", "owner representative", "project controls",
    "p3", "public-private", "alternative delivery",
    "operations and maintenance", "o&m", "grant management", "federal funding",
    "fta grant", "asset management", "cmms", "eam", "data analytics",
    "performance reporting", "procurement advisory",
    "capital program", "capital project", "infrastructure", "planning",
    "environmental", "community outreach", "stakeholder",
]

PRIORITY_AGENCIES = {
    "octa", "lbt", "sdmts", "ladot", "lametro",
    "kcmetro", "soundtransit",
    "nymta_cd", "nymta_gen", "nymta_hq", "nymta_nyct", "nymta_lirr",
}

ZEV_KEYWORDS = [
    "zero emission", "zeb", "battery electric", "ev charging", "evse",
    "electrification", "charging infrastructure", "electric bus",
]

STRONG_FIT_CATEGORIES = {"pm", "cm", "p3", "grant"}
GOOD_FIT_CATEGORIES   = {"adv", "asset", "data"}

CATEGORY_LABELS = {
    "pm": "Program / Project Mgmt", "cm": "Construction Mgmt",
    "adv": "Advisory & Consulting", "zev": "Zero Emissions / EV",
    "micro": "Microgrid / Energy", "p3": "P3 / Alternative Delivery",
    "om": "Operations & Maintenance", "grant": "Grant Management",
    "asset": "Asset Management", "data": "Data Analytics & Reporting",
    "proc": "Procurement Advisory",
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

FIT_LABELS = {"strong": "Strong Fit", "good": "Good Fit", "monitor": "Monitor"}
FIT_COLORS = {
    "strong": {"bg": "#EAF3DE", "text": "#27500A"},
    "good":   {"bg": "#FAEEDA", "text": "#633806"},
    "monitor":{"bg": "#F1EFE8", "text": "#5F5E5A"},
}

CATEGORIZE_PROMPT = """You are an AEC procurement analyst. Categorize these procurement opportunities.

Return ONLY valid JSON, no markdown, no backticks:
{
  "rfps": [
    {
      "title": "exact title",
      "summary": "2-3 sentence scope description",
      "deadline": "Month DD YYYY or Not specified",
      "rfp_number": "number or Not specified",
      "category": "pm|cm|adv|zev|micro|p3|om|grant|asset|data|proc",
      "url": "url from input"
    }
  ]
}

Categories: pm=program/project mgmt, cm=construction mgmt/owner's rep,
adv=advisory/consulting/planning, zev=zero emission/EV/electrification,
micro=microgrid/energy, p3=P3/alternative delivery,
om=operations & maintenance, grant=grant management,
asset=asset management, data=data analytics, proc=procurement advisory

Only include AEC professional services opportunities. Return {"rfps": []} if nothing relevant."""


def score_fit(rfp: dict) -> str:
    text   = ((rfp.get("title") or "") + " " + (rfp.get("summary") or "")).lower()
    cat    = rfp.get("category", "adv")
    agency = rfp.get("agency_id", "")
    if any(kw in text for kw in ZEV_KEYWORDS): return "strong"
    if cat in STRONG_FIT_CATEGORIES and agency in PRIORITY_AGENCIES: return "strong"
    if cat in STRONG_FIT_CATEGORIES: return "good"
    if cat in GOOD_FIT_CATEGORIES and agency in PRIORITY_AGENCIES: return "good"
    return "monitor"

def _normalize(t): return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", t.lower())).strip()
def make_rfp_id(aid, aname, title):
    ns = "NY MTA" if aid in MTA_FAMILY else aname
    return hashlib.sha256((ns + _normalize(title)).encode()).hexdigest()[:24]

def load_seen():
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()
def save_seen(s):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(s), indent=2))
def load_all_rfps():
    return json.loads(RFPS_FILE.read_text()) if RFPS_FILE.exists() else []
def save_all_rfps(r):
    RFPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RFPS_FILE.write_text(json.dumps(r, indent=2))

def is_relevant(text):
    t = text.lower()
    return any(kw in t for kw in RELEVANT_KEYWORDS)


CLOSED_INDICATORS = [
    "closed", "awarded", "cancelled", "canceled", "expired",
    "no longer accepting", "contract awarded", "bid opening passed",
    "solicitation closed", "procurement closed",
]

def is_closed(text: str) -> bool:
    """Return True if the item appears to be closed/past."""
    t = text.lower()
    # Check for closed keywords
    if any(indicator in t for indicator in CLOSED_INDICATORS):
        return True
    # Check for past deadline dates
    return is_past_deadline(text)

def is_past_deadline(text: str) -> bool:
    """Return True if text contains a deadline date that has already passed."""
    import re
    from datetime import date
    today = date.today()
    months = {
        "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
        "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
    }
    # Pattern: "Month DD, YYYY" or "Month DD YYYY"
    pattern = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})'
    for m, d, y in re.findall(pattern, text.lower()):
        try:
            if date(int(y), months[m[:3]], int(d)) < today:
                return True
        except:
            pass
    # Pattern: "MM/DD/YYYY"
    for mo, dy, yr in re.findall(r'(\d{1,2})/(\d{1,2})/(\d{4})', text):
        try:
            if date(int(yr), int(mo), int(dy)) < today:
                return True
        except:
            pass
    return False


def scrape_with_playwright(agency):
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse
    except ImportError:
        print(f"  [{agency['name']}] Install: pip install playwright beautifulsoup4 && playwright install chromium")
        return []

    raw_items = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = ctx.new_page()
            page.goto(agency["url"], wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            content = page.content()
            browser.close()

        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        seen_keys = set()
        base = urlparse(agency["url"])

        for elem in soup.find_all(["tr", "li", "div", "article"]):
            text = elem.get_text(" ", strip=True)
            if 30 < len(text) < 800 and is_relevant(text) and not is_closed(text):
                key = text[:50]
                if key not in seen_keys:
                    seen_keys.add(key)
                    link = elem.find("a")
                    url = agency["url"]
                    if link and link.get("href"):
                        href = link["href"]
                        url = f"{base.scheme}://{base.netloc}{href}" if href.startswith("/") else href
                    raw_items.append({"text": text[:500], "url": url})

        print(f"  [{agency['name']}] Playwright: {len(raw_items)} items")
    except Exception as e:
        print(f"  [{agency['name']}] Playwright error: {e}")

    return raw_items[:10]


def scrape_static(agency):
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    raw_items = []

    try:
        r = requests.get(agency["url"], headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()

        seen_keys = set()
        base = urlparse(agency["url"])

        for elem in soup.find_all(["tr", "li", "div", "p", "article"]):
            text = elem.get_text(" ", strip=True)
            if 30 < len(text) < 800 and is_relevant(text) and not is_closed(text):
                key = text[:50]
                if key not in seen_keys:
                    seen_keys.add(key)
                    link = elem.find("a")
                    url = agency["url"]
                    if link and link.get("href"):
                        href = link["href"]
                        url = f"{base.scheme}://{base.netloc}{href}" if href.startswith("/") else href
                    raw_items.append({"text": text[:500], "url": url})

        print(f"  [{agency['name']}] Static: {len(raw_items)} items")
    except Exception as e:
        print(f"  [{agency['name']}] Static error: {e}")

    return raw_items[:10]


def scrape_mta(agency):
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    raw_items = []

    try:
        r = requests.get(agency["url"], headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        seen_keys = set()

        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                text = " | ".join(c.get_text(strip=True) for c in cells)
                if is_relevant(text) and not is_closed(text) and len(text) > 20:
                    key = text[:50]
                    if key not in seen_keys:
                        seen_keys.add(key)
                        link = row.find("a")
                        url = agency["url"]
                        if link and link.get("href"):
                            href = link["href"]
                            url = f"https://www.mta.info{href}" if href.startswith("/") else href
                        raw_items.append({"text": text[:500], "url": url})

        for item in soup.find_all(["li", "div"]):
            text = item.get_text(" ", strip=True)
            if 30 < len(text) < 600 and is_relevant(text) and not is_closed(text):
                key = text[:50]
                if key not in seen_keys:
                    seen_keys.add(key)
                    link = item.find("a")
                    url = agency["url"]
                    if link and link.get("href"):
                        href = link["href"]
                        url = f"https://www.mta.info{href}" if href.startswith("/") else href
                    raw_items.append({"text": text[:500], "url": url})

        print(f"  [{agency['name']}] MTA: {len(raw_items)} items")
    except Exception as e:
        print(f"  [{agency['name']}] MTA error: {e}")

    return raw_items[:10]


def categorize_with_claude(client, agency, raw_items):
    if not raw_items: return []

    items_text = "\n\n".join(f"Item {i+1}:\nText: {item['text']}\nURL: {item['url']}" for i, item in enumerate(raw_items))

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1500,
            system=CATEGORIZE_PROMPT,
            messages=[{"role": "user", "content": f"Agency: {agency['name']}\n\n{items_text}"}]
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"): clean = clean[4:]

        rfps = json.loads(clean.strip()).get("rfps", [])
        for rfp in rfps:
            rfp["agency"]      = agency["name"]
            rfp["agency_id"]   = agency["id"]
            rfp["_id"]         = make_rfp_id(agency["id"], agency["name"], rfp.get("title", ""))
            rfp["_found_date"] = datetime.now().strftime("%Y-%m-%d")
            rfp["fit"]         = score_fit(rfp)
        print(f"  [{agency['name']}] Claude: {len(rfps)} RFP(s)")
        return rfps
    except Exception as e:
        print(f"  [{agency['name']}] Claude error: {e}")
        return []


def git_push():
    try:
        subprocess.run(["git", "config", "user.name",  "rfp-radar[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "rfp-radar[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", str(SEEN_FILE), str(RFPS_FILE)], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            print("No changes to push."); return
        subprocess.run(["git", "commit", "-m", "Update rfps.json [skip ci]"], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("Pushed to GitHub.")
    except subprocess.CalledProcessError as e:
        print(f"Git warning: {e}")


def build_rfp_row(rfp):
    cat        = rfp.get("category", "adv")
    colors     = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["adv"])
    cat_label  = CATEGORY_LABELS.get(cat, cat)
    deadline   = rfp.get("deadline", "Not specified")
    rfp_num    = rfp.get("rfp_number", "Not specified")
    url        = rfp.get("url", "#")
    fit        = rfp.get("fit", "monitor")
    fc         = FIT_COLORS.get(fit, FIT_COLORS["monitor"])
    fl         = FIT_LABELS.get(fit, "Monitor")
    dlc        = "#993C1D" if deadline != "Not specified" else "#888"
    return f"""<tr>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;width:160px;">
        <span style="display:inline-block;padding:3px 9px;border-radius:4px;font-size:11px;font-weight:700;background:{fc['bg']};color:{fc['text']};">{fl}</span>
        <div style="margin-top:5px;"><span style="display:inline-block;padding:2px 7px;border-radius:3px;font-size:10px;font-weight:600;background:{colors['bg']};color:{colors['text']};">{cat_label}</span></div>
        <div style="margin-top:6px;font-size:12px;color:#555;font-weight:500;">{rfp.get('agency','')}</div>
      </td>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;">
        <div style="font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4;">{rfp.get('title','Untitled')}</div>
        <div style="margin-top:6px;font-size:13px;color:#555;line-height:1.6;">{rfp.get('summary','')}</div>
      </td>
      <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;vertical-align:top;white-space:nowrap;font-size:12px;min-width:120px;">
        {f'<div style="color:#888;">#{rfp_num}</div>' if rfp_num != "Not specified" else ""}
        <div style="margin-top:4px;color:{dlc};font-weight:{'500' if deadline != 'Not specified' else '400'};">{"Due: " + deadline if deadline != "Not specified" else "No deadline"}</div>
        <div style="margin-top:8px;"><a href="{url}" style="color:#185FA5;font-size:12px;">View RFP →</a></div>
      </td>
    </tr>"""


def build_email(new_rfps, run_date):
    count = len(new_rfps)
    sorted_rfps = sorted(new_rfps, key=lambda r: {"strong":0,"good":1,"monitor":2}.get(r.get("fit","monitor"),2))
    body = f"""<table style="width:100%;border-collapse:collapse;"><thead><tr style="background:#fafafa;">
      <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;text-transform:uppercase;border-bottom:1px solid #e8e8e4;">Fit / Category</td>
      <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;text-transform:uppercase;border-bottom:1px solid #e8e8e4;">RFP</td>
      <td style="padding:10px 16px;font-size:11px;font-weight:600;color:#888;text-transform:uppercase;border-bottom:1px solid #e8e8e4;">Details</td>
    </tr></thead><tbody>{"".join(build_rfp_row(r) for r in sorted_rfps)}</tbody></table>""" if new_rfps else """<div style="padding:48px 28px;text-align:center;color:#888;"><div style="font-size:32px;margin-bottom:12px;">✓</div><div style="font-size:15px;font-weight:500;color:#555;">All caught up — no new RFPs today.</div></div>"""

    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f4f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:720px;margin:32px auto;background:#fff;border-radius:10px;overflow:hidden;border:1px solid #e0e0d8;">
    <div style="background:#1a1a1a;padding:24px 28px;"><h1 style="color:#fff;margin:0;font-size:18px;font-weight:500;">🚌 Transit RFP Radar</h1><p style="color:#aaa;margin:6px 0 0;font-size:13px;">{run_date}</p></div>
    <div style="padding:18px 28px;background:#f8f8f4;border-bottom:1px solid #e8e8e4;display:flex;gap:32px;">
      <div><div style="font-size:11px;color:#888;text-transform:uppercase;">New RFPs</div><div style="font-size:28px;font-weight:600;color:{'#185FA5' if count else '#1a1a1a'};">{count}</div></div>
      <div><div style="font-size:11px;color:#888;text-transform:uppercase;">Agencies</div><div style="font-size:28px;font-weight:600;color:#1a1a1a;">{len(AGENCIES)}</div></div>
    </div>
    {body}
  </div></body></html>"""


def send_email(html, count, run_date):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587").strip())
    smtp_user = os.environ["SMTP_USER"].strip()
    smtp_pass = os.environ["SMTP_PASSWORD"].strip()
    to_addrs  = [a.strip() for a in os.environ["DIGEST_TO_EMAIL"].split(",")]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Transit RFP Digest — {run_date} ({count} new)"
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(smtp_host, smtp_port) as s:
        s.ehlo(); s.starttls(); s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, to_addrs, msg.as_string())
    print(f"Email sent to {', '.join(to_addrs)}")


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key: raise ValueError("ANTHROPIC_API_KEY not set")

    client   = anthropic.Anthropic(api_key=api_key)
    seen     = load_seen()
    run_date = datetime.now().strftime("%A, %B %d, %Y").replace(" 0", " ")

    print(f"\n=== Transit RFP Radar — {run_date} ===")
    print(f"Seen RFPs: {len(seen)}\n")

    new_rfps = []

    for i, agency in enumerate(AGENCIES):
        print(f"[{i+1}/{len(AGENCIES)}] {agency['name']}...")
        scrape_type = agency.get("scrape_type", "static")
        if scrape_type == "playwright":
            raw_items = scrape_with_playwright(agency)
        elif scrape_type == "mta":
            raw_items = scrape_mta(agency)
        else:
            raw_items = scrape_static(agency)

        if not raw_items: continue

        rfps = categorize_with_claude(client, agency, raw_items)
        for rfp in rfps:
            if rfp["_id"] not in seen:
                new_rfps.append(rfp)
                seen.add(rfp["_id"])
                print(f"    NEW: {rfp.get('title','Untitled')}")
            else:
                print(f"    Seen: {rfp.get('title','Untitled')}")
        time.sleep(2)

    print(f"\n{len(new_rfps)} new RFP(s) found")

    save_seen(seen)
    all_rfps = load_all_rfps()
    existing = {r["_id"] for r in all_rfps}
    for rfp in new_rfps:
        if rfp["_id"] not in existing: all_rfps.append(rfp)
    all_rfps.sort(key=lambda r: r.get("_found_date",""), reverse=True)
    save_all_rfps(all_rfps)

    git_push()
    send_email(build_email(new_rfps, run_date), len(new_rfps), run_date)
    print("\nDone.")


if __name__ == "__main__":
    main()
