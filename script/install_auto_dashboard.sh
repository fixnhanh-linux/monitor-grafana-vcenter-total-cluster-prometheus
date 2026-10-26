#!/usr/bin/env bash
# Tu dong them khoi cho pool moi vao dashboard Grafana "VMWARE CLUSTER HCM".
# Cai systemd timer chay make_dashboard.py moi 10 phut: hoi Prometheus danh sach cluster,
# chi day len Grafana khi co cluster moi (them target vao Prometheus la dashboard tu co khoi moi).
#
#   sudo ./install_auto_dashboard.sh                    # hoi tung thong tin
#   sudo PROM_URL=http://10.0.0.x:9090 GRAFANA_URL=http://10.0.0.y:3000 ./install_auto_dashboard.sh
#   sudo ./install_auto_dashboard.sh --uninstall        # go timer
set -euo pipefail

APP=/opt/vmware-cluster-dashboard
STATE_DIR=/var/lib/vmware-cluster-dashboard
ENV_FILE=/etc/vmware-cluster-dashboard.env
NAME=vmware-cluster-dashboard
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "LOI: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "can chay bang root: sudo $0"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now "$NAME.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/$NAME.service" "/etc/systemd/system/$NAME.timer" "$ENV_FILE"
  rm -rf "$APP" "$STATE_DIR"
  systemctl daemon-reload
  echo "Da go $NAME (dashboard tren Grafana giu nguyen)."
  exit 0
fi

command -v python3 >/dev/null || die "chua co python3"
[ -f "$SRC/make_dashboard.py" ] || die "khong thay make_dashboard.py canh script nay"

ask() {  # ask BIEN "Cau hoi" "mac dinh"
  local var=$1 q=$2 def=${3:-} val=${!1:-}
  if [ -z "$val" ]; then
    read -r -p "$q${def:+ [$def]}: " val
    val=${val:-$def}
  fi
  [ -n "$val" ] || die "$q khong duoc de trong"
  printf -v "$var" '%s' "$val"
}
ask PROM_URL    "URL Prometheus (nguon cua dashboard, vd prometheus-all)" "http://localhost:9090"
ask GRAFANA_URL "URL Grafana" "http://localhost:3000"
ask INTERVAL    "Chu ky kiem tra (phut)" "10"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] || die "chu ky phai la so phut"
if [ -z "${GRAFANA_TOKEN:-}" ]; then
  if [ -f "$ENV_FILE" ] && grep -q '^GRAFANA_TOKEN=' "$ENV_FILE"; then
    read -r -s -p "Grafana token (Enter = giu token cu): " GRAFANA_TOKEN; echo
    [ -n "$GRAFANA_TOKEN" ] || GRAFANA_TOKEN=$(sed -n "s/^GRAFANA_TOKEN='\(.*\)'$/\1/p" "$ENV_FILE")
  else
    read -r -s -p "Grafana service account token (role Editor): " GRAFANA_TOKEN; echo
  fi
fi
[ -n "$GRAFANA_TOKEN" ] || die "token khong duoc de trong"
[[ "$GRAFANA_TOKEN" != *"'"* ]] || die "token khong duoc chua dau nhay don"

echo "-> Kiem tra Prometheus $PROM_URL"
curl -fsS "$PROM_URL/api/v1/label/cluster/values?match%5B%5D=vmware_cluster_hosts" >/dev/null \
  || die "khong goi duoc Prometheus $PROM_URL"
echo "-> Kiem tra Grafana $GRAFANA_URL"
curl -fsS -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/search?limit=1" >/dev/null \
  || die "Grafana tu choi token hoac khong ket noi duoc $GRAFANA_URL"

install -d -m 755 "$APP"
install -m 644 "$SRC/make_dashboard.py" "$APP/make_dashboard.py"
install -d -m 700 "$STATE_DIR"
umask 077
cat > "$ENV_FILE" <<EOF
# Tao boi install_auto_dashboard.sh - KHONG chia se file nay
PROM_URL='$PROM_URL'
GRAFANA_URL='$GRAFANA_URL'
GRAFANA_TOKEN='$GRAFANA_TOKEN'
EOF
chmod 600 "$ENV_FILE"
umask 022

cat > "/etc/systemd/system/$NAME.service" <<EOF
[Unit]
Description=Them khoi cho pool moi vao dashboard Grafana VMWARE CLUSTER HCM
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/env python3 $APP/make_dashboard.py --prometheus \${PROM_URL} --grafana \${GRAFANA_URL} --out $STATE_DIR --state $STATE_DIR/clusters.txt
EOF

cat > "/etc/systemd/system/$NAME.timer" <<EOF
[Unit]
Description=Chay $NAME moi $INTERVAL phut

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL}min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
rm -f "$STATE_DIR/clusters.txt"   # lan dau luon day len de dong bo
systemctl enable --now "$NAME.timer" >/dev/null
systemctl start "$NAME.service" || true
echo
journalctl -u "$NAME.service" -n 5 --no-pager || true
echo
echo "OK - timer chay moi $INTERVAL phut. Them target pool moi vao Prometheus la dashboard tu co khoi moi."
echo "   Xem lan chay: journalctl -u $NAME.service -n 20"
echo "   Chay ngay   : systemctl start $NAME.service"
echo "   Go          : sudo $0 --uninstall"
