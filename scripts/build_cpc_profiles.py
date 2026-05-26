#!/usr/bin/env python3
"""Reconstruct per-species CPC-subclass profiles directly from the BigQuery
full-tree hits (the same source the Neo4j graph was loaded from), so the
branch-length analysis doesn't depend on a live Neo4j. Validate the result
against the n_cpc_subclasses counts stored in mantel_results.json."""
import json
from pathlib import Path

DATA = Path("/Users/wimvancriekinge/treeoflife/data")
HITS = DATA / "bq_hits_full.jsonl"


def subclass(code: str) -> str:
    """CPC/IPC subclass = section letter + 2 class digits + subclass letter."""
    c = code.replace(" ", "")
    return c[:4] if len(c) >= 4 else ""


def main():
    prof = {}   # ott_id -> set(subclass)
    n = 0
    with HITS.open() as f:
        for line in f:
            n += 1
            r = json.loads(line)
            ott = r.get("ott_id")
            if ott is None:
                continue
            s = prof.setdefault(ott, set())
            for code in r.get("cpc_codes") or []:
                sc = subclass(code)
                if sc:
                    s.add(sc)
            if n % 200000 == 0:
                print(f"  {n} lines, {len(prof)} species", flush=True)
    print(f"read {n} hits; {len(prof)} species with ott_id", flush=True)

    # validate against stored counts
    res = json.load(open(DATA / "mantel_results.json"))
    ok = bad = miss = 0
    examples = []
    for s in res["species"]:
        ott = s["ott_id"]; want = s["n_cpc_subclasses"]
        got = len(prof.get(ott, set()))
        if ott not in prof:
            miss += 1; continue
        if got == want:
            ok += 1
        else:
            bad += 1
            if len(examples) < 10:
                examples.append((s["name"], ott, want, got))
    print(f"\nvalidation vs mantel_results.json:")
    print(f"  exact-count match: {ok}")
    print(f"  mismatch:          {bad}")
    print(f"  ott missing in hits: {miss}")
    if examples:
        print("  mismatch examples (name, ott, stored, reconstructed):")
        for e in examples:
            print("   ", e)

    out = {str(k): sorted(v) for k, v in prof.items()}
    json.dump(out, open(DATA / "cpc_profiles_by_ott.json", "w"))
    print(f"\nwrote {DATA/'cpc_profiles_by_ott.json'} ({len(out)} species)")


if __name__ == "__main__":
    main()
