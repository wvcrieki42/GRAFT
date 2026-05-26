#!/usr/bin/env python3
"""Single-pass BigQuery scan for binomial mentions in title+abstract of every
patent in `patents-public-data.patents.publications`. Replaces the OPS top-25
sample with a uniform corpus.

Strategy:
  - Build one big alternation regex of all species names.
  - In one CTE, filter the corpus to patents whose title|abstract matches the
    union regex (this is the only "expensive" scan — ~255 GB).
  - In the outer SELECT, REGEXP_EXTRACT_ALL emits a row per (patent, matched
    species), giving the exploded species→patent edge list directly.

Output: data/bq_hits.jsonl, one record per (species, patent) pair.
"""
from __future__ import annotations
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

from google.cloud import bigquery

ROOT = Path("/Users/wimvancriekinge/treeoflife")
INPUT = ROOT / "data" / "top_1k_species.tsv"
OUTPUT = ROOT / "data" / "bq_hits.jsonl"
PROJECT = "treeoflife-2026"


def load_species():
    species = []
    with INPUT.open() as f:
        for r in csv.DictReader(f, delimiter="\t"):
            species.append({
                "ott_id": int(r["ottId"]),
                "name": r["name"].strip(),
                "ncbi": r.get("ncbi") or None,
                "gbif": r.get("gbif") or None,
            })
    return species


def build_regex(names):
    # Lowercase + escape; require word boundaries on both sides so "Acer" doesn't
    # match "Aceraceae". Names containing spaces need no extra escaping beyond
    # re.escape, since BigQuery's RE2 honours \b.
    pat = "|".join(re.escape(n.lower()) for n in names)
    return r"\b(" + pat + r")\b"


def build_query(big_regex: str) -> str:
    return f"""
DECLARE big_regex STRING DEFAULT r'{big_regex}';

WITH matched AS (
  SELECT
    p.publication_number,
    p.country_code,
    p.kind_code,
    p.family_id,
    p.publication_date,
    p.filing_date,
    (SELECT t.text FROM UNNEST(p.title_localized) t WHERE t.language='en' LIMIT 1) AS title_en,
    p.assignee AS applicants,
    ARRAY(SELECT c.code FROM UNNEST(p.ipc) c) AS ipc_codes,
    ARRAY(SELECT c.code FROM UNNEST(p.cpc) c) AS cpc_codes,
    LOWER(CONCAT(
      IFNULL((SELECT t.text FROM UNNEST(p.title_localized) t    WHERE t.language='en' LIMIT 1), ''),
      ' ',
      IFNULL((SELECT a.text FROM UNNEST(p.abstract_localized) a WHERE a.language='en' LIMIT 1), '')
    )) AS searchtext
  FROM `patents-public-data.patents.publications` p
  WHERE
    REGEXP_CONTAINS(
      LOWER(IFNULL((SELECT t.text FROM UNNEST(p.title_localized) t    WHERE t.language='en' LIMIT 1), '')),
      big_regex
    )
    OR REGEXP_CONTAINS(
      LOWER(IFNULL((SELECT a.text FROM UNNEST(p.abstract_localized) a WHERE a.language='en' LIMIT 1), '')),
      big_regex
    )
)

SELECT
  matched_species,
  publication_number,
  country_code,
  kind_code,
  family_id,
  publication_date,
  filing_date,
  title_en,
  applicants,
  ipc_codes,
  cpc_codes
FROM matched, UNNEST(REGEXP_EXTRACT_ALL(searchtext, big_regex)) AS matched_species
"""


def main():
    species = load_species()
    print(f"loaded {len(species)} species")

    name_to_meta = {s["name"].lower(): s for s in species}
    big_regex = build_regex([s["name"] for s in species])
    print(f"regex length: {len(big_regex)/1024:.1f} KB ({len(species)} alternations)")

    sql = build_query(big_regex)

    client = bigquery.Client(project=PROJECT)

    if "--dry" in sys.argv:
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False))
        print(f"dry run: {job.total_bytes_processed/1e9:.2f} GB")
        return

    print("submitting query (may take 2–5 min)...")
    t0 = time.time()
    job = client.query(sql)
    iterator = job.result()
    elapsed = time.time() - t0
    print(f"  scanned: {job.total_bytes_processed/1e9:.2f} GB, billed: {job.total_bytes_billed/1e9:.2f} GB")
    print(f"  query elapsed: {elapsed:.1f}s")

    n = 0
    with OUTPUT.open("w") as out:
        for row in iterator:
            sp = name_to_meta.get(row["matched_species"])
            if not sp:
                continue
            rec = {
                "ott_id": sp["ott_id"],
                "species_name": sp["name"],
                "ncbi": sp["ncbi"],
                "gbif": sp["gbif"],
                "publication_number": row["publication_number"],
                "country_code": row["country_code"],
                "kind_code": row["kind_code"],
                "family_id": row["family_id"],
                "publication_date": row["publication_date"],
                "filing_date": row["filing_date"],
                "title_en": row["title_en"],
                "applicants": list(row["applicants"]) if row["applicants"] else [],
                "ipc_codes": list(row["ipc_codes"]) if row["ipc_codes"] else [],
                "cpc_codes": list(row["cpc_codes"]) if row["cpc_codes"] else [],
            }
            out.write(json.dumps(rec) + "\n")
            n += 1
            if n % 50000 == 0:
                print(f"  rows written: {n:,}")
    print(f"done: {n:,} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
