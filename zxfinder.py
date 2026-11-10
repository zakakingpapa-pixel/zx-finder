#!/usr/bin/env python3
"""
ZX Finder - username finder engine for Termux / Linux.

Features
  * Built-in site list + optional import of the Sherlock data.json database
  * Three detection methods: status code, error message, redirect (response_url)
  * Calibration: probes each site with a random fake username and flags sites
    that say "found" for everything (false-positive detection)
  * Honest statuses: FOUND / NOT_FOUND / UNSURE / BLOCKED / ERROR / INVALID
  * Username variations (name_, name123, name.official ...)
  * Multi-threaded, retries, proxy / Tor support
  * Reports: HTML, JSON, CSV, TXT
  * Interactive menu (run with no arguments)

Install:   pip install requests
Run:       python zxfinder.py                     (menu)
           python zxfinder.py username
           python zxfinder.py username --calibrate --variations
"""

import argparse
import csv
import html
import json
import os
import random
import re
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional

try:
    import requests
except ImportError:
    sys.exit("The 'requests' package is missing. Run:  pip install requests")

VERSION = "1.0"

FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
UNSURE = "UNSURE"
BLOCKED = "BLOCKED"
ERROR = "ERROR"
INVALID = "INVALID"

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

USE_COLOR = sys.stdout.isatty()
USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")


