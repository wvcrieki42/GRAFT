"""FastAPI service that exposes the treeoflife Neo4j graph to a browser UI.

Run locally:
    uvicorn webapp.main:app --reload --port 8000

Then open http://localhost:8000 in your browser.

The service expects NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DB in
the project's .env (already populated for the rest of the pipeline).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from neo4j import GraphDatabase
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
NEO4J_DB = os.environ.get("NEO4J_DB", "treeoflife")

if not NEO4J_PASSWORD:
    raise RuntimeError("NEO4J_PASSWORD not set; populate .env first")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


app = FastAPI(title="treeoflife browser")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------
# /api/taxon/search — autocomplete on Taxon.name (prefix match, uses index)
# --------------------------------------------------------------------------
@app.get("/api/taxon/search")
def taxon_search(q: str = Query(min_length=2), limit: int = 12):
    q_norm = q.strip()
    if not q_norm:
        return {"results": []}
    cypher = (
        "MATCH (t:Taxon) "
        "WHERE t.name STARTS WITH $q "
        "RETURN t.ottId AS ottId, t.name AS name, t.rank AS rank "
        "LIMIT $limit"
    )
    with driver.session(database=NEO4J_DB) as s:
        rows = s.run(cypher, q=q_norm, limit=limit).data()
    return {"results": rows}


# --------------------------------------------------------------------------
# /api/cpc — list CPC subclasses used in the graph, with their patent count
# --------------------------------------------------------------------------
@app.get("/api/cpc")
def cpc_list(limit: int = 250):
    cypher = (
        "MATCH (c:CPCClass)<-[:HAS_CPC]-(p:Patent)<-[:HAS_PATENT]-(t:Taxon {rank:'species'}) "
        "WITH c.subclassCode AS subclass, c.subclassTitle AS title, "
        "     count(DISTINCT p) AS n_patents, count(DISTINCT t) AS n_species "
        "WHERE subclass IS NOT NULL "
        "RETURN subclass, title, n_patents, n_species "
        "ORDER BY n_patents DESC LIMIT $limit"
    )
    with driver.session(database=NEO4J_DB) as s:
        rows = s.run(cypher, limit=limit).data()
    return {"results": rows}


# --------------------------------------------------------------------------
# /api/tree — subtree rooted at `ott`, filtered to species with a patent in
# `cpc` (a CPC subclass code). At most `limit` leaves are returned; for each
# leaf we attach up to `patents_per_leaf` patent titles + Google Patents URLs
# so the frontend tooltip is instant (no extra round-trips).
# --------------------------------------------------------------------------
@app.get("/api/tree")
def tree(
    ott: int = Query(..., description="Root taxon ottId"),
    cpc: Optional[str] = Query(None, description="Optional CPC subclass filter (e.g. A61K)"),
    limit: int = Query(300, ge=1, le=1500),
    patents_per_leaf: int = Query(6, ge=1, le=20),
):
    # 1. Confirm the root exists and grab its display info
    with driver.session(database=NEO4J_DB) as s:
        root_rec = s.run(
            "MATCH (r:Taxon {ottId:$ott}) RETURN r.ottId AS ottId, r.name AS name, r.rank AS rank",
            ott=ott,
        ).single()
    if not root_rec:
        raise HTTPException(404, f"No Taxon with ottId={ott}")

    # 2. Find descendant species that have a patent (filtered by CPC if requested),
    # ranked by patent-edge count. Then walk each leaf's lineage back to the root and
    # collect a handful of patent titles per leaf, all in one Cypher round-trip.
    if cpc:
        leaf_match = (
            "MATCH (leaf)-[:HAS_PATENT]->(pat:Patent) "
            "WHERE exists { (pat)-[:HAS_CPC]->(:CPCClass {subclassCode:$cpc}) } "
        )
        patent_match = (
            "MATCH (leaf)-[:HAS_PATENT]->(pp:Patent) "
            "WHERE exists { (pp)-[:HAS_CPC]->(:CPCClass {subclassCode:$cpc}) } "
            "WITH leaf, n_edges, lineage, english, pp ORDER BY pp.pubDate DESC "
            "WITH leaf, n_edges, lineage, english, "
            "collect({pubNumber: pp.pubNumber, country: pp.country, "
            "         title: pp.title, pubDate: pp.pubDate})[..$ppl] AS patents "
        )
    else:
        leaf_match = "MATCH (leaf)-[:HAS_PATENT]->(pat:Patent) "
        patent_match = (
            "MATCH (leaf)-[:HAS_PATENT]->(pp:Patent) "
            "WITH leaf, n_edges, lineage, english, pp ORDER BY pp.pubDate DESC "
            "WITH leaf, n_edges, lineage, english, "
            "collect({pubNumber: pp.pubNumber, country: pp.country, "
            "         title: pp.title, pubDate: pp.pubDate})[..$ppl] AS patents "
        )

    cypher = (
        "MATCH (root:Taxon {ottId:$ott})<-[:HAS_PARENT*0..]-(leaf:Taxon {rank:'species'}) "
        + leaf_match +
        "WITH leaf, count(DISTINCT pat) AS n_edges "
        "ORDER BY n_edges DESC LIMIT $limit "
        # Walk lineage up to root
        "MATCH p = (leaf)-[:HAS_PARENT*0..]->(:Taxon {ottId:$ott}) "
        "WITH leaf, n_edges, "
        "     [n IN nodes(p) | {ottId:n.ottId, name:n.name, rank:n.rank}] AS lineage "
        # Attach English vernacular if any
        "OPTIONAL MATCH (leaf)-[:HAS_VERNACULAR]->(v:Vernacular) "
        "WHERE v.language = 'en' "
        "WITH leaf, n_edges, lineage, head(collect(v.name)) AS english "
        + patent_match +
        "RETURN leaf.ottId AS leafId, leaf.name AS leafName, leaf.rank AS leafRank, "
        "       english, n_edges, lineage, patents"
    )

    with driver.session(database=NEO4J_DB) as s:
        rows = s.run(cypher, ott=ott, cpc=cpc, limit=limit, ppl=patents_per_leaf).data()

    if not rows:
        return {
            "root": dict(root_rec),
            "tree": {"ottId": root_rec["ottId"], "name": root_rec["name"],
                     "rank": root_rec["rank"], "children": []},
            "n_leaves": 0,
            "filter": {"cpc": cpc, "limit": limit},
            "truncated": False,
        }

    # 3. Build a tree in Python from the list of (lineage, leaf-patents) tuples.
    # Each lineage is leaf → ... → root, so we reverse to root → ... → leaf.
    def build_tree(rows):
        node_by_id = {}
        root_node = None
        for r in rows:
            lineage = list(reversed(r["lineage"]))  # root first
            parent_node = None
            for i, node in enumerate(lineage):
                ott_id = node["ottId"]
                if ott_id not in node_by_id:
                    new = {
                        "ottId": ott_id,
                        "name": node["name"],
                        "rank": node["rank"],
                        "children_map": {},
                    }
                    node_by_id[ott_id] = new
                    if i == 0:
                        root_node = new
                    else:
                        parent_node["children_map"][ott_id] = new
                parent_node = node_by_id[ott_id]
            # parent_node is now the leaf; attach patent info + vernacular
            parent_node["n_edges"] = r["n_edges"]
            parent_node["english"] = r.get("english")
            parent_node["wiki"] = _wiki_url(r["leafName"])
            parent_node["patents"] = [
                {**p, "url": _patent_url(p)}
                for p in (r["patents"] or [])
                if p.get("pubNumber")
            ]
        # Convert children_map dicts to children lists
        def finalize(n):
            kids = list(n.pop("children_map").values())
            kids.sort(key=lambda x: (x.get("rank") != "species", x["name"]))
            for k in kids:
                finalize(k)
            if kids:
                n["children"] = kids
        finalize(root_node)
        return root_node

    tree_root = build_tree(rows)
    return {
        "root": dict(root_rec),
        "tree": tree_root,
        "n_leaves": len(rows),
        "filter": {"cpc": cpc, "limit": limit},
        "truncated": len(rows) >= limit,
    }


def _wiki_url(name: str) -> str:
    """English Wikipedia direct URL for a scientific binomial. Works for most well-known
    species; obscure ones land on a search hit page instead, which is still useful."""
    if not name:
        return ""
    from urllib.parse import quote
    return f"https://en.wikipedia.org/wiki/{quote(name.replace(' ', '_'))}"


def _patent_url(p: dict) -> str:
    """Build a Google Patents URL from a patent record's country + pubNumber."""
    country = (p.get("country") or "").upper()
    pub = (p.get("pubNumber") or "").replace(" ", "")
    if not pub:
        return ""
    # pubNumber sometimes already starts with the country code
    if country and not pub.startswith(country):
        pub = f"{country}{pub}"
    return f"https://patents.google.com/patent/{pub}"


