#!/usr/bin/env python3
"""Convert OTT/NCBI/GBIF source files into clean CSVs ready for LOAD CSV."""
from pathlib import Path
import csv
import sys
import time

DATA = Path("/Users/wimvancriekinge/treeoflife/data")
OUT = Path("/Users/wimvancriekinge/Library/Application Support/neo4j-desktop/Application/Data/dbmss/dbms-59134f1d-234e-44e9-a33d-2b64ed0c9fdd/import/treeoflife")
OUT.mkdir(parents=True, exist_ok=True)


def parse_sourceinfo(s):
    ncbi = gbif = None
    for token in s.split(","):
        if ":" not in token:
            continue
        src, sid = token.split(":", 1)
        if src == "ncbi" and ncbi is None:
            ncbi = sid
        elif src == "gbif" and gbif is None:
            gbif = sid
    return ncbi, gbif


def ott_taxonomy():
    src = DATA / "ott3.7.3" / "taxonomy.tsv"
    dst = OUT / "ott_taxa.csv"
    t0 = time.time()
    with src.open() as f, dst.open("w", newline="") as g:
        w = csv.writer(g)
        w.writerow(["ottId", "parentOttId", "name", "rank", "flags", "ncbiTaxId", "gbifTaxKey"])
        header = f.readline()
        n = 0
        for line in f:
            parts = line.rstrip("\n").split("\t|\t")
            if len(parts) < 7:
                continue
            uid, parent, name, rank, sourceinfo, uniqname, flags = parts[:7]
            ncbi, gbif = parse_sourceinfo(sourceinfo)
            w.writerow([uid, parent, name, rank, flags.rstrip("\t|"), ncbi or "", gbif or ""])
            n += 1
    print(f"OTT taxa: {n} rows in {time.time()-t0:.1f}s -> {dst}")


def ncbi_commons():
    """Extract English common names from NCBI names.dmp."""
    src = DATA / "ncbi" / "names.dmp"
    dst = OUT / "ncbi_commons.csv"
    t0 = time.time()
    KEEP = {"genbank common name", "common name"}
    n = 0
    with src.open() as f, dst.open("w", newline="") as g:
        w = csv.writer(g)
        w.writerow(["ncbiTaxId", "name", "nameClass"])
        for line in f:
            parts = line.rstrip("\n").rstrip("\t|").split("\t|\t")
            if len(parts) < 4:
                continue
            taxid, name, _unique, nclass = parts[0], parts[1], parts[2], parts[3]
            if nclass in KEEP:
                w.writerow([taxid, name, nclass])
                n += 1
    print(f"NCBI commons: {n} rows in {time.time()-t0:.1f}s -> {dst}")


def gbif_vernacular():
    """Filter GBIF vernacular to nl + en."""
    src = DATA / "VernacularName.tsv"
    dst = OUT / "gbif_vernacular_nlen.csv"
    t0 = time.time()
    n_nl = n_en = 0
    with src.open() as f, dst.open("w", newline="") as g:
        w = csv.writer(g)
        w.writerow(["gbifTaxKey", "name", "language", "source"])
        f.readline()
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            taxid, name, lang = parts[0], parts[1], parts[2]
            source = parts[7] if len(parts) > 7 else ""
            if lang == "nl":
                w.writerow([taxid, name, "nl", source]); n_nl += 1
            elif lang == "en":
                w.writerow([taxid, name, "en", source]); n_en += 1
    print(f"GBIF vernacular: nl={n_nl} en={n_en} in {time.time()-t0:.1f}s -> {dst}")


if __name__ == "__main__":
    ott_taxonomy()
    ncbi_commons()
    gbif_vernacular()
    print("\nOutput dir:", OUT)
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}\t{p.stat().st_size:>12,} bytes")
