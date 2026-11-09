#!/usr/bin/env bash
# Tu dong them host Zabbix cho pool (instance exporter) moi cai tren may nay.
# Cai systemd timer chay zabbix_autosync.sh moi N phut: instance nao chua co host Zabbix (theo IP + port exporter)
# thi tao host "VMware Cluster <TEN>", gan template VMware Cluster HCM. Khong sua / xoa host nao.
# Token API Zabbix LUU trong /etc/vmware-cluster-zabbix-auto.env (chmod 600, chi root doc).
#
#   sudo ZBX_URL=... ZBX_TOKEN=... ZBX_HOST_IP=... [ZBX_OPTS=--insecure] [ZBX_PROXY=id] [ZBX_GROUP=VMware] \
#        [INTERVAL=10] ./install_auto_zabbix.sh       # menu.sh muc 19 hoi tung thong tin roi goi script nay
#   sudo ./install_auto_zabbix.sh --refresh            # chep lai script / template moi (giu cau hinh, token)
#   sudo ./install_auto_zabbix.sh --uninstall          # go timer + XOA token (host tren Zabbix giu nguyen)
set -euo pipefail

APP=/opt/vmware-cluster-zabbix
ENV_FILE=/etc/vmware-cluster-zabbix-auto.env
NAME=vmware-cluster-zabbix
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TPL="$SRC/../template-zabbix/zabbix_template_vmware_cluster_hcm.yaml"

die() { echo "LOI: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "can chay bang root: sudo $0"

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now "$NAME.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/$NAME.service" "/etc/systemd/system/$NAME.timer" "$ENV_FILE"
  rm -rf "$APP"
  systemctl daemon-reload
  echo "Da go $NAME va xoa token Zabbix tren may (host tren Zabbix giu nguyen)."
  exit 0
fi

copy_files() {
  install -d -m 755 "$APP"
  install -m 644 "$SRC/zabbix_tool.py" "$APP/zabbix_tool.py"
  install -m 755 "$SRC/zabbix_autosync.sh" "$APP/zabbix_autosync.sh"
  [ -f "$TPL" ] && install -m 644 "$TPL" "$APP/template.yaml"
  sed -i 's/\r$//' "$APP/zabbix_autosync.sh"  # repo clone tren Windows -> bo CRLF
}

if [ "${1:-}" = "--refresh" ]; then
  [ -f "$ENV_FILE" ] || exit 0
  copy_files
  echo "Da cap nhat $APP (giu cau hinh, token)."
  exit 0
fi

command -v python3 >/dev/null || die "chua co python3"
for v in ZBX_URL ZBX_TOKEN ZBX_HOST_IP; do
  [ -n "${!v:-}" ] || die "thieu bien $v (chay qua menu.sh muc 19)"
done
INTERVAL=${INTERVAL:-10}
[[ "$INTERVAL" =~ ^[0-9]+$ ]] || die "chu ky phai la so phut"
for v in ZBX_URL ZBX_TOKEN ZBX_HOST_IP ZBX_PROXY ZBX_GROUP ZBX_OPTS; do
  [[ "${!v:-}" != *"'"* ]] || die "$v khong duoc chua dau nhay don"
done

copy_files
umask 077
cat > "$ENV_FILE" <<EOF
# Tao boi install_auto_zabbix.sh - CO TOKEN ZABBIX, KHONG chia se file nay
ZBX_URL='$ZBX_URL'
ZBX_TOKEN='$ZBX_TOKEN'
ZBX_OPTS='${ZBX_OPTS:-}'
ZBX_HOST_IP='$ZBX_HOST_IP'
ZBX_PROXY='${ZBX_PROXY:-}'
ZBX_GROUP='${ZBX_GROUP:-VMware}'
EOF
chmod 600 "$ENV_FILE"
umask 022

cat > "/etc/systemd/system/$NAME.service" <<EOF
[Unit]
Description=Them host Zabbix cho pool VMware cluster exporter moi
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=$ENV_FILE
ExecStart=/bin/bash $APP/zabbix_autosync.sh
EOF

cat > "/etc/systemd/system/$NAME.timer" <<EOF
[Unit]
Description=Chay $NAME moi $INTERVAL phut

[Timer]
OnBootSec=3min
OnUnitActiveSec=${INTERVAL}min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$NAME.timer" >/dev/null
systemctl start "$NAME.service" || true
echo
journalctl -u "$NAME.service" -n 8 --no-pager -o cat || true
echo
echo "OK - timer chay moi $INTERVAL phut. Cai exporter cho pool moi (muc 1) la Zabbix tu co host moi."
echo "   Xem lan chay: journalctl -u $NAME.service -n 20"
echo "   Chay ngay   : systemctl start $NAME.service"
echo "   Go          : menu muc 20 (hoac sudo $0 --uninstall)"
