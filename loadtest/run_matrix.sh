#!/usr/bin/env bash
# Runs the load-test matrix for docs/slo.md and docs/scaling.md (steps C-E).
# Each case: 5 min, spawn rate 2, stub model at STUB_DELAY_MS=3000.
# Output per case: loadtest/results/<case>_stats.csv (+ _failures, _stats_history) and a summary line in
# loadtest/results/summary.tsv with the full_run row and peak queue depth / worker CPU from Prometheus.
# Before each case the leftover queue is purged and every worker is warmed up. .env is backed up and restored at the end.
set -uo pipefail
cd "$(dirname "$0")/.."

RUN_TIME="${RUN_TIME:-5m}"
RES=loadtest/results
PROM=http://localhost:9090/api/v1/query
SUMMARY="${SUMMARY:-$RES/summary.tsv}"   # set SUMMARY=... to keep an earlier summary
# ONLY="case1 case2" runs just those cases.
cp .env .env.loadtest.bak
trap 'mv .env.loadtest.bak .env; docker compose up -d --scale worker=1 >/dev/null 2>&1' EXIT

set_env() { # KEY VALUE
  if grep -q "^$1=" .env; then sed -i '' "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}

prom() { curl -s --get "$PROM" --data-urlencode "query=$1" | python3 -c 'import json,sys
r=json.load(sys.stdin)["data"]["result"]; print(round(max(float(x["value"][1]) for x in r),2) if r else "n/a")'; }

printf 'case\tworkers\tusers\tload_vary\trun_cache\trequests\tfailures\tmedian_ms\tp95_ms\tp99_ms\tavg_ms\trps\tpeak_queue\tpeak_cpu_by_service\tstart_utc\tend_utc\n' > "$SUMMARY"

run_case() { # name workers users load_vary run_cache [worker_threads]
  local name=$1 workers=$2 users=$3 vary=$4 cache=$5 threads=${6:-1}
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $name "* ]]; then return; fi
  echo "=== $name: workers=$workers threads=$threads users=$users LOAD_VARY=$vary ENABLE_RUN_CACHE=$cache"
  set_env STUB_DELAY_MS 3000
  set_env ENABLE_RUN_CACHE "$cache"
  set_env WORKER_THREADS "$threads"
  docker compose up -d --scale worker="$workers" --wait >/dev/null 2>&1 || docker compose up -d --scale worker="$workers"
  # Start each case clean: drop the backlog Locust abandoned in the previous case (otherwise it queues ahead of
  # this case's runs and every case after the first measures the previous one's leftovers).
  echo "  purged leftover queue: $(docker compose exec -T redis redis-cli llen queue:runs)"
  docker compose exec -T redis redis-cli del queue:runs >/dev/null
  # Warm-up: 2 unique runs per worker so every replica has loaded the embedding model before measuring.
  local ids=() i
  for i in $(seq 1 $((workers * threads * 2))); do
    ids+=("$(python3 -c "import json;d=json.load(open('examples/requests/source_of_protein.json'));d['product_name']+=' warmup $name $i';print(json.dumps(d))" \
      | curl -s -X POST localhost:8000/v1/runs -H 'Content-Type: application/json' -H "X-Client-Id: warmup-$i" --data @- \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')")
  done
  local t0=$SECONDS done_n=0 id
  while [ $((SECONDS - t0)) -lt 240 ]; do
    done_n=0
    for id in "${ids[@]}"; do
      case "$(curl -s "localhost:8000/v1/runs/$id" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')" in
        completed|degraded|failed) done_n=$((done_n + 1));;
      esac
    done
    [ "$done_n" -eq "${#ids[@]}" ] && break
    sleep 2
  done
  echo "  warm-up: $done_n/${#ids[@]} runs finished in $((SECONDS - t0))s"
  docker compose exec -T redis redis-cli --scan --pattern 'cache:run*' | xargs -r -n 500 docker compose exec -T redis redis-cli del >/dev/null 2>&1 || true
  sleep 5
  local start; start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  ( while true; do docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' >> "$RES/${name}_dockerstats.tsv"; sleep 30; done ) &
  local sampler=$!
  docker compose --profile loadtest run --rm -e LOAD_VARY="$vary" \
    -e LOCUST_USERS="$users" locust -f /mnt/locust/locustfile.py --host http://api:8000 --headless \
    -u "$users" -r 2 -t "$RUN_TIME" --csv "/mnt/locust/results/$name" 2>&1 | tail -3
  local end; end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
  local q c; q=$(prom 'max(max_over_time(run_queue_depth[6m]))')
  c=$(awk -F'\t' '{gsub("%","",$2); split($1,a,"-"); svc=a[2]; if ($2+0>m[svc]) m[svc]=$2+0} END{for(k in m) printf "%s=%.0f%% ", k, m[k]}' "$RES/${name}_dockerstats.tsv")
  python3 - "$RES/${name}_stats.csv" "$name" "$workers" "$users" "$vary" "$cache" "$q" "$c" "$start" "$end" >> "$SUMMARY" <<'PY'
import csv, sys
f, *meta = sys.argv[1:]
row = next((r for r in csv.DictReader(open(f)) if r["Name"] == "full_run"), None)
if row is None:
    print("\t".join(meta[:5] + ["no full_run row"] * 7 + meta[5:])); sys.exit()
vals = [row["Request Count"], row["Failure Count"], row["Median Response Time"], row["95%"], row["99%"],
        str(round(float(row["Average Response Time"]))), str(round(float(row["Requests/s"]), 3))]
print("\t".join(meta[:5] + vals + meta[5:]))
PY
  tail -1 "$SUMMARY"
}

run_case w1_u10_uncached 1 10 1 true
run_case w3_u10_uncached 3 10 1 true
run_case w1_u20_uncached 1 20 1 true
run_case w3_u20_uncached 3 20 1 true
run_case w1_u10_cached   1 10 0 true
run_case w1_u10_cacheoff 1 10 0 false
# More worker threads (runs per replica) instead of more replicas: ONLY="w1t4_u10_uncached w3t2_u10_uncached"
run_case w1t4_u10_uncached 1 10 1 true 4
run_case w3t2_u10_uncached 3 10 1 true 2
echo "done"; column -t -s $'\t' "$SUMMARY"
