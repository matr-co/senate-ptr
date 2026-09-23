#!/usr/bin/env python3
"""Scrape Senate eFD Periodic Transaction Reports into senate_ptrs.json.

efdsearch.senate.gov blocks non-US IPs, so this runs on GitHub Actions and solara reads the raw file.
  python scrape.py                 reports submitted in the last 14 days
  python scrape.py --since 2023-01-01
Existing reports are never re-fetched. The file is rewritten only when something changed, or once a day
as a heartbeat (the reader warns when it is more than 3 days old).
"""
import argparse, datetime as dt, html, json, re, sys, time
from pathlib import Path

import requests

ROOT = "https://efdsearch.senate.gov"
OUT = Path(__file__).with_name("senate_ptrs.json")
s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 (senate-ptr scraper; github.com/matr-co/senate-ptr)"


def agree():
    r = s.get(f"{ROOT}/search/home/", timeout=30)
    r.raise_for_status()
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text).group(1)
    r = s.post(f"{ROOT}/search/home/", data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
               headers={"Referer": f"{ROOT}/search/home/"}, timeout=30)
    r.raise_for_status()


def search(since):
    start, out = 0, []
    while True:
        r = s.post(f"{ROOT}/search/report/data/", timeout=60, headers={
            "Referer": f"{ROOT}/search/", "X-CSRFToken": s.cookies.get("csrftoken")}, data={
            "start": str(start), "length": "100", "report_types": "[11]", "filer_types": "[]",
            "submitted_start_date": since.strftime("%m/%d/%Y 00:00:00"), "submitted_end_date": "",
            "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": "",
            "csrfmiddlewaretoken": s.cookies.get("csrftoken")})
        r.raise_for_status()
        j = r.json()
        out += j["data"]
        start += 100
        if start >= j["recordsTotal"]:
            return out
        time.sleep(1)


def money(a):
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", a)]
    return (nums[0], nums[-1]) if nums else (None, None)


def cell(c):
    return html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()


def parse_ptr(page):
    trades = []
    body = page.split("<tbody>", 1)[-1].split("</tbody>", 1)[0]
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        c = [cell(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(c) < 8:
            continue
        _, date, owner, ticker, asset, atype, typ, amount = c[:8]
        lo, hi = money(amount)
        m, d, y = date.split("/")
        trades.append({"date": f"{y}-{int(m):02d}-{int(d):02d}", "owner": owner,
                       "ticker": None if ticker in ("--", "") else ticker.split()[0],
                       "asset": asset[:120], "asset_type": atype, "type": typ, "lo": lo, "hi": hi})
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    args = ap.parse_args()
    since = dt.date.fromisoformat(args.since) if args.since else dt.date.today() - dt.timedelta(days=14)
    data = json.loads(OUT.read_text()) if OUT.exists() else {"updated": None, "reports": []}
    have = {r["id"] for r in data["reports"]}
    agree()
    added = 0
    for first, last, _office, link, filed in search(since):
        href = re.search(r'href="([^"]+)"', link).group(1)
        rid = href.rstrip("/").split("/")[-1]
        if rid in have:
            continue
        m, d, y = filed.split("/")
        rep = {"id": rid, "name": f"{cell(first)} {cell(last)}", "first": cell(first), "last": cell(last),
               "filed": f"{y}-{int(m):02d}-{int(d):02d}", "url": ROOT + href, "title": cell(link), "trades": []}
        if "/ptr/" in href:  # "/paper/" = scanned GIFs, stays link-only
            r = s.get(ROOT + href, timeout=60)
            r.raise_for_status()
            rep["trades"] = parse_ptr(r.text)
            time.sleep(1)
        data["reports"].append(rep)
        have.add(rid)
        added += 1
    now = dt.datetime.now(dt.timezone.utc)
    stale = not data["updated"] or now - dt.datetime.fromisoformat(data["updated"]) > dt.timedelta(hours=20)
    if added or stale:
        data["updated"] = now.isoformat(timespec="seconds")
        data["reports"].sort(key=lambda r: r["filed"], reverse=True)
        OUT.write_text(json.dumps(data, indent=0))
    print(f"added {added} reports, total {len(data['reports'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
