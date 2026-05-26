#!/usr/bin/env python3
"""For each species in top_1k_species.tsv, query EPO OPS biblio search and
persist top-N hits with IPC/CPC inline. Resumable: skips species already in output.
"""
from __future__ import annotations
import base64
import csv
import json
import os
import sys
import time
from pathlib import Path
import requests
from requests.exceptions import HTTPError, RequestException

ROOT = Path("/Users/wimvancriekinge/treeoflife")
INPUT = ROOT / "data" / "top_1k_species.tsv"
OUTPUT = ROOT / "data" / "ops_hits.jsonl"
LOG = ROOT / "data" / "ops_search.log"

KEY = os.environ["OPS_KEY"]
SECRET = os.environ["OPS_SECRET"]
RANGE = "1-25"  # top-25 per species
SLEEP_MIN = 5.0   # never go faster than 12 req/min even if green:50 is reported
SLEEP_MAX = 90.0  # cap to a safe upper bound during black/red tiers


def get_token() -> tuple[str, float]:
    r = requests.post(
        "https://ops.epo.org/3.2/auth/accesstoken",
        auth=(KEY, SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    return d["access_token"], time.time() + int(d["expires_in"]) - 60


def search(token: str, query: str) -> tuple[dict, dict]:
    r = requests.get(
        "https://ops.epo.org/3.2/rest-services/published-data/search/biblio",
        params={"q": query, "Range": RANGE},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json(), dict(r.headers)


def parse_throttle(headers: dict) -> tuple[str, int]:
    """Parse x-throttling-control header into (tier, per_minute_limit) for the
    'search' service. Returns ('unknown', 60) on parse failure (defensive default)."""
    tc = headers.get("x-throttling-control") or headers.get("X-Throttling-Control") or ""
    import re as _re
    m = _re.search(r"search=([a-z]+):(\d+)", tc, _re.IGNORECASE)
    if not m:
        return ("unknown", 60)
    tier = m.group(1).lower()
    per_min = int(m.group(2))
    return (tier, per_min)


def adaptive_sleep(tier: str, per_min: int) -> float:
    """Pick a polite sleep based on the search tier and per-minute headroom.

    - green: 60/per_min + 25% margin  (e.g. green:15 -> 5s sleep)
    - yellow/orange: 60/per_min + 100% margin (very cautious)
    - red/black: SLEEP_MAX (back off hard)
    - unknown: SLEEP_MIN
    """
    if tier in ("red", "black"):
        return SLEEP_MAX
    if per_min <= 0:
        return SLEEP_MAX
    base = 60.0 / per_min
    if tier == "green":
        return max(SLEEP_MIN, base * 1.25)
    if tier in ("yellow", "orange"):
        return max(SLEEP_MIN, base * 2.0)
    return SLEEP_MIN


def _g(node, *path, default=None):
    """Safe nested-dict getter."""
    cur = node
    for k in path:
        if cur is None:
            return default
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur if cur is not None else default


def _as_list(v):
    return v if isinstance(v, list) else ([v] if v is not None else [])


def _txt(v):
    if isinstance(v, dict):
        return v.get("$", "")
    return v or ""


def parse_ipcr(biblio: dict) -> list[str]:
    out = []
    items = _as_list(_g(biblio, "classifications-ipcr", "classification-ipcr"))
    for it in items:
        raw = _txt(it.get("text"))
        # OPS returns fixed-width text like "A21D  13/    33            A I".
        # Splitting on whitespace yields [subclass, main_group_with_slash, subgroup, ...].
        head = raw.split()
        if len(head) >= 3:
            out.append(f"{head[0]} {head[1]}{head[2]}")
        elif len(head) == 2:
            out.append(f"{head[0]} {head[1]}")
    return list(dict.fromkeys(out))


def parse_cpc(biblio: dict) -> list[dict]:
    out = []
    items = _as_list(_g(biblio, "patent-classifications", "patent-classification"))
    for it in items:
        scheme = _txt(_g(it, "classification-scheme", "@scheme")) or _g(it, "classification-scheme", "@scheme") or ""
        if not scheme:
            scheme = _g(it, "classification-scheme", "@scheme", default="")
        sec = _txt(it.get("section"))
        cls = _txt(it.get("class"))
        sub = _txt(it.get("subclass"))
        mg = _txt(it.get("main-group"))
        sg = _txt(it.get("subgroup"))
        if sec and cls and sub:
            code = f"{sec}{cls}{sub}"
            if mg:
                code += f" {mg}"
                if sg:
                    code += f"/{sg}"
            out.append({"code": code, "scheme": scheme})
    # dedupe per (code,scheme)
    seen = set(); dedup = []
    for c in out:
        k = (c["code"], c["scheme"])
        if k not in seen:
            seen.add(k); dedup.append(c)
    return dedup


def parse_pub_number(biblio: dict) -> tuple[str, str | None]:
    pr = _g(biblio, "publication-reference", "document-id", default=[])
    pr = _as_list(pr)
    docdb = epodoc = None
    for d in pr:
        t = d.get("@document-id-type")
        country = _txt(d.get("country"))
        num = _txt(d.get("doc-number"))
        kind = _txt(d.get("kind"))
        if t == "docdb":
            docdb = f"{country}{num}{kind}"
        elif t == "epodoc":
            epodoc = num
    pubdate = None
    for d in pr:
        if d.get("@document-id-type") == "docdb":
            pubdate = _txt(d.get("date"))
            break
    return (docdb or epodoc or ""), pubdate


def parse_title(biblio: dict) -> dict[str, str]:
    items = _as_list(biblio.get("invention-title"))
    return {it.get("@lang", ""): _txt(it) for it in items}


def parse_applicants(biblio: dict) -> list[str]:
    apps = _as_list(_g(biblio, "parties", "applicants", "applicant"))
    out = []
    for a in apps:
        names = _as_list(a.get("applicant-name"))
        for n in names:
            v = _txt(n.get("name") if isinstance(n.get("name"), dict) else n)
            if v: out.append(v)
    return list(dict.fromkeys(out))


def extract_hits(payload: dict) -> tuple[int, list[dict]]:
    biblio = _g(payload, "ops:world-patent-data", "ops:biblio-search")
    total = int(biblio.get("@total-result-count", 0))
    docs = _as_list(_g(biblio, "ops:search-result", "exchange-documents")) or []
    out = []
    for entry in docs:
        ed = entry.get("exchange-document") if isinstance(entry, dict) else None
        if not ed:
            continue
        b = ed.get("bibliographic-data", {})
        pub_no, pub_date = parse_pub_number(b)
        if not pub_no:
            continue
        out.append({
            "pub_number": pub_no,
            "pub_date": pub_date,
            "country": ed.get("@country"),
            "kind": ed.get("@kind"),
            "family_id": ed.get("@family-id"),
            "title": parse_title(b),
            "applicants": parse_applicants(b),
            "ipc": parse_ipcr(b),
            "cpc": parse_cpc(b),
        })
    return total, out


def already_done() -> set[int]:
    done = set()
    if OUTPUT.exists():
        with OUTPUT.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done.add(int(rec["ott_id"]))
                except Exception:
                    pass
    return done


def main():
    species = []
    with INPUT.open() as f:
        r = csv.DictReader(f, delimiter="\t")
        for row in r:
            species.append(row)
    done = already_done()
    todo = [s for s in species if int(s["ottId"]) not in done]
    print(f"species total={len(species)} already_done={len(done)} todo={len(todo)}")

    token, exp = get_token()
    log = LOG.open("a")

    with OUTPUT.open("a") as out:
        for i, sp in enumerate(todo, 1):
            if time.time() > exp - 30:
                token, exp = get_token()
            name = sp["name"]
            ott_id = int(sp["ottId"])
            query = f'txt="{name}"'
            t0 = time.time()
            try:
                payload, hdrs = search(token, query)
                total, hits = extract_hits(payload)
                tier, per_min = parse_throttle(hdrs)
                rec = {
                    "ott_id": ott_id,
                    "name": name,
                    "ncbi": sp.get("ncbi") or None,
                    "gbif": sp.get("gbif") or None,
                    "total_results": total,
                    "returned": len(hits),
                    "hits": hits,
                }
                out.write(json.dumps(rec) + "\n")
                out.flush()
                msg = f"[{i:>4}/{len(todo)}] {name:<40} total={total:<6} got={len(hits):<3} {time.time()-t0:.1f}s tier={tier}:{per_min}"
                sleep_for = adaptive_sleep(tier, per_min)
            except HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                hdrs = dict(e.response.headers) if e.response is not None else {}
                tier, per_min = parse_throttle(hdrs)
                msg = f"[{i:>4}/{len(todo)}] {name:<40} HTTP {code} tier={tier}:{per_min}"
                if code == 404:
                    rec = {"ott_id": ott_id, "name": name, "ncbi": sp.get("ncbi") or None,
                           "gbif": sp.get("gbif") or None, "total_results": 0,
                           "returned": 0, "hits": []}
                    out.write(json.dumps(rec) + "\n"); out.flush()
                    sleep_for = adaptive_sleep(tier, per_min)
                elif code == 403:
                    # Throttled — back off HARD, do not retry token (token is fine)
                    sleep_for = SLEEP_MAX
                    msg += "  -> backing off"
                elif code == 401:
                    token, exp = get_token()
                    sleep_for = SLEEP_MIN
                else:
                    sleep_for = adaptive_sleep(tier, per_min)
            except RequestException as e:
                msg = f"[{i:>4}/{len(todo)}] {name:<40} REQERR {type(e).__name__}: {e}"
                sleep_for = SLEEP_MAX
            except Exception as e:
                msg = f"[{i:>4}/{len(todo)}] {name:<40} ERROR {type(e).__name__}: {e}"
                sleep_for = SLEEP_MIN
            print(msg)
            log.write(msg + "\n"); log.flush()
            time.sleep(sleep_for)
    log.close()


if __name__ == "__main__":
    main()
