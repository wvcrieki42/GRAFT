#!/usr/bin/env python3
"""Load BigQuery patent hits (data/bq_hits.jsonl) into Neo4j.

Replaces the previous OPS-based loader. Differences from load_patents.py:
  - Input is one row per (species, patent) edge (BigQuery exploded format).
  - IPC/CPC codes from BigQuery are no-space (e.g. 'A23K50/80'). We normalize
    to the 'A23K 50/80' form to match the existing scheme-resolver logic.
  - publication_number includes hyphens ('EP-4733385-A1'); we keep that.
  - publication_date and filing_date are integers (YYYYMMDD); stored as such.
"""
from __future__ import annotations
import csv
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from neo4j import GraphDatabase

ROOT = Path("/Users/wimvancriekinge/treeoflife")
HITS = ROOT / "data" / ("bq_hits_full.jsonl" if (ROOT / "data" / "bq_hits_full.jsonl").exists() else "bq_hits.jsonl")

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = os.environ.get("NEO4J_DB", "treeoflife")

BATCH = 500


CODE_RE = re.compile(r"^([A-HY]\d{2}[A-Z])(\d.*)$")

def normalize_code(c: str) -> str | None:
    """Normalize BigQuery 'A23K50/80' → 'A23K 50/80' (matches scheme-file form)."""
    if not c: return None
    c = c.strip()
    m = CODE_RE.match(c)
    if m: return f"{m.group(1)} {m.group(2)}"
    return c


def schema(driver):
    stmts = [
        "CREATE CONSTRAINT patent_pub IF NOT EXISTS FOR (p:Patent) REQUIRE p.pubNumber IS UNIQUE",
        "CREATE CONSTRAINT ipc_code IF NOT EXISTS FOR (c:IPCClass) REQUIRE c.code IS UNIQUE",
        "CREATE CONSTRAINT cpc_code IF NOT EXISTS FOR (c:CPCClass) REQUIRE c.code IS UNIQUE",
        "CREATE INDEX patent_family IF NOT EXISTS FOR (p:Patent) ON (p.familyId)",
        "CREATE INDEX patent_country IF NOT EXISTS FOR (p:Patent) ON (p.country)",
    ]
    with driver.session(database=DB) as s:
        for q in stmts:
            s.run(q).consume()
    print("schema OK")


LOAD_QUERY = """
UNWIND $rows AS row
MATCH (t:Taxon {ottId: row.ott_id})
MERGE (p:Patent {pubNumber: row.pub_number})
ON CREATE SET p.country = row.country,
              p.kind = row.kind,
              p.familyId = row.family_id,
              p.pubDate = row.pub_date,
              p.filingDate = row.filing_date,
              p.title = row.title,
              p.applicants = row.applicants
MERGE (t)-[:HAS_PATENT]->(p)
FOREACH (code IN row.ipc |
  MERGE (c:IPCClass {code: code})
  MERGE (p)-[:HAS_IPC]->(c)
)
FOREACH (code IN row.cpc |
  MERGE (c:CPCClass {code: code})
  MERGE (p)-[:HAS_CPC]->(c)
)
"""


def stream_rows():
    with HITS.open() as f:
        for line in f:
            r = json.loads(line)
            ipc = sorted({normalize_code(c) for c in (r.get("ipc_codes") or []) if c})
            cpc = sorted({normalize_code(c) for c in (r.get("cpc_codes") or []) if c})
            yield {
                "ott_id": int(r["ott_id"]),
                "pub_number": r["publication_number"],
                "country": r.get("country_code"),
                "kind": r.get("kind_code"),
                "family_id": r.get("family_id"),
                "pub_date": r.get("publication_date"),
                "filing_date": r.get("filing_date"),
                "title": r.get("title_en") or "",
                "applicants": r.get("applicants") or [],
                "ipc": [c for c in ipc if c],
                "cpc": [c for c in cpc if c],
            }


def load(driver):
    n_rows = 0
    t0 = time.time()
    batch = []
    with driver.session(database=DB) as s:
        for row in stream_rows():
            batch.append(row)
            if len(batch) >= BATCH:
                s.run(LOAD_QUERY, rows=batch).consume()
                n_rows += len(batch)
                if n_rows % 10000 == 0:
                    print(f"  rows: {n_rows:>8,}  ({n_rows/(time.time()-t0):,.0f}/s)")
                batch = []
        if batch:
            s.run(LOAD_QUERY, rows=batch).consume()
            n_rows += len(batch)
    print(f"loaded {n_rows:,} (species, patent) rows in {time.time()-t0:.1f}s")


def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        driver.verify_connectivity()
        schema(driver)
        load(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
