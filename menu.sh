#!/usr/bin/env bash
# Menu quan ly VMware cluster exporter + dashboard Grafana "VMWARE CLUSTER HCM".
#   sudo bash menu.sh
# Moi viec (cai pool moi, doi mat khau, xem trang thai, cau hinh Prometheus, them/xoa khoi tren dashboard,
# cap nhat ban moi) chon bang so. Mat khau vCenter va token Grafana luon tu go, khong in ra man hinh.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$REPO/script"
ZIP="$SCRIPT/vmware-cluster-exporter-linux.zip"
PKG="$SCRIPT/vmware-cluster-exporter"
CONF=/etc/vmware-cluster-exporter
SVC=vmware-cluster-exporter
DASH_ENV=/etc/vmware-cluster-dashboard.env
DASH_APP=/opt/vmware-cluster-dashboard/make_dashboard.py
DASH_NAME=vmware-cluster-dashboard

if [ "$(id -u)" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi

if [ -t 1 ]; then
  B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; C=$'\e[36m'; M=$'\e[35m'; BL=$'\e[34m'
  W=$'\e[97m'; N=$'\e[0m'
else
  B=""; D=""; G=""; R=""; Y=""; C=""; M=""; BL=""; W=""; N=""
fi

# Khung ve bang ky tu Unicode neu terminal la UTF-8, khong thi dung ASCII
UTF8=""
for loc in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8; do
  if locale -a 2>/dev/null | grep -qix "$loc"; then export LC_ALL=$loc; UTF8=1; break; fi
done
if [ -n "$UTF8" ]; then
  H="═"; V="║"; TL="╔"; TR="╗"; BLc="╚"; BR="╝"; ML="╠"; MR="╣"; DOT="●"; CP="©"
else
  H="="; V="|"; TL="+"; TR="+"; BLc="+"; BR="+"; ML="+"; MR="+"; DOT="*"; CP="(c)"
fi
BOX_W=66        # be rong ben trong khung
COPYRIGHT="Ban Quyen $CP $(date +%Y) FIX NHANH  -  License: FIX NHANH"
ok()   { echo "${G}$*${N}"; }
err()  { echo "${R}$*${N}"; }
warn() { echo "${Y}$*${N}"; }
PAD=""
pause() { echo; read -r -p "$PAD${D}Enter De Quay Lai Menu...${N}" _; }
pad_out() {  # day ket qua cua chuc nang vao giua man hinh (thang le trai khung menu)
  local l
  while IFS= read -r l || [ -n "$l" ]; do printf '%s%s\n' "$PAD" "$l"; done
}
my_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }

# ---------- tien ich ----------

