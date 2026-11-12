#!/usr/bin/env bash
# End-to-end smoke test from a clean clone (demo item 1).
#
#   scripts/smoke_test.sh            # build, start, check everything, leave the stack running
#   scripts/smoke_test.sh --fresh    # first wipe containers AND volumes (true clean-clone run)
#   scripts/smoke_test.sh --scale3   # also check that 3 worker replicas are scraped by Prometheus
#
# Prints PASS/FAIL per check and writes the same report to reports/smoke_<timestamp>.txt.
# Needs: Docker Compose v2.24+, curl, python3 on the host.
set -uo pipefail
cd "$(dirname "$0")/.."

FRESH=0; SCALE3=0
for a in "$@"; do
  case "$a" in
    --fresh) FRESH=1 ;;
    --scale3) SCALE3=1 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

API=http://localhost:8000; WEB=http://localhost:8080
PROM=http://localhost:9090; GRAF=http://localhost:3000; JAEGER=http://localhost:16686
mkdir -p reports
REPORT="reports/smoke_$(date +%Y%m%d_%H%M%S).txt"
FAILS=0

log()  { echo "$*" | tee -a "$REPORT"; }
pass() { log "PASS  $*"; }
fail() { log "FAIL  $*"; FAILS=$((FAILS + 1)); }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); $1"; }
# all_up "<job=up/total ...>" <job> [expected_total] -> success if every target of <job> is up
all_up() { python3 -c "
import sys
c={k:v for k,v in (x.split('=') for x in sys.argv[1].split())}
u,n=(int(x) for x in c.get(sys.argv[2],'0/0').split('/'))
want=int(sys.argv[3]) if len(sys.argv)>3 else None
sys.exit(0 if n>0 and u==n and (want is None or n==want) else 1)" "$@"; }

# Poll a command until it succeeds. wait_for <seconds> <description> <command...>
wait_for() {
  local secs=$1 what=$2; shift 2
  for _ in $(seq 1 "$secs"); do
    if "$@" >/dev/null 2>&1; then pass "$what"; return 0; fi
    sleep 1
  done
  fail "$what (gave up after ${secs}s)"; return 1
}

log "== smoke test $(date -Iseconds)  commit $(git rev-parse --short HEAD 2>/dev/null || echo '?')"

# ---------------------------------------------------------------- 0. preflight
v=$(docker compose version --short 2>/dev/null | sed 's/^v//')
if [ -z "$v" ]; then fail "docker compose is not installed"; exit 1; fi
if python3 -c "import sys; a=[int(x) for x in '$v'.split('.')[:2]]; sys.exit(0 if a>=[2,24] else 1)"; then
  pass "docker compose $v (need 2.24+)"
else
  fail "docker compose $v is older than 2.24 (optional env_file entries will not parse)"; exit 1
fi

missing=$(python3 - <<'PY'
import os, yaml
cat = yaml.safe_load(open("data/corpus/corpus.yaml"))
print(" ".join(d["file"] for d in cat["documents"] if not os.path.exists(os.path.join("data/corpus", d["file"]))))
PY
) || missing="(could not read corpus.yaml - is PyYAML installed? pip install pyyaml)"
if [ -n "$missing" ]; then
  fail "corpus files missing in data/corpus/: $missing  -> the indexer will exit and workers will not start"
  exit 1
fi
pass "corpus PDFs present"

if [ -z "$(docker compose ps -q 2>/dev/null)" ]; then   # only meaningful when our stack is not up
  for port in 8000 8080 8081 3000 9090 16686; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      fail "port $port is already in use by another program - free it or the stack cannot bind it"
    fi
  done
fi

# ---------------------------------------------------------------- 1. build + start
if [ "$FRESH" = 1 ]; then
  log "-- wiping previous containers and volumes (docker compose down -v)"
  docker compose down -v --remove-orphans >>"$REPORT" 2>&1
fi
log "-- docker compose up --build -d  (first build pulls torch and can take 10+ minutes)"
UP_ARGS=(up --build -d)
[ "$SCALE3" = 1 ] && UP_ARGS+=(--scale worker=3)
if docker compose "${UP_ARGS[@]}" 2>&1 | tee -a "$REPORT" | tail -20; then :; fi

# indexer is a one-shot job: it must have exited 0
ix_state=$(docker compose ps -a indexer --format '{{.State}} {{.ExitCode}}' 2>/dev/null | head -1)
if [ "$ix_state" = "exited 0" ]; then
  pass "indexer finished (exit 0)"
else
  fail "indexer state: '${ix_state:-not found}'. Last log lines:"
  docker compose logs --tail=30 indexer 2>&1 | tee -a "$REPORT"
fi

# every long-running service should be healthy (or at least running if it has no healthcheck)
for svc in redis llm-stub verifier api worker; do
  wait_for 240 "$svc healthy" bash -c "docker compose ps $svc --format '{{.Health}}' | grep -qx healthy" \
    || docker compose logs --tail=30 "$svc" 2>&1 | tee -a "$REPORT"
done
for svc in web jaeger prometheus grafana cadvisor; do
  if docker compose ps "$svc" --format '{{.State}}' | grep -qx running; then pass "$svc running"
  else fail "$svc not running"; docker compose logs --tail=20 "$svc" 2>&1 | tee -a "$REPORT"; fi
done

# ---------------------------------------------------------------- 2. index contents
idx=$(docker compose exec -T worker python -c "import json;m=json.load(open('/app/data/index/manifest.json'));print(m['chunk_count'], m['build_id'], ' '.join(f\"{d['doc_id']}={d['chunks']}\" for d in m['documents']))" 2>/dev/null)
if [ -n "$idx" ] && [ "${idx%% *}" -gt 0 ] 2>/dev/null; then pass "index: chunks build_id per-doc = $idx"
else fail "index manifest missing or empty in the worker"; fi

# ---------------------------------------------------------------- 3. API + web
wait_for 30 "api /healthz"  curl -fsS "$API/healthz"
wait_for 30 "api /readyz"   curl -fsS "$API/readyz"
wait_for 10 "api /docs"     curl -fsS "$API/docs"
wait_for 10 "api /metrics"  bash -c "curl -fsS $API/metrics | grep -q http_requests_total"
wait_for 10 "web client :8080" curl -fsS "$WEB/"
wait_for 10 "web -> api proxy (/api/healthz)" curl -fsS "$WEB/api/healthz"

# ---------------------------------------------------------------- 4. one real run through the agents
run_case() {  # run_case <request file> <label>
  local file=$1 label=$2 code body run_id status=""
  body=$(mktemp)
  code=$(curl -s -o "$body" -w '%{http_code}' -X POST "$API/v1/runs" -H 'content-type: application/json' -d @"$file")
  if [ "$code" != "202" ]; then fail "$label: POST /v1/runs returned HTTP $code: $(head -c 300 "$body")"; return 1; fi
  run_id=$(json "print(d['run_id'])" <"$body")
  for _ in $(seq 1 150); do
    status=$(curl -s "$API/v1/runs/$run_id" | json "print(d['status'])" 2>/dev/null)
    case "$status" in completed|degraded|failed) break ;; esac
    sleep 1
  done
  local summary
  summary=$(curl -s "$API/v1/runs/$run_id" | json "r=d.get('result') or {}; print('route=%s findings=%s' % (r.get('route'), len(r.get('findings') or [])))" 2>/dev/null)
  case "$status" in
    completed) pass "$label: run $run_id completed ($summary)" ;;
    degraded)  fail "$label: run $run_id DEGRADED ($summary) - check worker/verifier logs" ;;
    *)         fail "$label: run $run_id ended as '${status:-timeout}'"; docker compose logs --tail=40 worker verifier 2>&1 | tee -a "$REPORT" ;;
  esac
  if curl -fsS "$API/v1/runs/$run_id/trace" >/dev/null; then pass "$label: /trace reconstructs the run"
  else fail "$label: /trace endpoint failed"; fi
  rm -f "$body"
  LAST_RUN=$run_id
}
LAST_RUN=""
run_case examples/requests/source_of_protein.json "source_of_protein"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/v1/runs" -H 'content-type: application/json' -d @examples/requests/malformed.json)
case "$code" in 4??) pass "malformed request rejected at the boundary (HTTP $code)" ;;
                 *)  fail "malformed request returned HTTP $code (expected 4xx)" ;; esac

