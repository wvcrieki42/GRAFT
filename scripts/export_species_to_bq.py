#!/usr/bin/env python3
"""Export all OTT species (rank='species') from Neo4j to a CSV, then load it
into BigQuery as table treeoflife-2026:treeoflife.species.

We restrict to rank='species' (not subspecies, varieties, etc.) and to names
that look like proper binomials (two lowercase Latin words after lowercasing)
to make the downstream JOIN against extracted candidates clean.
"""
from __future__ import annotations
import csv
import os
import re
import time
import subprocess
from pathlib import Path
from neo4j import GraphDatabase

ROOT = Path("/Users/wimvancriekinge/treeoflife")
OUT_CSV = ROOT / "data" / "ott_species.csv"
PROJECT = "treeoflife-2026"
DATASET = "treeoflife"
TABLE = "species"

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = os.environ.get("NEO4J_DB", "treeoflife")

BINOMIAL_RE = re.compile(r"^[a-z]+ [a-z]+$")  # exactly two lowercase words after .lower()


def export():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    t0 = time.time()
    n_total = n_kept = 0
    with driver.session(database=DB) as s, OUT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ott_id", "name"])
        result = s.run("""
            MATCH (t:Taxon {rank: 'species'})
            RETURN t.ottId AS ott_id, t.name AS name
        """)
        for r in result:
            n_total += 1
            name = (r["name"] or "").strip()
            if not name:
                continue
            # Keep only proper-binomial-shaped names (filters subspecies trinomials,
            # names with parentheses, hybrid '×', authority suffixes, etc.)
            if not BINOMIAL_RE.match(name.lower()):
                continue
            w.writerow([r["ott_id"], name])
            n_kept += 1
            if n_kept % 200000 == 0:
                print(f"  exported: {n_kept:,} ({n_kept/(time.time()-t0):,.0f}/s)")
    driver.close()
    print(f"total species seen: {n_total:,}; binomial-shaped kept: {n_kept:,}")
    print(f"file: {OUT_CSV} ({OUT_CSV.stat().st_size/1e6:.1f} MB)")
    return n_kept


def upload():
    """Use the Python BigQuery client (which already has ADC working) to create
    the dataset and load the CSV."""
    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)

    # Create dataset if missing
    dataset_ref = bigquery.Dataset(f"{PROJECT}.{DATASET}")
    dataset_ref.location = "US"
    try:
        client.create_dataset(dataset_ref, exists_ok=True)
        print(f"dataset ready: {PROJECT}:{DATASET}")
    except Exception as e:
        print(f"dataset create warn: {e}")

    table_id = f"{PROJECT}.{DATASET}.{TABLE}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[
            bigquery.SchemaField("ott_id", "INT64"),
            bigquery.SchemaField("name", "STRING"),
        ],
    )
    print(f"uploading {OUT_CSV} -> {table_id} ...")
    with OUT_CSV.open("rb") as f:
        job = client.load_table_from_file(f, table_id, job_config=job_config)
    job.result()
    table = client.get_table(table_id)
    print(f"loaded {table.num_rows:,} rows ({table.num_bytes/1e6:.1f} MB)")


def main():
    n = export()
    if n == 0:
        raise SystemExit("no species exported")
    upload()
    print("done")


if __name__ == "__main__":
    main()
