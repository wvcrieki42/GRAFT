#!/usr/bin/env python3
"""Full-tree BigQuery scan: for ALL 2.5M binomial-shaped species in OTT,
find every patent in patents-public-data.patents.publications whose English
title or abstract mentions the binomial.

Strategy: extract-then-join.
  - For each patent, REGEXP_EXTRACT_ALL emits every 2-word lowercase phrase
    in the title+abstract as a candidate row.
  - INNER JOIN those candidates against treeoflife-2026.treeoflife.species.
  - The JOIN survives only known-species candidates; everything else (author
    names, common-language phrases, technical terms) is filtered out cleanly.

Output is written directly to a BigQuery destination table to avoid streaming
millions of rows back to the client. We then EXPORT that table to a local
JSONL via the BQ client.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from google.cloud import bigquery

PROJECT = "treeoflife-2026"
DATASET = "treeoflife"
DEST_TABLE = f"{PROJECT}.{DATASET}.species_patents_full"
SPECIES_TABLE = f"{PROJECT}.{DATASET}.species"

ROOT = Path("/Users/wimvancriekinge/treeoflife")
OUT_JSONL = ROOT / "data" / "bq_hits_full.jsonl"


SQL = f"""
WITH species AS (
  SELECT ott_id, name, LOWER(name) AS lname
  FROM `{SPECIES_TABLE}`
)
SELECT
  s.ott_id,
  s.name AS species_name,
  p.publication_number,
  p.country_code,
  p.kind_code,
  p.family_id,
  p.publication_date,
  p.filing_date,
  (SELECT t.text FROM UNNEST(p.title_localized) t WHERE t.language='en' LIMIT 1) AS title_en,
  p.assignee AS applicants,
  ARRAY(SELECT c.code FROM UNNEST(p.ipc) c) AS ipc_codes,
  ARRAY(SELECT c.code FROM UNNEST(p.cpc) c) AS cpc_codes
FROM `patents-public-data.patents.publications` p,
  UNNEST(
    REGEXP_EXTRACT_ALL(
      LOWER(CONCAT(
        IFNULL((SELECT t.text FROM UNNEST(p.title_localized) t    WHERE t.language='en' LIMIT 1), ''),
        ' ',
        IFNULL((SELECT a.text FROM UNNEST(p.abstract_localized) a WHERE a.language='en' LIMIT 1), '')
      )),
      r'\\b[a-z]+ [a-z]+\\b'
    )
  ) AS candidate
JOIN species s ON s.lname = candidate
"""


def main():
    client = bigquery.Client(project=PROJECT)

    if "--dry" in sys.argv:
        job = client.query(SQL, job_config=bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False))
        print(f"dry run scan: {job.total_bytes_processed/1e9:.1f} GB")
        return

    print("submitting full-tree scan (writes to destination table)…")
    job_config = bigquery.QueryJobConfig(
        destination=DEST_TABLE,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        allow_large_results=True,
    )
    t0 = time.time()
    job = client.query(SQL, job_config=job_config)
    job.result()
    elapsed = time.time() - t0
    print(f"  scan: {job.total_bytes_processed/1e9:.1f} GB in {elapsed:.1f}s")

    # how many rows landed?
    table = client.get_table(DEST_TABLE)
    print(f"  destination rows: {table.num_rows:,}  size: {table.num_bytes/1e9:.2f} GB")

    # stream the destination table to a local JSONL
    print(f"streaming {DEST_TABLE} -> {OUT_JSONL}")
    n = 0
    t1 = time.time()
    with OUT_JSONL.open("w") as out:
        for row in client.list_rows(DEST_TABLE):
            rec = {
                "ott_id": row["ott_id"],
                "species_name": row["species_name"],
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
            if n % 100000 == 0:
                print(f"  streamed {n:,} rows ({n/(time.time()-t1):,.0f}/s)")
    print(f"  total: {n:,} rows -> {OUT_JSONL}")


if __name__ == "__main__":
    main()