# ---------------------------------------------------------------- 5. Prometheus
sleep 8   # let at least one scrape happen after the run
targets=$(curl -fsS "$PROM/api/v1/targets" 2>/dev/null | json "
from collections import defaultdict
c=defaultdict(lambda:[0,0])
for t in d['data']['activeTargets']:
    j=t['labels']['job']; c[j][1]+=1; c[j][0]+= t['health']=='up'
print(' '.join(f'{j}={u}/{n}' for j,(u,n) in sorted(c.items())))" 2>/dev/null)
if [ -z "$targets" ]; then fail "Prometheus API not reachable at $PROM"
else
  log "      prometheus targets up/total: $targets"
  for job in api verifier worker cadvisor; do
    if all_up "$targets" "$job"; then pass "prometheus target '$job' up"
    else fail "prometheus target '$job' not (all) up"; fi
  done
  if [ "$SCALE3" = 1 ]; then
    if all_up "$targets" worker 3; then pass "3 worker replicas scraped"
    else fail "expected worker=3/3 with --scale3"; fi
  fi
fi
n=$(curl -fsS "$PROM/api/v1/query" --data-urlencode 'query=sum(runs_finished_total)' 2>/dev/null | json "r=d['data']['result']; print(r[0]['value'][1] if r else 0)" 2>/dev/null)
if [ -n "$n" ] && python3 -c "import sys; sys.exit(0 if float('$n')>0 else 1)"; then pass "runs_finished_total = $n in Prometheus"
else fail "runs_finished_total not visible in Prometheus (got '${n:-nothing}')"; fi

# ---------------------------------------------------------------- 6. Grafana
wait_for 60 "grafana /api/health" curl -fsS "$GRAF/api/health"
if curl -fsS "$GRAF/api/dashboards/uid/foodlabel" >/dev/null 2>&1; then pass "grafana dashboard 'foodlabel' provisioned"
else fail "grafana dashboard uid 'foodlabel' not found"; fi
ds=$(curl -fsS "$GRAF/api/datasources/uid/prom/health" 2>/dev/null | json "print(d.get('status'))" 2>/dev/null)
if [ "$ds" = "OK" ]; then pass "grafana -> prometheus datasource healthy"
else fail "grafana prometheus datasource health: '${ds:-unreachable}'"; fi

# ---------------------------------------------------------------- 7. Jaeger
sleep 5   # BatchSpanProcessor flushes every ~5s
services=$(curl -fsS "$JAEGER/api/services" 2>/dev/null | json "print(' '.join(sorted(d.get('data') or [])))" 2>/dev/null)
log "      jaeger services: ${services:-none}"
for svc in api worker; do
  if echo " $services " | grep -q " $svc "; then pass "jaeger has traces from '$svc'"
  else fail "jaeger has no traces from '$svc'"; fi
done

# ---------------------------------------------------------------- summary
log "== $FAILS failure(s). Report: $REPORT"
log "   Web $WEB | API docs $API/docs | Grafana $GRAF | Jaeger $JAEGER | Prometheus $PROM"
log "   Stack left running. Stop with: docker compose down   (add -v to drop the index and data)"
[ "$FAILS" -eq 0 ]