instances() {  # ten cac instance da cai (theo file cau hinh)
  local f
  for f in "$CONF"/*.env; do
    [ -f "$f" ] && basename "$f" .env
  done
}

env_get() {  # env_get <instance> <BIEN> -> gia tri trong file cau hinh (bo dau nhay)
  ( set -a; . "$CONF/$1.env" >/dev/null 2>&1; eval "printf '%s' \"\${$2:-}\"" )
}

pick_instance() {  # chon 1 instance -> bien PICK
  local list=() i=1 n choice
  mapfile -t list < <(instances)
  if [ ${#list[@]} -eq 0 ]; then
    warn "Chua co exporter nao tren may nay (chon 1 de cai)."
    return 1
  fi
  for n in "${list[@]}"; do
    printf "  %d) %-12s vCenter %-16s port %s\n" "$i" "$n" "$(env_get "$n" VC)" "$(env_get "$n" PORT)"
    i=$((i + 1))
  done
  read -r -p "${PAD}Chon so (Enter = huy): " choice
  [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#list[@]} ] || return 1
  PICK=${list[$((choice - 1))]}
}

next_port() {  # port trong dau tien tu 9821
  local p=9821 used
  used=" $(for n in $(instances); do env_get "$n" PORT; echo -n " "; done)"
  while [[ "$used" == *" $p "* ]] || { command -v ss >/dev/null && ss -ltnH "sport = :$p" | grep -q .; }; do
    p=$((p + 1))
  done
  echo "$p"
}

ensure_pkg() {  # giai nen goi exporter canh script/ (lan dau hoac khi zip moi hon)
  command -v unzip >/dev/null || { err "Chua co unzip: apt install -y unzip  |  dnf install -y unzip"; return 1; }
  if [ ! -x "$PKG/install.sh" ] || [ "$ZIP" -nt "$PKG/install.sh" ]; then
    echo "-> Giai nen goi exporter"
    if [ -f "$ZIP.sha256" ]; then
      ( cd "$SCRIPT" && sha256sum -c "$(basename "$ZIP").sha256" >/dev/null ) \
        || { err "Zip khong khop sha256 - tai lai repo (muc 13)"; return 1; }
    fi
    ( cd "$SCRIPT" && unzip -oq "$(basename "$ZIP")" ) || { err "Giai nen loi"; return 1; }
    chmod +x "$PKG"/*.sh
  fi
}

metric() {  # metric <port> <ten metric> -> gia tri dau tien (rong neu khong co)
  curl -s --max-time 5 "http://127.0.0.1:$1/metrics" 2>/dev/null | awk -v m="$2" '$1==m {print $2; exit}'
}

dash_ready() {
  [ -f "$DASH_ENV" ] && [ -f "$DASH_APP" ] && return 0
  warn "Chua cai phan tu dong dashboard - chon 9 truoc."
  return 1
}

run_dash() {  # chay make_dashboard.py voi cau hinh cua timer (URL, token, datasource, hang)
  ( set -a; . "$DASH_ENV"; set +a
    python3 "$DASH_APP" --prometheus "$PROM_URL" --grafana "$GRAFANA_URL" ${GRAFANA_OPTS:-} \
      --datasource "${PROM_DS:-prometheus-all}" --row "${ROW:-}" --out /var/lib/vmware-cluster-dashboard "$@" )
}

reinstall_instance() {  # chay lai install.sh cho 1 instance voi cau hinh cu (khong hoi gi)
  ( set -a; . "$CONF/$1.env"; set +a; NAME=$1; export NAME
    [ -n "${INFLUX_ORG:-}" ] || unset INFLUX_URL  # InfluxDB 1.x: install.sh tu giu nguyen dong INFLUX_ cu
    cd "$PKG" && ./install.sh </dev/null )
}

# ---------- cac muc menu ----------

m_install() {
  ensure_pkg || return
  local name port
  echo "${B}Cai exporter cho 1 vCenter (pool) moi${N} - moi pool 1 ten + 1 port rieng."
  [ -n "$(instances)" ] && echo "Dang co: $(instances | tr '\n' ' ')"
  read -r -p "${PAD}Ten instance (vd pool5, chi chu/so/-): " name
  [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || { err "Ten khong hop le"; return; }
  if [ -f "$CONF/$name.env" ]; then
    warn "$name da co - dung muc 3 de doi user/mat khau, hoac nhap ten khac."
    return
  fi
  port=$(next_port)
  read -r -p "${PAD}Port metrics [$port]: " p
  port=${p:-$port}
  echo "Tiep theo nhap IP vCenter, user (quyen Read-only), mat khau (go khong hien)."
  ( cd "$PKG" && NAME=$name PORT=$port ./install.sh ) || true
  if systemctl is-active --quiet "$SVC@$name"; then
    echo
    ok "Xong. Viec tiep theo: them target $(my_ip):$port vao Prometheus (muc 7)."
  fi
}

m_status() {
  local n port st up last now age clusters
  now=$(date +%s)
  printf "${B}%-10s %-16s %-6s %-9s %-4s %-9s %s${N}\n" "Instance" "vCenter" "Port" "Service" "Up" "Lay luc" "Cluster"
  for n in $(instances); do
    port=$(env_get "$n" PORT)
    st=$(systemctl is-active "$SVC@$n" 2>/dev/null)
    up=$(metric "$port" vmware_cluster_exporter_up)
    last=$(metric "$port" vmware_cluster_exporter_last_success_timestamp_seconds)
    age="-"
    [ -n "$last" ] && age="$(( (now - ${last%.*}) ))s"
    clusters=$(curl -s --max-time 5 "http://127.0.0.1:$port/metrics" 2>/dev/null \
      | sed -n 's/^vmware_cluster_hosts{cluster="\([^"]*\)".*/\1/p' | tr '\n' ' ')
    [ "$st" = active ] && st="${G}active${N}   " || st="${R}${st:-?}${N} "
    [ "$up" = 1 ] && up="${G}1${N}  " || up="${R}${up:-0}${N}  "
    printf "%-10s %-16s %-6s %s %s %-9s %s\n" "$n" "$(env_get "$n" VC)" "$port" "$st" "$up" "$age" "${clusters:--}"
  done
  [ -z "$(instances)" ] && warn "Chua co exporter nao."
  echo
  if systemctl list-unit-files "$DASH_NAME.timer" >/dev/null 2>&1 && [ -f "$DASH_ENV" ]; then
    echo "Tu dong dashboard: timer $(systemctl is-active $DASH_NAME.timer), lan chay cuoi:"
    journalctl -u "$DASH_NAME.service" -n 3 --no-pager -o cat 2>/dev/null | sed 's/^/   /'
  else
    echo "Tu dong dashboard: chua cai (muc 9)."
  fi
  echo
  echo "Up=0 hoac service failed: xem log (muc 5). Sai mat khau: muc 3."
}

