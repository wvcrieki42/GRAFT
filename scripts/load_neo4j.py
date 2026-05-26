#!/usr/bin/env python3
"""Load preprocessed CSVs into Neo4j (database: treeoflife)."""
from __future__ import annotations
import csv
import os
import sys
import time
from pathlib import Path
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = "treeoflife"

DATA = Path("/Users/wimvancriekinge/Library/Application Support/neo4j-desktop/Application/Data/dbmss/dbms-59134f1d-234e-44e9-a33d-2b64ed0c9fdd/import/treeoflife")

BATCH = 20_000


def run(driver, query, **params):
    with driver.session(database=DB) as s:
        return s.run(query, **params).consume()


def schema(driver):
    stmts = [
        "CREATE CONSTRAINT taxon_ott_id IF NOT EXISTS FOR (t:Taxon) REQUIRE t.ottId IS UNIQUE",
        "CREATE INDEX taxon_ncbi IF NOT EXISTS FOR (t:Taxon) ON (t.ncbiTaxId)",
        "CREATE INDEX taxon_gbif IF NOT EXISTS FOR (t:Taxon) ON (t.gbifTaxKey)",
        "CREATE INDEX taxon_name IF NOT EXISTS FOR (t:Taxon) ON (t.name)",
    ]
    with driver.session(database=DB) as s:
        for q in stmts:
            s.run(q).consume()
            print(f"  OK: {q[:70]}")


def stream_batches(path: Path, batch_size: int = BATCH):
    with path.open() as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            batch.append(row)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def load_taxa(driver):
    """Phase A: create Taxon nodes (no edges)."""
    q = """
    UNWIND $rows AS r
    CREATE (t:Taxon {
      ottId: toInteger(r.ottId),
      name: r.name,
      rank: r.rank,
      flags: CASE WHEN r.flags = '' THEN null ELSE r.flags END,
      ncbiTaxId: CASE WHEN r.ncbiTaxId = '' THEN null ELSE toInteger(r.ncbiTaxId) END,
      gbifTaxKey: CASE WHEN r.gbifTaxKey = '' THEN null ELSE toInteger(r.gbifTaxKey) END
    })
    """
    t0 = time.time(); n = 0
    with driver.session(database=DB) as s:
        for batch in stream_batches(DATA / "ott_taxa.csv"):
            s.run(q, rows=batch).consume()
            n += len(batch)
            if n % 500_000 == 0:
                print(f"  taxa: {n:>10,} ({n/(time.time()-t0):,.0f}/s)")
    print(f"Phase A done: {n:,} taxa in {time.time()-t0:.1f}s")


def link_parents(driver):
    """Phase B: create HAS_PARENT edges from parentOttId."""
    q = """
    UNWIND $rows AS r
    WITH r WHERE r.parentOttId <> ''
    MATCH (c:Taxon {ottId: toInteger(r.ottId)})
    MATCH (p:Taxon {ottId: toInteger(r.parentOttId)})
    CREATE (c)-[:HAS_PARENT]->(p)
    """
    t0 = time.time(); n = 0
    with driver.session(database=DB) as s:
        for batch in stream_batches(DATA / "ott_taxa.csv"):
            s.run(q, rows=batch).consume()
            n += len(batch)
            if n % 500_000 == 0:
                print(f"  parents: {n:>10,} ({n/(time.time()-t0):,.0f}/s)")
    print(f"Phase B done: {n:,} rows in {time.time()-t0:.1f}s")


def load_ncbi_commons(driver):
    """Phase C: create Vernacular nodes from NCBI common names, link via ncbiTaxId."""
    q = """
    UNWIND $rows AS r
    MATCH (t:Taxon {ncbiTaxId: toInteger(r.ncbiTaxId)})
    CREATE (v:Vernacular {name: r.name, language: 'en', source: r.nameClass + ' (NCBI)'})
    CREATE (t)-[:HAS_VERNACULAR]->(v)
    """
    t0 = time.time(); n = 0; matched = 0
    with driver.session(database=DB) as s:
        for batch in stream_batches(DATA / "ncbi_commons.csv", batch_size=5_000):
            res = s.run(q, rows=batch).consume()
            n += len(batch)
            matched += res.counters.nodes_created
    print(f"Phase C done: {n:,} input rows, {matched:,} vernaculars created in {time.time()-t0:.1f}s")


def load_gbif_vernacular(driver):
    """Phase D: GBIF Dutch + English vernaculars, link via gbifTaxKey."""
    q = """
    UNWIND $rows AS r
    MATCH (t:Taxon {gbifTaxKey: toInteger(r.gbifTaxKey)})
    CREATE (v:Vernacular {name: r.name, language: r.language, source: 'GBIF: ' + r.source})
    CREATE (t)-[:HAS_VERNACULAR]->(v)
    """
    t0 = time.time(); n = 0; matched = 0
    with driver.session(database=DB) as s:
        for batch in stream_batches(DATA / "gbif_vernacular_nlen.csv", batch_size=5_000):
            res = s.run(q, rows=batch).consume()
            n += len(batch)
            matched += res.counters.nodes_created
    print(f"Phase D done: {n:,} input rows, {matched:,} vernaculars created in {time.time()-t0:.1f}s")


def main():
    phases = sys.argv[1:] or ["schema", "taxa", "parents", "ncbi", "gbif"]
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        driver.verify_connectivity()
        if "schema" in phases:
            print("=== Schema ===")
            schema(driver)
        if "taxa" in phases:
            print("=== Phase A: Taxon nodes ===")
            load_taxa(driver)
        if "parents" in phases:
            print("=== Phase B: HAS_PARENT edges ===")
            link_parents(driver)
        if "ncbi" in phases:
            print("=== Phase C: NCBI commons ===")
            load_ncbi_commons(driver)
        if "gbif" in phases:
            print("=== Phase D: GBIF vernaculars (nl+en) ===")
            load_gbif_vernacular(driver)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
