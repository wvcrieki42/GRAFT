#!/usr/bin/env python3
"""Load OPS search results (ops_hits.jsonl) into Neo4j db `treeoflife`.

Schema:
  (Taxon)-[:HAS_PATENT]->(Patent {pubNumber, familyId, country, kind, pubDate, titleEn})
  (Patent)-[:HAS_IPC]->(IPCClass {code})
  (Patent)-[:HAS_CPC]->(CPCClass {code, scheme})
  (Patent)-[:FILED_BY]->(Applicant {name})
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
from neo4j import GraphDatabase

ROOT = Path("/Users/wimvancriekinge/treeoflife")
HITS = ROOT / "data" / "ops_hits.jsonl"

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = os.environ.get("NEO4J_DB", "treeoflife")

BATCH = 500


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
            print(f"  OK: {q[:80]}")


LOAD_QUERY = """
UNWIND $rows AS row
MATCH (t:Taxon {ottId: row.ott_id})
UNWIND row.hits AS h
MERGE (p:Patent {pubNumber: h.pub_number})
ON CREATE SET p.country = h.country,
              p.kind = h.kind,
              p.familyId = h.family_id,
              p.pubDate = h.pub_date,
              p.title = h.title_en,
              p.titleLangs = h.title_langs,
              p.titleTexts = h.title_texts
MERGE (t)-[:HAS_PATENT]->(p)
WITH p, h
FOREACH (code IN h.ipc |
  MERGE (c:IPCClass {code: code})
  MERGE (p)-[:HAS_IPC]->(c)
)
FOREACH (cpc IN h.cpc |
  MERGE (c:CPCClass {code: cpc.code})
  ON CREATE SET c.scheme = cpc.scheme
  MERGE (p)-[:HAS_CPC]->(c)
)
"""


def normalize_hit(h: dict) -> dict:
    titles = h.get("title") or {}
    title_en = titles.get("en") or next(iter(titles.values()), "")
    return {
        "pub_number": h["pub_number"],
        "country": h.get("country"),
        "kind": h.get("kind"),
        "family_id": h.get("family_id"),
        "pub_date": h.get("pub_date"),
        "title_en": title_en,
        "title_langs": list(titles.keys()),
        "title_texts": list(titles.values()),
        "ipc": h.get("ipc") or [],
        "cpc": h.get("cpc") or [],
    }


def load(driver):
    rows = []
    n_species = n_hits = 0
    skipped_no_taxon = 0
    with HITS.open() as f:
        for line in f:
            rec = json.loads(line)
            hits = rec.get("hits") or []
            if not hits:
                continue
            rows.append({
                "ott_id": int(rec["ott_id"]),
                "hits": [normalize_hit(h) for h in hits],
            })
            n_species += 1
            n_hits += len(hits)
            if len(rows) >= BATCH:
                _flush(driver, rows)
                rows = []
        if rows:
            _flush(driver, rows)
    print(f"loaded: {n_species} species records, {n_hits} hit-rows (some patents shared across species)")


def _flush(driver, rows):
    t0 = time.time()
    with driver.session(database=DB) as s:
        s.run(LOAD_QUERY, rows=rows).consume()
    print(f"  batch: {len(rows)} species in {time.time()-t0:.1f}s")


def main():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        driver.verify_connectivity()
        print("=== schema ===")
        schema(driver)
        print("=== load ===")
        load(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