m_password() {
  ensure_pkg || return
  pick_instance || return
  local user
  user=$(env_get "$PICK" VC_USER)
  echo "Doi user/mat khau vCenter cho ${B}$PICK${N} (vCenter $(env_get "$PICK" VC))."
  read -r -p "${PAD}User vCenter [$user]: " u
  ( set -a; . "$CONF/$PICK.env"; set +a
    unset VC_PASS INFLUX_URL INFLUX_ORG INFLUX_BUCKET INFLUX_TOKEN
    NAME=$PICK VC_USER=${u:-$user}; export NAME VC_USER
    read -r -s -p "${PAD}Mat khau vCenter moi: " VC_PASS; echo
    [ -n "$VC_PASS" ] || { echo "Mat khau trong - huy."; exit 1; }
    export VC_PASS
    cd "$PKG" && ./install.sh ) || true
}

m_service() {
  pick_instance || return
  echo "  1) Khoi dong lai   2) Dung   3) Chay"
  read -r -p "${PAD}Chon: " a
  case "$a" in
    1) systemctl restart "$SVC@$PICK" ;;
    2) systemctl stop "$SVC@$PICK" ;;
    3) systemctl start "$SVC@$PICK" ;;
    *) return ;;
  esac
  sleep 2
  systemctl status "$SVC@$PICK" --no-pager -n 5 | sed -n '1,3p;/^$/,$p'
}

m_logs() {
  pick_instance || return
  journalctl -u "$SVC@$PICK" -n 40 --no-pager
}

m_uninstall() {
  echo "  1) Go 1 instance   2) Go TAT CA exporter tren may nay"
  read -r -p "${PAD}Chon: " a
  case "$a" in
    1) pick_instance || return
       read -r -p "${PAD}Go $PICK? (y/N): " y
       [[ "$y" =~ ^[yY] ]] || return
       systemctl disable --now "$SVC@$PICK" 2>/dev/null
       rm -f "$CONF/$PICK.env"
       ok "Da go $PICK. Nho xoa target cua no trong Prometheus." ;;
    2) read -r -p "${PAD}Go TAT CA exporter? (go 'yes'): " y
       [ "$y" = yes ] || return
       if [ -x /opt/vmware-cluster-exporter/uninstall.sh ]; then
         /opt/vmware-cluster-exporter/uninstall.sh
       else
         ensure_pkg && "$PKG/uninstall.sh"
       fi ;;
  esac
}

