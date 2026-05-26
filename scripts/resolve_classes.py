#!/usr/bin/env python3
"""Attach human-readable definitions to IPCClass / CPCClass nodes in Neo4j.

Source data:
  data/EN_ipc_section_*_title_list_*.txt   (WIPO IPC, 80k entries)
  data/cpc/cpc-section-*_*.txt             (EPO CPC 2026.05, ~250k entries)

Code formats encountered:
  - In Neo4j (from OPS):
      IPCClass.code:  "C12N 15/"  (truncated by parser bug; subgroup missing)
      CPCClass.code:  "A23K 50/80"
  - In WIPO IPC scheme files:
      "A01B0001000000"  for subgroups (4 letters + 4 main-group digits + 6 subgroup digits)
      "A01B"            for subclass / class / section
  - In CPC scheme files:
      "A23K50/80"       (no space)

Strategy:
  1. Parse both schemes into normalized "<subclass> <main>/<sub>" or shorter forms.
  2. For each Neo4j class node, normalize its code, look up best-matching definition
     (exact > main-group fallback > subclass fallback). Set `.definition`,
     `.section`, `.class`, `.subclass` on the node.
"""
from __future__ import annotations
import glob
import os
import re
from pathlib import Path
from neo4j import GraphDatabase

ROOT = Path("/Users/wimvancriekinge/treeoflife/data")
URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]
DB = os.environ.get("NEO4J_DB", "treeoflife")

SECTION_TITLES = {
    "A": "HUMAN NECESSITIES",
    "B": "PERFORMING OPERATIONS; TRANSPORTING",
    "C": "CHEMISTRY; METALLURGY",
    "D": "TEXTILES; PAPER",
    "E": "FIXED CONSTRUCTIONS",
    "F": "MECHANICAL ENGINEERING; LIGHTING; HEATING; WEAPONS; BLASTING",
    "G": "PHYSICS",
    "H": "ELECTRICITY",
    "Y": "GENERAL TAGGING OF NEW TECHNOLOGICAL DEVELOPMENTS (CPC only)",
}

IPC_SUBGROUP_RE = re.compile(r"^([A-HY]\d{2}[A-Z])(\d{4})(\d{6})$")


def normalize_ipc_scheme_code(code: str) -> str | None:
    """`A01B0001000000` -> `A01B 1/00`. `A01B` -> `A01B`."""
    code = code.strip()
    m = IPC_SUBGROUP_RE.match(code)
    if m:
        sub, mg, sg = m.groups()
        mg_int = int(mg)
        sg_clean = sg.rstrip("0")
        if len(sg_clean) < 2:
            sg_clean = (sg_clean + "00")[:2]
        return f"{sub} {mg_int}/{sg_clean}"
    if re.fullmatch(r"[A-HY]", code):
        return code
    if re.fullmatch(r"[A-HY]\d{2}", code):
        return code
    if re.fullmatch(r"[A-HY]\d{2}[A-Z]", code):
        return code
    return None


def load_ipc_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    for fn in sorted(ROOT.glob("EN_ipc_section_*_title_list_*.txt")):
        with fn.open() as f:
            for line in f:
                parts = line.rstrip("\n").split("\t", 1)
                if len(parts) != 2:
                    continue
                code, title = parts[0], parts[1].strip()
                norm = normalize_ipc_scheme_code(code)
                if norm and title:
                    titles.setdefault(norm, title)
    for sec, t in SECTION_TITLES.items():
        titles.setdefault(sec, t)
    return titles


