#!/usr/bin/env bash
# End-to-end scan test: login -> create campaign -> discover (country-filtered) -> poll to READY.
set -euo pipefail
BASE="http://localhost:8001/api/v1"
SECTOR="a1a6f766-190f-4a1f-bd78-ab8097b17e75"

TOKEN=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"tenant_slug":"demo","email":"admin@example.com","password":"Demo1234!"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
echo "token acquired"

CAMP=$(curl -s -X POST "$BASE/campaigns" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Hiz Testi TR $(date +%H%M%S)\",\"sector_id\":\"$SECTOR\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "campaign=$CAMP"

# Country-filtered scan: TR only.
curl -s -X POST "$BASE/campaigns/$CAMP/discover" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"countries":["TR"]}' >/dev/null
echo "discover started (countries=TR)"

START=$(date +%s)
for i in $(seq 1 90); do
  S=$(curl -s "$BASE/campaigns/$CAMP/discovery-status" -H "Authorization: Bearer $TOKEN")
  EL=$(( $(date +%s) - START ))
  echo "t=${EL}s $S"
  ST=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  if [ "$ST" = "ready" ] || [ "$ST" = "completed" ]; then
    echo "DONE in ${EL}s"
    break
  fi
  sleep 3
done
echo "CAMP_ID=$CAMP"