m_prometheus() {
  local ip n
  ip=$(my_ip)
  [ -n "$(instances)" ] || { warn "Chua co exporter nao."; return; }
  echo "Dan vao ${B}scrape_configs:${N} cua prometheus.yml tren server Prometheus"
  echo "(dau '-' cua '- job_name' thang cot voi cac job khac; da co job vmware_cluster thi chi them cac dong '- targets'):"
  echo
  echo "${C}  - job_name: 'vmware_cluster'"
  echo "    scrape_interval: 60s"
  echo "    scrape_timeout: 30s"
  echo "    static_configs:"
  for n in $(instances); do
    echo "      - targets: ['$ip:$(env_get "$n" PORT)']"
    echo "        labels:"
    echo "          pool: '$n'"
  done
  echo "${N}"
  echo "Sau do tren server Prometheus:"
  echo "  promtool check config /etc/prometheus/prometheus.yml && systemctl reload prometheus"
  echo "Mo port tren may nay cho Prometheus (thay <ip-prometheus>):"
  for n in $(instances); do
    echo "  ufw allow from <ip-prometheus> to any port $(env_get "$n" PORT) proto tcp"
  done
}

m_check() {
  local n port prom
  echo "${B}1) Exporter tren may nay${N}"
  for n in $(instances); do
    port=$(env_get "$n" PORT)
    if [ "$(metric "$port" vmware_cluster_exporter_up)" = 1 ]; then
      ok "  $n :$port  OK"
    else
      err "  $n :$port  KHONG lay duoc du lieu vCenter (muc 5 xem log)"
    fi
  done
  prom=""
  [ -f "$DASH_ENV" ] && prom=$(set -a; . "$DASH_ENV"; echo "$PROM_URL")
  read -r -p "${PAD}URL Prometheus can kiem tra [${prom:-http://<ip>:9090}]: " p
  prom=${p:-$prom}
  if [ -n "$prom" ]; then
    echo "${B}2) Prometheus $prom${N}"
    if curl -s --max-time 5 "$prom/-/ready" >/dev/null; then
      ok "  Ket noi duoc"
      local have
      have=$(curl -s --max-time 10 "$prom/api/v1/label/cluster/values?match%5B%5D=vmware_cluster_hosts")
      echo "  Cluster dang co tren Prometheus: $(echo "$have" | sed 's/.*"data":\[\(.*\)\].*/\1/')"
      for n in $(instances); do
        port=$(env_get "$n" PORT)
        for c in $(curl -s --max-time 5 "http://127.0.0.1:$port/metrics" \
                   | sed -n 's/^vmware_cluster_hosts{cluster="\([^"]*\)".*/\1/p'); do
          if [[ "$have" == *"\"$c\""* ]]; then ok "  $c ($n) da co tren Prometheus"
          else warn "  $c ($n) CHUA co - them target $(my_ip):$port vao Prometheus (muc 7)"; fi
        done
      done
    else
      err "  Khong ket noi duoc (firewall/IP sai)"
    fi
  fi
  if [ -f "$DASH_ENV" ]; then
    local g k
    g=$(set -a; . "$DASH_ENV"; echo "$GRAFANA_URL"); k=$(set -a; . "$DASH_ENV"; [ "${GRAFANA_OPTS:-}" = --insecure ] && echo -k)
    echo "${B}3) Grafana $g${N}"
    if curl -s $k --max-time 5 "$g/api/health" | grep -q '"database"'; then ok "  Ket noi duoc"
    else err "  Khong ket noi duoc"; fi
    echo "${B}4) Dashboard${N}"
    run_dash --list | sed 's/^/  /'
  fi
}

m_auto_dash() {
  bash "$SCRIPT/install_auto_dashboard.sh"
}

m_dash_now() {
  dash_ready || return
  install -m 644 "$SCRIPT/make_dashboard.py" "$DASH_APP"
  systemctl start "$DASH_NAME.service"
  journalctl -u "$DASH_NAME.service" -n 10 --no-pager -o cat
}

m_dash_list() {
  dash_ready || return
  run_dash --list
}

m_dash_remove() {
  dash_ready || return
  install -m 644 "$SCRIPT/make_dashboard.py" "$DASH_APP"
  run_dash --list
  echo
  read -r -p "${PAD}Ten cluster can xoa khoi dashboard (cach nhau dau cach, Enter = huy): " names
  [ -n "$names" ] || return
  # shellcheck disable=SC2086
  run_dash --remove $names
  warn "Luu y: cluster con tren Prometheus thi timer se tu them lai (<= 10 phut). Muon bo han: xoa target trong Prometheus."
}

