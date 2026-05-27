# GRAFT — Graph of Relatedness, Applications, Families and Taxonomy

GRAFT is a Neo4j knowledge graph that grafts the global patent literature onto the **Open Tree of Life** phylogeny, with multilingual common names attached, to study the phylogenetic signal of human technological interest in biodiversity. The Neo4j database name (`treeoflife`) is kept for path-compatibility with the scripts and the manuscript supplement.

> **Status:** research project, May 2026. Pipeline reproducible; manuscript (with Supplementary Information) in `manuscript/paper.pdf`.
> **Author:** Wim Van Criekinge (UGent) · ORCID [0000-0003-2971-5539](https://orcid.org/0000-0003-2971-5539).
> This is the public companion repository to the manuscript.

## What's in the graph

| Layer | Source | Size |
|---|---|---|
| **Taxonomy** | Open Tree Taxonomy 3.7.3 (2025-12) | 4,529,570 Taxon nodes · 4,529,569 `HAS_PARENT` edges |
| **Vernacular names** | NCBI taxdump (en) + GBIF backbone (en, nl) | 524,609 Vernacular nodes (479,194 en · 45,415 nl) |
| **Patents** | Google Patents BigQuery (`patents-public-data.patents.publications`) | 759,182 Patent nodes · 1,562,854 `HAS_PATENT` edges across 22,876 species |
| **IPC / CPC classes** | WIPO IPC 2024.01 · EPO/USPTO CPC 2026.05 | 20,746 IPC · 35,139 CPC (100% of definitions resolved) |

## Schema

```
(:Taxon {ottId, name, rank, flags, ncbiTaxId, gbifTaxKey})
  -[:HAS_PARENT]-> (:Taxon)
  -[:HAS_VERNACULAR]-> (:Vernacular {name, language, source})
  -[:HAS_PATENT]-> (:Patent {pubNumber, country, kind, familyId, pubDate, title, ...})

(:Patent)-[:HAS_IPC]-> (:IPCClass {code, definition, section, ...})
(:Patent)-[:HAS_CPC]-> (:CPCClass {code, scheme, definition, section, ...})
```

Unique constraints on `Taxon.ottId`, `Patent.pubNumber`, `IPCClass.code`, `CPCClass.code`. Indexes on `Taxon.ncbiTaxId`, `Taxon.gbifTaxKey`, `Taxon.name`, `Patent.familyId`, `Patent.country`.

## Key results

- **Phylogenetic signal in patent applications** (Mantel test, full-tree BigQuery corpus, *n* = 9,944 species ≥5 patents, 49.4 M pairs): global Pearson **r = +0.188** (one-sided *p* = 0.001), Bonferroni-significant in every close-distance bin from sister-species through within-class. Closely related species are non-randomly used for the same kinds of biotechnology.
- **Branch-length validation** (TimeTree of Life v5, divergence-time distances, *n* = 5,306 species, 14.1 M pairs): global **r = +0.103** (*p* = 0.001), Bonferroni-significant in every divergence-time bin **out to ~500 Myr**, peaking at 100–250 Myr; the signal flips positive (cross-kingdom dilution) for deeper splits. A topological control on the identical subtree gives **r = +0.154**, so the result does not depend on whether phylogenetic distance is measured as topology or as evolutionary time.

See `manuscript/paper.pdf` (Figures 3–4, Tables 1, S5–S6) for full detail.

## Prerequisites

- Neo4j 2026.04+ (Desktop or Server). APOC **not** required.
- Python 3.11+ with `neo4j`, `numpy`, `scipy`, `matplotlib`, `dendropy`.
- For the patent layer: a Google Cloud project with BigQuery enabled (~3 TB/month free-tier budget; one full scan ≈ 258 GB).

## Quickstart

```bash
git clone https://github.com/wvcrieki42/GRAFT.git
cd GRAFT
cp .env.example .env   # fill in NEO4J_PASSWORD, GCP_PROJECT, OPS_KEY/OPS_SECRET

# taxonomy + vernaculars (≈ 30 min)
python scripts/preprocess.py
python scripts/load_neo4j.py

# patent overlay
python scripts/export_species_to_bq.py     # export binomial species to a BigQuery table
python scripts/bq_search_full.py           # single full-tree extract-then-join scan
python scripts/resolve_classes.py          # attach IPC/CPC definitions
python scripts/load_patents_bq.py          # idempotent MERGE into Neo4j

# statistics
python scripts/mantel_analysis.py          # topological Mantel + correlogram
python scripts/timetree_coverage.py        # match species to TimeTree of Life
python scripts/build_timetree_distmat.py   # prune TToL5, build divergence matrix
python scripts/build_cpc_profiles.py       # reconstruct CPC profiles from BQ hits
python scripts/mantel_branchlength.py      # branch-length (divergence-time) Mantel
python scripts/topo_check.py               # topological control on the same subtree
```

## Repo layout

```
scripts/
  preprocess.py            # parse OTT + NCBI + GBIF, emit normalized TSVs
  load_neo4j.py            # taxonomy + vernacular ingest
  ops_search.py            # EPO OPS biblio search (legacy backend, rate-limited)
  bq_search.py             # BigQuery scan, short-list pilot
  bq_search_full.py        # BigQuery full-tree extract-then-join scan
  load_patents.py          # patent ingest from OPS hits
  load_patents_bq.py       # patent ingest from BigQuery hits
  resolve_classes.py       # IPC/CPC code → definition lookup
  export_species_to_bq.py  # upload binomial species table to BigQuery
  mantel_analysis.py       # topological phylogenetic-signal statistics
  timetree_coverage.py     # species → TimeTree of Life v5 matching
  build_timetree_distmat.py# prune TToL5, verify ultrametricity, build matrix
  build_cpc_profiles.py    # reconstruct per-species CPC profiles from BQ hits
  mantel_branchlength.py   # branch-length (divergence-time) Mantel + correlogram
  topo_check.py            # topological control on the TToL5 subtree
  build_figures.py         # manuscript figures
  resume_pipeline.sh       # resumable pipeline wrapper

data/                      # small reproducibility artefacts (results JSON, figures,
                           # matched-species lists, pruned TToL5 tree, CPC profiles).
                           # Large raw inputs (BigQuery hits, source dumps) are not
                           # tracked — see Data availability in the manuscript.
webapp/                    # FastAPI + D3 interactive browser of the graph
manuscript/                # paper.pdf, paper.html
```

## Backend notes (lessons worth keeping)

- **OPS free tier is much tighter than the docs imply.** Sustained >30 req/min escalates green→yellow→orange→red→**black** (full `/search` ban, observed `retry-after: ~10 days`). Watch `x-throttling-control` after every response; `ops_search.py` reads it adaptively. BigQuery is the right primary backend at >1k species.
- **BigQuery full-tree scan** uses an extract-then-join architecture (`REGEXP_EXTRACT_ALL` of two-word phrases, INNER JOIN against a 2.5 M-row species table) — scan cost is column-read-bound (~258 GB) regardless of species count, sidestepping the RE2 regex-size limit.
- **OTT's `sourceinfo`** already contains NCBI and GBIF IDs (`ncbi:9606,gbif:2436436`), so no fuzzy name matching is needed at the bridge layer.
- **TimeTree of Life v5** tips are MEGA-TT integer IDs (not NCBI taxIDs); species are matched by name via the distributed name map.

## License

[MIT](LICENSE) for the code. Data layers carry their source licenses regardless (OTT: CC0; NCBI taxdump: public domain; GBIF backbone: CC-BY-4.0; TimeTree of Life: see MEGA-TT; Google Patents Public Data: subject to its [terms](https://console.cloud.google.com/marketplace/product/google_patents_public_datasets/google-patents-public-data)).

## Contact

Wim Van Criekinge — wim.vancriekinge@ugent.be
