#!/usr/bin/env bash
# Demo yêu cầu 1c: điều khiển IaaS qua REST API thuần (curl), chạy từ Mac.
#   ./demo-1c-api.sh            # lấy token, liệt kê flavor/image/network/server
#   ./demo-1c-api.sh create     # thêm: tạo 1 VM qua POST /servers, chờ ACTIVE, rồi xóa
set -euo pipefail
H=${OS_HOST:-192.168.64.2}; KS=http://$H/identity/v3; NOVA=http://$H/compute/v2.1
GLANCE=http://$H/image/v2; NEUTRON=http://$H/networking/v2.0
step(){ printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "1. Keystone: POST /auth/tokens (user demo, project demo)"
TOKEN=$(curl -si "$KS/auth/tokens" -H 'Content-Type: application/json' -d '{
  "auth":{"identity":{"methods":["password"],"password":{"user":{"name":"demo","domain":{"id":"default"},"password":"secret"}}},
          "scope":{"project":{"name":"demo","domain":{"id":"default"}}}}}' \
  | awk 'tolower($1)=="x-subject-token:"{print $2}' | tr -d '\r')
echo "X-Subject-Token: ${TOKEN:0:24}…"
api(){ curl -s -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' "$@"; }

step "2. Nova: GET /flavors/detail"
api "$NOVA/flavors/detail" | jq -r '.flavors[] | "\(.name)\t\(.vcpus) vCPU\t\(.ram) MB"'
step "3. Glance: GET /v2/images"
api "$GLANCE/images" | jq -r '.images[] | "\(.name)\t\(.status)\t\(.size/1048576|floor) MB"'
step "4. Neutron: GET /v2.0/networks"
NETS=$(api "$NEUTRON/networks"); echo "$NETS" | jq -r '.networks[] | "\(.name)\t\(.id)"'
step "5. Nova: GET /servers/detail"
api "$NOVA/servers/detail" | jq -r '.servers[] | "\(.name)\t\(.status)\t\(.addresses|tostring)"'

if [ "${1:-}" = create ]; then
  IMG=$(api "$GLANCE/images?name=app-golden" | jq -r '.images[0].id')
  FLV=$(api "$NOVA/flavors/detail" | jq -r '.flavors[] | select(.name=="app.small") .id')
  NET=$(echo "$NETS" | jq -r '.networks[] | select(.name=="app-net") .id')
  step "6. Nova: POST /servers (api-demo)"
  ID=$(api -X POST "$NOVA/servers" -d "{\"server\":{\"name\":\"api-demo\",\"imageRef\":\"$IMG\",\"flavorRef\":\"$FLV\",
       \"networks\":[{\"uuid\":\"$NET\"}],\"security_groups\":[{\"name\":\"app-sg\"}]}}" | jq -r .server.id)
  echo "server id: $ID"
  until [ "$(api "$NOVA/servers/$ID" | jq -r .server.status)" = ACTIVE ]; do sleep 2; printf .; done; echo " ACTIVE"
  api "$NOVA/servers/$ID" | jq -r '.server | "\(.name) \(.addresses|tostring)"'
  step "7. Nova: DELETE /servers/$ID"
  api -X DELETE -o /dev/null -w "HTTP %{http_code}\n" "$NOVA/servers/$ID"
fi