m_update() {
  if [ -d "$REPO/.git" ]; then
    echo "-> git pull"
    git -C "$REPO" pull --ff-only || { err "git pull loi (co file sua tay?) - xem: git -C $REPO status"; return; }
  else
    warn "Thu muc khong phai git clone - bo qua git pull."
  fi
  ensure_pkg || return
  if [ -f "$DASH_APP" ]; then
    install -m 644 "$SCRIPT/make_dashboard.py" "$DASH_APP"
    ok "Da cap nhat $DASH_APP"
  fi
  local n
  for n in $(instances); do
    echo "-> Cap nhat exporter $n (giu cau hinh, mat khau cu)"
    reinstall_instance "$n" >/tmp/vmc-update.log 2>&1 \
      && ok "   $n OK" || { err "   $n loi:"; tail -5 /tmp/vmc-update.log; }
  done
}

m_remove_auto() {
  [ -f "$SCRIPT/install_auto_dashboard.sh" ] && bash "$SCRIPT/install_auto_dashboard.sh" --uninstall
}

# ---------- menu chinh ----------

# ---------- ve menu: khung mau, can giua man hinh ----------

ARR="→"; [ -n "$UTF8" ] || ARR="->"
MARGIN=""
rep() { local s="" i; for ((i = 0; i < $2; i++)); do s+=$1; done; printf '%s' "$s"; }
vis_len() { local s; s=$(printf '%s' "$1" | sed 's/\x1b\[[0-9;]*m//g'); echo "${#s}"; }
if [ -z "$UTF8" ]; then  # locale khong UTF-8: bash dem byte -> dem ky tu bang python3
  vis_len() {
    python3 -c 'import re, sys
s = sys.argv[1].encode("utf-8", "surrogateescape").decode("utf-8", "replace")
print(len(re.sub(r"\x1b\[[0-9;]*m", "", s)))' "$1"
  }
fi
row() {  # 1 dong trong khung, can trai
  local pad=$((BOX_W - $(vis_len "$1")))
  ((pad < 0)) && pad=0
  printf '%s%s%s%s%s%s%s%s\n' "$MARGIN" "$C" "$V" "$N" "$1" "$(rep ' ' $pad)" "$C$V" "$N"
}
center_row() { row "$(rep ' ' $(((BOX_W - $(vis_len "$1")) / 2)))$1"; }
border() { printf '%s%s%s%s%s%s\n' "$MARGIN" "$C" "$1" "$(rep "$H" $BOX_W)" "$2" "$N"; }
section() {  # ╠══ TEN NHOM ══════╣
  local title=" $2 "
  printf '%s%s%s%s%s%s%s%s\n' "$MARGIN" "$C" "$ML$H$H" "$1$B$title$N" "$C" \
    "$(rep "$H" $((BOX_W - 2 - $(vis_len "$title"))))" "$MR" "$N"
}
item() { row "   $2$B[$(printf '%2s' "$1")]$N  $3"; }
badge() { [ "$1" = ok ] && printf '%s' "$G$DOT $2$N" || printf '%s' "$R$DOT $2$N"; }