def col(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


BANNER = r"""
 ______  __  __   _____ _           _
|__  / \/ / |  ___(_)_ __   __| | ___ _ __
  / / \  /  | |_  | | '_ \ / _` |/ _ \ '__|
 / /_ /  \  |  _| | | | | | (_| |  __/ |
/____/_/\_\ |_|   |_|_| |_|\__,_|\___|_|
"""


# ----------------------------------------------------------------------------
# Site database
# ----------------------------------------------------------------------------

@dataclass
class Site:
    name: str
    url: str                                   # profile URL, "{}" = username
    kinds: List[str] = field(default_factory=lambda: ["status_code"])
    error_msgs: List[str] = field(default_factory=list)
    regex: Optional[str] = None                # valid username pattern
    probe: Optional[str] = None                # different URL used for the request
    reliable: bool = True
    note: str = ""

    def link(self, username):
        return self.url.replace("{}", username)

    def probe_link(self, username):
        return (self.probe or self.url).replace("{}", username)


BUILTIN_SITES = [
    Site("GitHub", "https://github.com/{}",
         regex=r"^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$"),
    Site("YouTube", "https://www.youtube.com/@{}"),
    Site("Snapchat", "https://www.snapchat.com/add/{}"),
    Site("Reddit", "https://www.reddit.com/user/{}",
         probe="https://www.reddit.com/user/{}/about.json"),
    Site("Pinterest", "https://www.pinterest.com/{}/"),
    Site("Medium", "https://medium.com/@{}"),
    Site("DEV", "https://dev.to/{}"),
    Site("Pastebin", "https://pastebin.com/u/{}"),
    Site("SoundCloud", "https://soundcloud.com/{}"),
    Site("Vimeo", "https://vimeo.com/{}"),
    Site("Linktree", "https://linktr.ee/{}"),
    Site("About.me", "https://about.me/{}"),
    Site("Replit", "https://replit.com/@{}"),
    Site("Kaggle", "https://www.kaggle.com/{}"),
    Site("Steam", "https://steamcommunity.com/id/{}",
         kinds=["message"],
         error_msgs=["The specified profile could not be found"]),
    # These sites hide the real answer from bots (login wall / JavaScript page).
    # ZX Finder never claims FOUND for them - it gives the link to open by hand.
    Site("TikTok", "https://www.tiktok.com/@{}", reliable=False,
         note="login wall / bot protection"),
    Site("Instagram", "https://www.instagram.com/{}/", reliable=False,
         note="login wall / bot protection"),
    Site("Facebook", "https://www.facebook.com/{}", reliable=False,
         note="login wall / bot protection"),
    Site("X (Twitter)", "https://x.com/{}", reliable=False,
         note="JavaScript page, login wall"),
    Site("Threads", "https://www.threads.net/@{}", reliable=False,
         note="JavaScript page, login wall"),
]


def load_sherlock(path):
    """Convert a Sherlock data.json file into Site objects (NSFW entries skipped)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    sites = []
    for name, d in data.items():
        if name.startswith("$") or not isinstance(d, dict) or "url" not in d:
            continue
        if d.get("isNSFW"):
            continue
        et = d.get("errorType", "status_code")
        kinds = et if isinstance(et, list) else [et]
        em = d.get("errorMsg", [])
        em = em if isinstance(em, list) else [em]
        sites.append(Site(
            name=name,
            url=d["url"],
            kinds=[k for k in kinds if isinstance(k, str)] or ["status_code"],
            error_msgs=[m for m in em if isinstance(m, str)],
            regex=d.get("regexCheck"),
            probe=d.get("urlProbe"),
            note="from Sherlock database",
        ))
    return sites


def merge_sites(builtin, extra):
    """Built-in entries win over imported ones with the same name."""
    seen = {s.name.lower() for s in builtin}
    merged = list(builtin)
    for s in extra:
        if s.name.lower() not in seen:
            merged.append(s)
            seen.add(s.name.lower())
    return merged


# ----------------------------------------------------------------------------
# Checking engine
# ----------------------------------------------------------------------------

@dataclass
class Result:
    site: str
    username: str
    status: str
    url: str
    http: int = 0
    ms: int = 0
    note: str = ""


_local = threading.local()


def get_session(args):
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        if args.proxy:
            s.proxies = {"http": args.proxy, "https": args.proxy}
        _local.session = s
    return s


def classify(site, resp):
    code = resp.status_code
    if code in (401, 403, 429) or code >= 500:
        return BLOCKED, "HTTP %d (blocked or rate-limited)" % code
    if not site.reliable:
        return UNSURE, site.note + " - open the link manually"
    if code in (404, 410):
        return NOT_FOUND, ""
    if "message" in site.kinds and site.error_msgs:
        body = resp.text
        if any(m in body for m in site.error_msgs):
            return NOT_FOUND, ""
    if "response_url" in site.kinds and not (200 <= code < 300):
        return NOT_FOUND, ""
    if 200 <= code < 300:
        return FOUND, ""
    return UNSURE, "HTTP %d" % code


def check_site(site, username, args):
    link = site.link(username)
    if site.regex:
        try:
            if not re.match(site.regex, username):
                return Result(site.name, username, INVALID, link,
                              note="username format not allowed on this site")
        except re.error:
            pass
    url = site.probe_link(username)
    follow = "response_url" not in site.kinds
    session = get_session(args)
    last_error = ""
    for attempt in range(args.retries + 1):
        started = time.time()
        try:
            resp = session.get(
                url,
                headers={"User-Agent": random.choice(USER_AGENTS),
                         "Accept-Language": "en-US,en;q=0.9"},
                timeout=args.timeout,
                allow_redirects=follow,
            )
            ms = int((time.time() - started) * 1000)
            status, note = classify(site, resp)
            if status == BLOCKED and attempt < args.retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return Result(site.name, username, status, link, resp.status_code, ms, note)
        except requests.RequestException as exc:
            last_error = exc.__class__.__name__
            time.sleep(0.5 * (attempt + 1))
    return Result(site.name, username, ERROR, link, note=last_error)


def calibrate(sites, args):
    """Probe reliable sites with a random fake username; any 'FOUND' = false positive."""
    flagged = []

    def probe(site):
        fake = "zx" + "".join(random.choices(string.ascii_lowercase + string.digits, k=14))
        return site, check_site(site, fake, args).status

    targets = [s for s in sites if s.reliable]
    with ThreadPoolExecutor(args.threads) as ex:
        for site, status in ex.map(probe, targets):
            if status == FOUND:
                site.reliable = False
                site.note = "says 'found' for fake usernames (false positive)"
                flagged.append(site.name)
    return flagged


def variations(username):
    u = username.lstrip("@")
    year = datetime.now().year
    out = [u, u + "_", "_" + u, u + "1", u + "01", u + "123", u + ".official",
           u + "_official", u + "official", "the" + u, "real" + u, u + "_pk",
           u + str(year)]
    seen, unique = set(), []
    for v in out:
        if v.lower() not in seen and USERNAME_RE.match(v):
            seen.add(v.lower())
            unique.append(v)
    return unique


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

TAGS = {
    FOUND: ("[+]", "92"),
    NOT_FOUND: ("[-]", "90"),
    UNSURE: ("[?]", "93"),
    BLOCKED: ("[!]", "91"),
    ERROR: ("[x]", "91"),
    INVALID: ("[~]", "90"),
}


def show(res, args):
    if res.status in (FOUND, UNSURE) or args.all:
        tag, code = TAGS[res.status]
        line = "%s %-14s %s" % (col(tag, code), res.site, res.url)
        if res.note and res.status != FOUND:
            line += "  (%s)" % res.note
        if USE_COLOR:
            sys.stdout.write("\r\033[K")
        print(line)


def progress(done, total):
    if USE_COLOR:
        sys.stdout.write("\r\033[K  checking %d/%d" % (done, total))
        sys.stdout.flush()


def scan(usernames, sites, args):
    tasks = [(s, u) for u in usernames for s in sites]
    results = []
    total = len(tasks)
    print("  %d usernames x %d sites = %d checks\n" % (len(usernames), len(sites), total))
    with ThreadPoolExecutor(args.threads) as ex:
        futures = [ex.submit(check_site, s, u, args) for s, u in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            show(res, args)
            progress(i, total)
    if USE_COLOR:
        sys.stdout.write("\r\033[K")
    order = {FOUND: 0, UNSURE: 1, BLOCKED: 2, ERROR: 3, NOT_FOUND: 4, INVALID: 5}
    results.sort(key=lambda r: (order[r.status], r.username, r.site))
    return results


def summary(results):
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("\n  Summary: " + "  ".join(
        "%s=%d" % (k, counts.get(k, 0))
        for k in (FOUND, UNSURE, BLOCKED, ERROR, NOT_FOUND, INVALID)))
    found = [r for r in results if r.status == FOUND]
    if found:
        print("\n  Confirmed profiles:")
        for r in found:
            print("   - %-14s %s" % (r.site, r.url))
    unsure = [r for r in results if r.status == UNSURE]
    if unsure:
        print("\n  Open these manually (site hides the answer from bots):")
        for r in unsure[:15]:
            print("   - %-14s %s" % (r.site, r.url))


def save_reports(results, usernames, args):
    formats = {f.strip().lower() for f in args.out.split(",") if f.strip()}
    folder = os.path.join(os.path.expanduser("~"), "zxfinder_results")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(folder, "%s_%s" % (usernames[0], stamp))
    saved = []

    if "json" in formats:
        path = base + ".json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"tool": "ZX Finder " + VERSION, "time": stamp,
                       "results": [asdict(r) for r in results]}, fh, indent=2)
        saved.append(path)

    if "csv" in formats:
        path = base + ".csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["username", "site", "status", "url", "http", "ms", "note"])
            for r in results:
                w.writerow([r.username, r.site, r.status, r.url, r.http, r.ms, r.note])
        saved.append(path)

    if "txt" in formats:
        path = base + ".txt"
        with open(path, "w", encoding="utf-8") as fh:
            for r in results:
                if r.status in (FOUND, UNSURE):
                    fh.write("%s\t%s\t%s\n" % (r.status, r.site, r.url))
        saved.append(path)

    if "html" in formats:
        path = base + ".html"
        rows = []
        for r in results:
            if r.status not in (FOUND, UNSURE):
                continue
            rows.append(
                "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td>"
                "<td><a href='%s' rel='noopener'>%s</a></td><td>%s</td></tr>" % (
                    r.status.lower(), html.escape(r.username), html.escape(r.site),
                    r.status, html.escape(r.url, quote=True), html.escape(r.url),
                    html.escape(r.note)))
        page = (
            "<!doctype html><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>ZX Finder report</title>"
            "<style>body{font-family:sans-serif;margin:12px}"
            "table{border-collapse:collapse;width:100%%}"
            "td,th{border:1px solid #888;padding:6px;font-size:14px;word-break:break-all}"
            "tr.found{background:#d6f5d6}tr.unsure{background:#fff3cd}</style>"
            "<h2>ZX Finder %s</h2><p>%s &mdash; %s</p>"
            "<table><tr><th>User</th><th>Site</th><th>Status</th><th>Link</th><th>Note</th></tr>"
            "%s</table>" % (VERSION, html.escape(", ".join(usernames[:5])), stamp, "".join(rows)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        saved.append(path)
    return saved


# ----------------------------------------------------------------------------
# WhatsApp / WeChat helper
# ----------------------------------------------------------------------------

def phone_helper(number):
    digits = re.sub(r"\D", "", number)
    if digits.startswith("00"):
        digits = digits[2:]
    if not 7 <= len(digits) <= 15:
        print("  Invalid number. Use full international format, e.g. +923001234567")
        return
    print("\n  WhatsApp link : https://wa.me/%s" % digits)
    print("  Note: this only opens a chat link. It cannot confirm that the number")
    print("  has WhatsApp, and WhatsApp/WeChat have no public username pages.")
    print("  To recover these accounts use the phone number inside the app itself.\n")


# ----------------------------------------------------------------------------
# Running a scan
# ----------------------------------------------------------------------------

def select_sites(sites, wanted):
    if not wanted:
        return sites
    names = {w.strip().lower() for w in wanted.split(",") if w.strip()}
    chosen = [s for s in sites if s.name.lower() in names]
    missing = names - {s.name.lower() for s in chosen}
    if missing:
        print("  Unknown sites ignored: " + ", ".join(sorted(missing)))
    return chosen


def clean_usernames(raw):
    out = []
    for u in raw:
        u = u.strip().lstrip("@")
        if USERNAME_RE.match(u):
            out.append(u)
        else:
            print("  Skipped %r (allowed: letters, digits, . _ - up to 40 chars)" % u)
    return out


def run(usernames, sites, args):
    usernames = clean_usernames(usernames)
    if not usernames:
        print("  No valid username given.")
        return
    if args.variations:
        expanded = []
        for u in usernames:
            expanded.extend(variations(u))
        usernames = list(dict.fromkeys(expanded))
    if args.calibrate:
        print("  Calibrating sites with fake usernames ...")
        flagged = calibrate(sites, args)
        if flagged:
            print("  Marked unreliable: " + ", ".join(flagged))
    started = time.time()
    results = scan(usernames, sites, args)
    summary(results)
    print("\n  Done in %.1fs" % (time.time() - started))
    if not args.no_save:
        for path in save_reports(results, usernames, args):
            print("  Saved: " + path)


# ----------------------------------------------------------------------------
# Interactive menu + CLI
# ----------------------------------------------------------------------------

def menu(args, sites):
    while True:
        print("""
  1) Scan a username
  2) Scan with username variations
  3) WhatsApp link from phone number
  4) Load Sherlock data.json
  5) Toggle calibration  (now: %s)
  6) List sites (%d loaded)
  0) Exit""" % ("ON" if args.calibrate else "off", len(sites)))
        choice = input("\n  > ").strip()
        if choice == "0":
            return
        if choice in ("1", "2"):
            name = input("  Username: ").strip()
            args.variations = choice == "2"
            run([name], sites, args)
        elif choice == "3":
            phone_helper(input("  Phone (+countrycode...): "))
        elif choice == "4":
            path = os.path.expanduser(input("  Path to data.json: ").strip())
            try:
                sites[:] = merge_sites(sites, load_sherlock(path))
                print("  Now %d sites loaded." % len(sites))
            except (OSError, ValueError) as exc:
                print("  Could not load file: %s" % exc)
        elif choice == "5":
            args.calibrate = not args.calibrate
        elif choice == "6":
            for s in sites:
                print("   - %s%s" % (s.name, "" if s.reliable else "  (manual check)"))


def build_parser():
    p = argparse.ArgumentParser(
        prog="zxfinder", description="ZX Finder - username finder engine")
    p.add_argument("usernames", nargs="*", help="username(s) to search")
    p.add_argument("--variations", action="store_true", help="also try name_, name123 ...")
    p.add_argument("-s", "--sites", help="only these sites, comma separated")
    p.add_argument("--sherlock", metavar="DATA.JSON", help="import Sherlock data.json")
    p.add_argument("-t", "--threads", type=int, default=20)
    p.add_argument("--timeout", type=int, default=10)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--proxy", help="e.g. socks5h://127.0.0.1:9050 (needs requests[socks])")
    p.add_argument("--calibrate", action="store_true", help="detect false-positive sites")
    p.add_argument("--all", action="store_true", help="print not-found results too")
    p.add_argument("-o", "--out", default="html,json", help="html,json,csv,txt")
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--phone", help="make a WhatsApp link from a phone number")
    p.add_argument("--list-sites", action="store_true")
    return p


def main():
    global USE_COLOR
    args = build_parser().parse_args()
    if args.no_color:
        USE_COLOR = False
    args.threads = max(1, min(args.threads, 60))
    print(col(BANNER, "96") + "  ZX Finder v%s\n" % VERSION)

    sites = list(BUILTIN_SITES)
    if args.sherlock:
        try:
            sites = merge_sites(sites, load_sherlock(os.path.expanduser(args.sherlock)))
        except (OSError, ValueError) as exc:
            sys.exit("  Could not load Sherlock file: %s" % exc)
    sites = select_sites(sites, args.sites)

    if args.list_sites:
        for s in sites:
            print("  %-16s %s" % (s.name, s.url))
        return
    if args.phone:
        phone_helper(args.phone)
        return
    if not args.usernames:
        try:
            menu(args, sites)
        except (KeyboardInterrupt, EOFError):
            print()
        return
    try:
        run(args.usernames, sites, args)
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