# --------------------------------------------------------------------------
# /api/lineage — full path from the requested taxon up to OTT root.
# Used by the breadcrumb so the user can re-root the view at any ancestor.
# --------------------------------------------------------------------------
@app.get("/api/lineage")
def lineage(ott: int):
    cypher = (
        "MATCH (t:Taxon {ottId:$ott}) "
        "OPTIONAL MATCH p = (t)-[:HAS_PARENT*0..]->(root:Taxon) "
        "WHERE NOT (root)-[:HAS_PARENT]->() "
        "WITH p ORDER BY length(p) DESC LIMIT 1 "
        "RETURN [n IN nodes(p) | {ottId:n.ottId, name:n.name, rank:n.rank}] AS lineage"
    )
    with driver.session(database=NEO4J_DB) as s:
        rec = s.run(cypher, ott=ott).single()
    if not rec or not rec["lineage"]:
        raise HTTPException(404, "lineage not found")
    # Return root → ... → taxon (currently the query returns taxon → ... → root).
    return {"lineage": list(reversed(rec["lineage"]))}


# --------------------------------------------------------------------------
# /api/patents — extra patents for a single leaf, on demand
# --------------------------------------------------------------------------
@app.get("/api/patents")
def patents(ott: int, cpc: Optional[str] = None, limit: int = 50):
    if cpc:
        cypher = (
            "MATCH (t:Taxon {ottId:$ott})-[:HAS_PATENT]->(p:Patent)"
            "-[:HAS_CPC]->(:CPCClass {subclassCode:$cpc}) "
            "RETURN DISTINCT p.pubNumber AS pubNumber, p.country AS country, "
            "       p.title AS title, p.pubDate AS pubDate "
            "ORDER BY p.pubDate DESC LIMIT $limit"
        )
    else:
        cypher = (
            "MATCH (t:Taxon {ottId:$ott})-[:HAS_PATENT]->(p:Patent) "
            "RETURN DISTINCT p.pubNumber AS pubNumber, p.country AS country, "
            "       p.title AS title, p.pubDate AS pubDate "
            "ORDER BY p.pubDate DESC LIMIT $limit"
        )
    with driver.session(database=NEO4J_DB) as s:
        rows = s.run(cypher, ott=ott, cpc=cpc, limit=limit).data()
    for r in rows:
        r["url"] = _patent_url(r)
    return {"results": rows}


@app.on_event("shutdown")
def shutdown():
    driver.close()