draw_menu() {
  local n total=0 run=0 exp auto
  for n in $(instances); do
    total=$((total + 1))
    systemctl is-active --quiet "$SVC@$n" && run=$((run + 1))
  done
  if [ "$total" -gt 0 ] && [ "$run" = "$total" ]; then exp=$(badge ok "$run/$total Dang Chay")
  else exp=$(badge no "$run/$total Dang Chay"); fi
  if [ ! -f "$DASH_ENV" ]; then auto=$(badge no "Chua Cai")
  elif systemctl is-active --quiet $DASH_NAME.timer; then auto=$(badge ok "Dang Bat")
  else auto=$(badge no "Dang Tat"); fi

  border "$TL" "$TR"
  center_row "$B${W}VMWARE CLUSTER HCM  $Y$DOT$N$B$W  MONITOR MENU$N"
  center_row "${D}May $(hostname)  $DOT  IP $(my_ip)$N"
  border "$ML" "$MR"
  row "  Exporter: $exp        Dashboard Tu Dong: $auto"
  section "$G" "EXPORTER  -  LAY SO LIEU VCENTER"
  item 1 "$G" "Cai Exporter Cho Pool / vCenter Moi"
  item 2 "$G" "Xem Trang Thai Cac Exporter"
  item 3 "$G" "Doi User / Mat Khau vCenter Cua 1 Pool"
  item 4 "$G" "Khoi Dong Lai / Dung / Chay 1 Exporter"
  item 5 "$G" "Xem Log 1 Exporter"
  item 6 "$G" "Go Exporter"
  section "$Y" "PROMETHEUS"
  item 7 "$Y" "In Cau Hinh Prometheus Cho Cac Exporter"
  item 8 "$Y" "Kiem Tra Ket Noi Exporter $ARR Prometheus $ARR Grafana"
  section "$M" "DASHBOARD GRAFANA"
  item 9 "$M" "Cai Tu Dong Them Pool Len Dashboard"
  item 10 "$M" "Them Pool Moi Len Dashboard Ngay"
  item 11 "$M" "Xem Hang Va Cluster Tren Dashboard"
  item 12 "$M" "Xoa Khoi 1 Cluster Khoi Dashboard"
  section "$BL" "KHAC"
  item 13 "$BL" "Cap Nhat Ban Moi Tu GitHub"
  item 14 "$BL" "Go Phan Tu Dong Dashboard"
  item 0 "$R" "Thoat"
  border "$BLc" "$BR"
  printf '%s%s%s%s\n' "$MARGIN" "$(rep ' ' $(((BOX_W + 2 - $(vis_len "$COPYRIGHT")) / 2)))" "$Y$COPYRIGHT" "$N"
}

header() {  # ve menu giua man hinh (ngang + doc)
  local cols lines out height top
  cols=$(tput cols 2>/dev/null || echo 80); lines=$(tput lines 2>/dev/null || echo 24)
  MARGIN=$(rep ' ' $(((cols - BOX_W - 2) / 2 > 0 ? (cols - BOX_W - 2) / 2 : 0)))
  PAD="$MARGIN  "
  out=$(draw_menu)
  height=$(printf '%s\n' "$out" | wc -l)
  top=$(((lines - height - 2) / 2))
  clear 2>/dev/null || true
  ((top > 0)) && rep $'\n' "$top"
  printf '%s\n\n' "$out"
}

while true; do
  header
  read -r -p "$MARGIN   ${B}Chon Chuc Nang [0-14]:${N} " choice || exit 0
  echo
  case "$choice" in
    1) fn=m_install; t="Cai Exporter Cho Pool / vCenter Moi" ;;
    2) fn=m_status; t="Trang Thai Cac Exporter" ;;
    3) fn=m_password; t="Doi User / Mat Khau vCenter" ;;
    4) fn=m_service; t="Khoi Dong Lai / Dung / Chay Exporter" ;;
    5) fn=m_logs; t="Log Exporter" ;;
    6) fn=m_uninstall; t="Go Exporter" ;;
    7) fn=m_prometheus; t="Cau Hinh Prometheus" ;;
    8) fn=m_check; t="Kiem Tra Ket Noi" ;;
    9) fn=m_auto_dash; t="Cai Tu Dong Them Pool Len Dashboard" ;;
    10) fn=m_dash_now; t="Them Pool Moi Len Dashboard" ;;
    11) fn=m_dash_list; t="Hang Va Cluster Tren Dashboard" ;;
    12) fn=m_dash_remove; t="Xoa Khoi Cluster Khoi Dashboard" ;;
    13) fn=m_update; t="Cap Nhat Ban Moi Tu GitHub" ;;
    14) fn=m_remove_auto; t="Go Phan Tu Dong Dashboard" ;;
    0|q|Q) echo "$MARGIN   ${Y}$COPYRIGHT${N}"; exit 0 ;;
    *) continue ;;
  esac
  clear 2>/dev/null || true
  echo
  border "$TL" "$TR"
  center_row "$B$W[$choice]  $t$N"
  border "$BLc" "$BR"
  echo
  "$fn" | pad_out
  pause
done
