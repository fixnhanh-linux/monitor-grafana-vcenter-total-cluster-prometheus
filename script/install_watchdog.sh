#!/usr/bin/env bash
# Cai watchdog tu khoi dong lai service (exporter, Prometheus, Grafana, timer tu dong) - xem watchdog.sh.
#   sudo ./install_watchdog.sh              # cai / cap nhat (giu cau hinh cu)
#   sudo ./install_watchdog.sh --uninstall  # go
# Cau hinh: /etc/vmware-cluster-watchdog.env (EXTRA, STALE, STALE_GAP, GRACE). Chu ky: 2 phut.
set -euo pipefail

APP=/opt/vmware-cluster-watchdog
ENV_FILE=/etc/vmware-cluster-watchdog.env
NAME=vmware-cluster-watchdog
DROPIN=/etc/systemd/system/vmware-cluster-exporter@.service.d/restart.conf
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "LOI: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "can chay bang root: sudo $0"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now "$NAME.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/$NAME.service" "/etc/systemd/system/$NAME.timer" "$ENV_FILE" "$DROPIN"
  rmdir "$(dirname "$DROPIN")" 2>/dev/null || true
  rm -rf "$APP" /var/lib/vmware-cluster-watchdog
  systemctl daemon-reload
  echo "Da go watchdog (exporter van tu chay lai khi crash nho Restart=always cua systemd)."
  exit 0
fi

command -v curl >/dev/null || die "can curl (apt install curl)"
install -d -m 755 "$APP"
install -m 755 "$SRC/watchdog.sh" "$APP/watchdog.sh"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# Watchdog VMware cluster exporter (menu muc 22). Sua xong khong can restart, lan chay sau tu doc.
# Service khac can tu chay lai khi bi dung (de trong = chi exporter):
EXTRA="prometheus grafana-server"
# So lieu exporter cu hon STALE giay -> restart exporter, toi da 1 lan moi STALE_GAP giay
STALE=900
STALE_GAP=1800
# Service moi chay chua du GRACE giay thi chua xet treo / so lieu cu
GRACE=300
EOF
fi

# Exporter crash -> systemd tu chay lai sau 30s, khong bao gio bo cuoc (mac dinh systemd ngung sau 5 lan / 10s)
install -d -m 755 "$(dirname "$DROPIN")"
cat > "$DROPIN" <<'EOF'
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=30
EOF

cat > "/etc/systemd/system/$NAME.service" <<EOF
[Unit]
Description=Watchdog: tu khoi dong lai VMware cluster exporter / Prometheus / Grafana bi dung hoac treo
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-$ENV_FILE
ExecStart=/bin/bash $APP/watchdog.sh
EOF

cat > "/etc/systemd/system/$NAME.timer" <<EOF
[Unit]
Description=Chay $NAME moi 2 phut

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$NAME.timer" >/dev/null
systemctl start "$NAME.service" || true
echo "OK - watchdog chay moi 2 phut."
echo "   Nhat ky : journalctl -u $NAME -n 50"
echo "   Cau hinh: $ENV_FILE"
