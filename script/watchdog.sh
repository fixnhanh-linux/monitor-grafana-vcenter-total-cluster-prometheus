#!/usr/bin/env bash
# Watchdog: timer chay moi 2 phut (cai bang install_watchdog.sh / menu muc 22), tu khoi dong lai service bi dung / treo.
#  - Exporter (vmware-cluster-exporter@<ten>): dang enable ma khong chay -> restart.
#      Bo qua: sai mat khau vCenter (ma thoat 3, tranh khoa tai khoan) va instance dung tay o menu muc 4.
#    Dang chay ma /metrics khong tra loi 2 lan lien tiep (treo) -> restart.
#    So lieu cu hon STALE giay (khong lay duoc vCenter) -> restart, toi da 1 lan / STALE_GAP giay.
#  - EXTRA (mac dinh prometheus, grafana-server): co cai + enable ma khong chay -> restart.
#  - Timer dashboard / Zabbix tu dong: enable ma timer bi dung -> start lai.
# Nhat ky: journalctl -u vmware-cluster-watchdog -n 50
set -uo pipefail

CONF=${CONF:-/etc/vmware-cluster-exporter}
SVC=vmware-cluster-exporter
STATE=${STATE:-/var/lib/vmware-cluster-watchdog}
EXTRA=${EXTRA-prometheus grafana-server}
STALE=${STALE:-900}          # giay
STALE_GAP=${STALE_GAP:-1800}  # giay giua 2 lan restart vi so lieu cu
GRACE=${GRACE:-300}           # service moi chay < GRACE giay thi chua xet treo / so lieu cu

mkdir -p "$STATE"
now=$(date +%s)

prop() { systemctl show -p "$2" --value "$1" 2>/dev/null; }
up_secs() {  # so giay tu luc service vao trang thai active
  local t
  t=$(prop "$1" ActiveEnterTimestamp)
  [ -n "$t" ] && t=$(date -d "$t" +%s 2>/dev/null) || t=$now
  echo $((now - t))
}
key() { printf '%s' "${1//[^A-Za-z0-9_.-]/_}"; }
do_restart() {  # do_restart <unit> <ly do>
  echo "KHOI DONG LAI $1: $2"
  if systemctl restart "$1"; then
    echo "$now" > "$STATE/$(key "$1").last"
  else
    echo "  -> restart $1 loi, xem: journalctl -u $1 -n 20"
  fi
}

# ---- exporter ----
for f in "$CONF"/*.env; do
  [ -f "$f" ] || continue
  n=$(basename "$f" .env); u="$SVC@$n"; k=$(key "$u")
  systemctl is-enabled --quiet "$u" 2>/dev/null || continue  # da disable / go -> khong dung toi
  if systemctl is-active --quiet "$u"; then
    rm -f "$CONF/$n.stopped"  # chay lai roi -> bo dau "dung tay"
  else
    [ -f "$CONF/$n.stopped" ] && continue  # dung tay o menu muc 4
    state=$(prop "$u" ActiveState); code=$(prop "$u" ExecMainStatus)
    [ "$state" = activating ] && continue  # systemd dang tu chay lai (RestartSec)
    if [ "$code" = 3 ]; then
      [ -f "$STATE/$k.pw" ] || echo "$u dung vi SAI MAT KHAU vCenter (ma 3) - khong tu chay lai; doi mat khau o menu muc 3"
      touch "$STATE/$k.pw"
      continue
    fi
    do_restart "$u" "service khong chay ($state, ma thoat ${code:-?})"
    rm -f "$STATE/$k.fail"
    continue
  fi
  rm -f "$STATE/$k.pw"
  (( $(up_secs "$u") < GRACE )) && { rm -f "$STATE/$k.fail"; continue; }
  port=$(set -a; . "$f" >/dev/null 2>&1; echo "${PORT:-}")
  [ -n "$port" ] || continue
  m=$(curl -s --max-time 15 "http://127.0.0.1:$port/metrics" 2>/dev/null)
  if [ -z "$m" ]; then
    fails=$(( $(cat "$STATE/$k.fail" 2>/dev/null || echo 0) + 1 ))
    if (( fails >= 2 )); then
      do_restart "$u" "dang chay nhung /metrics port $port khong tra loi $fails lan lien tiep (treo)"
      rm -f "$STATE/$k.fail"
    else
      echo "$u: /metrics port $port khong tra loi (lan $fails) - lan sau van vay se restart"
      echo "$fails" > "$STATE/$k.fail"
    fi
    continue
  fi
  rm -f "$STATE/$k.fail"
  last=$(printf '%s\n' "$m" | awk '$1=="vmware_cluster_exporter_last_success_timestamp_seconds" {printf "%d", $2 + 0; exit}')
  last=${last:-0}
  if (( last > 0 )); then age=$((now - last)); else age=$(up_secs "$u"); fi
  if (( age > STALE )); then
    prev=$(cat "$STATE/$k.last" 2>/dev/null || echo 0)
    if (( now - prev >= STALE_GAP )); then
      do_restart "$u" "so lieu cu $((age / 60)) phut (khong lay duoc tu vCenter)"
    fi
  fi
done

# ---- Prometheus / Grafana ... ----
for s in $EXTRA; do
  u="$s.service"
  systemctl cat "$u" >/dev/null 2>&1 || continue          # khong cai tren may nay
  systemctl is-enabled --quiet "$u" 2>/dev/null || continue
  systemctl is-active --quiet "$u" && continue
  [ "$(prop "$u" ActiveState)" = activating ] && continue
  do_restart "$u" "service khong chay ($(prop "$u" ActiveState))"
done

# ---- timer dashboard / Zabbix tu dong ----
for t in vmware-cluster-dashboard.timer vmware-cluster-zabbix.timer; do
  systemctl is-enabled --quiet "$t" 2>/dev/null || continue
  systemctl is-active --quiet "$t" && continue
  echo "START LAI $t (timer bi dung)"
  systemctl start "$t" || true
done
exit 0
