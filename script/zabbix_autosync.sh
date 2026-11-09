#!/usr/bin/env bash
# Chay boi timer vmware-cluster-zabbix (bien moi truong tu /etc/vmware-cluster-zabbix-auto.env):
# liet ke instance exporter tren may (ten:port) -> zabbix_tool.py autosync tao host cho instance chua co.
set -uo pipefail
CONF=/etc/vmware-cluster-exporter
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

list=""
for f in "$CONF"/*.env; do
  [ -f "$f" ] || continue
  port=$( set -a; . "$f" >/dev/null 2>&1; printf '%s' "${PORT:-}" )
  [ -n "$port" ] && list="$list${list:+,}$(basename "$f" .env):$port"
done
if [ -z "$list" ]; then
  echo "Chua co exporter nao tren may - khong co gi de them."
  exit 0
fi

tpl=()
[ -f "$APP/template.yaml" ] && tpl=(--template "$APP/template.yaml")
# shellcheck disable=SC2086
exec python3 "$APP/zabbix_tool.py" --url "$ZBX_URL" ${ZBX_OPTS:-} autosync --instances "$list" \
  --ip "$ZBX_HOST_IP" --proxy "${ZBX_PROXY:-}" --group "${ZBX_GROUP:-VMware}" ${tpl[@]+"${tpl[@]}"}