def load_cpc_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    for fn in sorted((ROOT / "cpc").glob("cpc-section-*.txt")):
        with fn.open() as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
                # Two formats:
                #   <code><tab><title>           (sections/classes/subclasses, plus some
                #                                groups missing level)
                #   <code><tab><level><tab><title>  (groups)
                if len(parts) == 2:
                    code, title = parts[0], parts[1].strip()
                elif len(parts) >= 3:
                    code, _level, title = parts[0], parts[1], parts[2].strip()
                else:
                    continue
                code = code.strip()
                if not code or not title:
                    continue
                # CPC scheme uses no space between subclass and main-group: A01B1/00
                m = re.match(r"^([A-HY]\d{2}[A-Z])(\d.*)$", code)
                if m:
                    norm = f"{m.group(1)} {m.group(2)}"
                else:
                    norm = code
                titles.setdefault(norm, title)
    for sec, t in SECTION_TITLES.items():
        titles.setdefault(sec, t)
    return titles


def hierarchy(code: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (section, class, subclass, main_group) for an IPC/CPC code like 'A23K 50/80'."""
    if not code:
        return (None, None, None, None)
    m = re.match(r"^([A-HY])(\d{2})?([A-Z])?\s*(\d+)?(/\d+)?", code)
    if not m:
        return (None, None, None, None)
    sec = m.group(1)
    cls = sec + m.group(2) if m.group(2) else None
    sub = cls + m.group(3) if m.group(3) and cls else None
    mg = (sub + " " + m.group(4)) if m.group(4) and sub else None
    return sec, cls, sub, mg


def lookup(code: str, titles: dict[str, str]) -> str | None:
    """Find the most-specific available title.

    Tries exact match, main-group strip ("A23K 50/80" -> "A23K 50/00" -> "A23K 50"),
    subclass, class, section as fallbacks.
    """
    if code in titles:
        return titles[code]
    # strip trailing slash (parser bug) and try canonical main-group "/00"
    bare = code.rstrip("/").strip()
    if bare in titles:
        return titles[bare]
    if " " in bare:
        sub, rest = bare.split(" ", 1)
        if "/" in rest:
            mg = rest.split("/")[0]
            for cand in (f"{sub} {mg}/00", f"{sub} {mg}"):
                if cand in titles:
                    return titles[cand]
        else:
            for cand in (f"{sub} {rest}/00", f"{sub} {rest}"):
                if cand in titles:
                    return titles[cand]
    sec, cls, sub, _mg = hierarchy(code)
    for cand in (sub, cls, sec):
        if cand and cand in titles:
            return titles[cand]
    return None


def patch_nodes(label: str, titles: dict[str, str]):
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    try:
        with driver.session(database=DB) as s:
            codes = [r["code"] for r in s.run(f"MATCH (c:{label}) RETURN c.code AS code")]
        print(f"{label}: {len(codes)} nodes")
        rows = []
        for code in codes:
            sec, cls, sub, mg = hierarchy(code)
            rows.append({
                "code": code,
                "definition": lookup(code, titles),
                "section": sec,
                "section_title": SECTION_TITLES.get(sec),
                "class_": cls,
                "class_title": titles.get(cls) if cls else None,
                "subclass": sub,
                "subclass_title": titles.get(sub) if sub else None,
            })
        with driver.session(database=DB) as s:
            s.run(f"""
                UNWIND $rows AS r
                MATCH (c:{label} {{code: r.code}})
                SET c.definition = r.definition,
                    c.section = r.section,
                    c.sectionTitle = r.section_title,
                    c.classCode = r.class_,
                    c.classTitle = r.class_title,
                    c.subclassCode = r.subclass,
                    c.subclassTitle = r.subclass_title
            """, rows=rows).consume()
        unresolved = sum(1 for r in rows if not r["definition"])
        print(f"  resolved={len(rows)-unresolved} unresolved={unresolved}")
    finally:
        driver.close()


def main():
    print("Loading IPC titles..."); ipc = load_ipc_titles(); print(f"  {len(ipc):,} entries")
    print("Loading CPC titles..."); cpc = load_cpc_titles(); print(f"  {len(cpc):,} entries")
    patch_nodes("IPCClass", ipc)
    patch_nodes("CPCClass", cpc)


if __name__ == "__main__":
    main()
