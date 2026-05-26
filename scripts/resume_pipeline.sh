#!/bin/bash
# Resume the patent-ingest pipeline once the EPO OPS ban (from 2026-05-06) has lifted.
# Sequenced: 1) OPS searches  2) load into Neo4j  3) resolve any new IPC/CPC definitions.
# Logs each step separately so you can see exactly where (and if) it failed.

set -u  # don't `set -e` — we want each step's exit code logged even if the previous one fails

ROOT="/Users/wimvancriekinge/treeoflife"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

STAMP=$(date +%Y%m%d_%H%M%S)
SUMMARY="$LOGS/resume_${STAMP}.summary.log"

cd "$ROOT" || { echo "cannot cd to $ROOT" >&2; exit 1; }

# Load env (OPS keys, Neo4j password)
set -a
source "$ROOT/.env"
set +a

# Hard-code the python.org 3.13 (where `neo4j` and `requests` are installed).
# launchd's PATH otherwise resolves /usr/bin/python3 (system 3.9, no deps).
PYTHON="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
echo "=== resume @ $(date -u +%FT%TZ) ===" | tee -a "$SUMMARY"
echo "python: $PYTHON $($PYTHON --version)" | tee -a "$SUMMARY"

# Quick precheck: Neo4j up?
if ! "$PYTHON" -c "from neo4j import GraphDatabase; GraphDatabase.driver('$NEO4J_URI', auth=('$NEO4J_USER','$NEO4J_PASSWORD')).verify_connectivity()" 2>>"$SUMMARY"; then
  echo "FAIL: Neo4j not reachable at $NEO4J_URI — Neo4j Desktop probably not running." | tee -a "$SUMMARY"
  exit 2
fi
echo "Neo4j: OK" | tee -a "$SUMMARY"

# Step 1: OPS searches (resumable; skips species already in data/ops_hits.jsonl)
echo "--- step 1: OPS searches ---" | tee -a "$SUMMARY"
"$PYTHON" -u scripts/ops_search.py >"$LOGS/resume_${STAMP}.step1_ops.log" 2>&1
RC1=$?
echo "step 1 exit: $RC1 (log: resume_${STAMP}.step1_ops.log)" | tee -a "$SUMMARY"

# Step 2: load patents into Neo4j (idempotent via MERGE)
echo "--- step 2: load patents ---" | tee -a "$SUMMARY"
"$PYTHON" -u scripts/load_patents.py >"$LOGS/resume_${STAMP}.step2_load.log" 2>&1
RC2=$?
echo "step 2 exit: $RC2 (log: resume_${STAMP}.step2_load.log)" | tee -a "$SUMMARY"

# Step 3: resolve any newly-introduced IPC/CPC class definitions
echo "--- step 3: resolve class definitions ---" | tee -a "$SUMMARY"
"$PYTHON" -u scripts/resolve_classes.py >"$LOGS/resume_${STAMP}.step3_classes.log" 2>&1
RC3=$?
echo "step 3 exit: $RC3 (log: resume_${STAMP}.step3_classes.log)" | tee -a "$SUMMARY"

echo "=== done @ $(date -u +%FT%TZ) — exit codes ($RC1, $RC2, $RC3) ===" | tee -a "$SUMMARY"
exit $((RC1 | RC2 | RC3))
