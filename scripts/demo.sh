#!/usr/bin/env bash
# Submit a request file and wait for the result.   Usage: scripts/demo.sh examples/requests/source_of_protein.json
set -euo pipefail
API="${API:-http://localhost:8000}"
FILE="${1:-examples/requests/source_of_protein.json}"

resp=$(curl -s -o /tmp/demo_resp.json -w '%{http_code}' -X POST "$API/v1/runs" -H 'content-type: application/json' -d @"$FILE")
if [ "$resp" != "202" ]; then
  echo "HTTP $resp (rejected at the ingestion boundary or rate limited):"; cat /tmp/demo_resp.json; echo; exit 0
fi
run_id=$(python3 -c "import json;print(json.load(open('/tmp/demo_resp.json'))['run_id'])")
echo "run_id=$run_id"
for _ in $(seq 1 120); do
  status=$(curl -s "$API/v1/runs/$run_id" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  echo "status=$status"
  case "$status" in completed|degraded|failed) break;; esac
  sleep 1
done
curl -s "$API/v1/runs/$run_id" | python3 -m json.tool
echo "trace: $API/v1/runs/$run_id/trace"
