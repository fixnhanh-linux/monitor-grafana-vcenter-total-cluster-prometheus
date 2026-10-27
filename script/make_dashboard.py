#!/usr/bin/env python3
"""Sinh dashboard Grafana "VMWARE CLUSTER HCM" - luoi o mau theo tung cluster.

Chay:  python make_dashboard.py            -> ghi vmware_cluster_hcm_grafana.json (+ ban trong vmware-cluster-exporter/)
Doi nguong mau: sua bang THRESHOLDS ben duoi roi chay lai, import lai Grafana (Overwrite).
Du lieu: metric vmware_cluster_* cua vmware-cluster-exporter (rieng, khong dung esxi-host-exporter).

Tu them pool / cluster moi (moi cluster 1 khoi 15 o, giong cac cluster cu):
  python3 make_dashboard.py --prometheus http://<ip>:9090
      -> hoi Prometheus danh sach cluster dang co metric vmware_cluster_hosts, them cluster moi vao cuoi
  GRAFANA_TOKEN=<service account token> python3 make_dashboard.py --prometheus http://<ip>:9090       --grafana http://<ip-grafana>:3000
      -> them nua: day thang len Grafana (ghi de dashboard uid vmware-cluster-hcm). Dat cron de tu dong.
Chi dung thu vien chuan Python 3 (chay duoc tren server Linux).
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

TITLE = "VMWARE CLUSTER HCM"
UID = "vmware-cluster-hcm"
PROM_DS = "prometheus-all"  # ten datasource Prometheus tren Grafana
DS = {"type": "prometheus", "uid": "${datasource}"}
C = 'cluster="$cluster"'  # $cluster duoc thay bang ten tung cluster trong CLUSTERS

# Nguong mau (xanh -> cam -> do). None = khong dung muc do.
THRESHOLDS = {
    "CPU Used": (20, 50),         # % CPU dang dung cua ca cluster
    "Ram Used": (80, 90),         # % RAM dang dung cua ca cluster
    "vSAN Used": (70, 80),        # % dung luong vSAN da dung
    "Host - Uptime": (None, 180),  # ngay - host chay lau nhat, do khi >= 180 ngay (can va/patch)
    "CPU Host (Max)": (30, 35),    # % CPU cua host cao nhat
    "RAM Host (Max)": (76, 80),    # % RAM cua host cao nhat
}

# (ten o, PromQL, don vi, kieu mau)  - thu tu = thu tu hien tren o
TILES = [
    # Diem Skyline Health (giong vCenter): >= 80 HEALTHY, 60-79 WARNING, < 60 ERROR.
    # Khong co diem (vSAN < 8 / khong vSAN) -> vSAN overall health -> trang thai cluster.
    ("Cluster Status", "(2 - (max(vmware_cluster_vsan_health_score{%s}) >= bool 60)"
                       " - (max(vmware_cluster_vsan_health_score{%s}) >= bool 80))"
                       " or max(vmware_cluster_vsan_health{%s}) or max(vmware_cluster_status{%s})" % (C, C, C, C),
     "none", "status"),
    ("DRS", "max(vmware_cluster_drs_mode{%s})" % C, "none", "drs"),
    ("Total Host", "max(vmware_cluster_hosts{%s})" % C, "none", "fixed"),
    ("Total VM", "max(vmware_cluster_vms_total{%s})" % C, "none", "fixed"),
    ("CPU Total", "max(vmware_cluster_cpu_total_hz{%s})" % C, "hertz", "fixed"),
    ("CPU Used", "max(vmware_cluster_cpu_used_hz{%s}) / max(vmware_cluster_cpu_total_hz{%s}) * 100" % (C, C),
     "percent", "thr"),
    ("Ram Total", "max(vmware_cluster_mem_total_bytes{%s})" % C, "decbytes", "fixed"),
    ("Ram Used", "max(vmware_cluster_mem_used_bytes{%s}) / max(vmware_cluster_mem_total_bytes{%s}) * 100" % (C, C),
     "percent", "thr"),
    ("vSAN Total", "max(vmware_cluster_vsan_capacity_bytes{%s})" % C, "bytes", "fixed"),
    ("vSAN Used", "(max(vmware_cluster_vsan_capacity_bytes{%s}) - max(vmware_cluster_vsan_free_bytes{%s}))"
                  " / max(vmware_cluster_vsan_capacity_bytes{%s}) * 100" % (C, C, C), "percent", "thr"),
    ("vSAN Free", "max(vmware_cluster_vsan_free_bytes{%s})" % C, "bytes", "fixed"),
    ("vSAN Resync", "max(vmware_cluster_vsan_resync_bytes{%s})" % C, "bytes", "resync"),
    ("Host - Uptime", "max(vmware_cluster_host_uptime_max_seconds{%s}) / 86400" % C, "suffix: d", "thr"),
    ("CPU Host (Max)", "max(vmware_cluster_host_cpu_max_percent{%s})" % C, "percent", "thr"),
    ("RAM Host (Max)", "max(vmware_cluster_host_mem_max_percent{%s})" % C, "percent", "thr"),
]

GREEN, ORANGE, RED, GRAY = "green", "orange", "red", "#8e8e8e"


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in pairs]}


def tile_override(ref, name, unit, kind):
    props = [{"id": "displayName", "value": name}, {"id": "unit", "value": unit}]
    if kind == "status":
        props += [{"id": "mappings", "value": [{"type": "value", "options": {
            "0": {"text": "HEALTHY", "color": GREEN, "index": 0},
            "1": {"text": "WARNING", "color": ORANGE, "index": 1},
            "2": {"text": "ERROR", "color": RED, "index": 2},
            "3": {"text": "UNKNOWN", "color": GRAY, "index": 3}}}]},
                  {"id": "thresholds", "value": steps((None, GREEN), (1, ORANGE), (2, RED), (3, GRAY))}]
    elif kind == "drs":
        props += [{"id": "mappings", "value": [{"type": "value", "options": {
            "0": {"text": "Tắt", "color": ORANGE, "index": 0},
            "1": {"text": "Manual", "color": GREEN, "index": 1},
            "2": {"text": "Partially Automated", "color": GREEN, "index": 2},
            "3": {"text": "Fully Automated", "color": GREEN, "index": 3}}}]},
                  {"id": "thresholds", "value": steps((None, ORANGE), (1, GREEN))}]
    elif kind == "resync":
        props += [{"id": "thresholds", "value": steps((None, GREEN), (1, ORANGE))}]
    elif kind == "thr":
        warn, crit = THRESHOLDS[name]
        st = [(None, GREEN)] + ([(warn, ORANGE)] if warn is not None else []) + [(crit, RED)]
        props += [{"id": "thresholds", "value": steps(*st)}, {"id": "decimals", "value": 0 if unit.startswith("suffix") else 1}]
    else:  # fixed
        props += [{"id": "thresholds", "value": steps((None, GREEN))}]
    if unit in ("hertz", "decbytes", "bytes"):
        props.append({"id": "decimals", "value": 2 if unit == "hertz" else 1})
    return {"matcher": {"id": "byFrameRefID", "options": ref}, "properties": props}


# Ten cluster dung nhu tren vCenter (nhan cluster cua metric). Them/bot cluster: sua danh sach roi chay lai.
CLUSTERS = ["CORE", "POOL17", "POOL18-XD", "POOL23", "POOL25", "VC1_POOL6"]
PER_ROW = 3          # so cluster moi hang
TILE_W, TILE_H = 2, 3  # 1 o = 2/24 chieu ngang -> 4 o / cluster / hang
# Bo cuc 1 cluster (4 cot x 4 hang): Cluster Status rong gap doi, 14 o con lai moi o 1 cot
WIDTHS = [2] + [1] * (len(TILES) - 1)
BLOCK_W, BLOCK_H = 24 // PER_ROW, 1 + TILE_H * 4  # 1 khoi = tieu de + 4 hang o


def tile_panel(pid, cluster, x, y, w, tile):
    name, expr, unit, kind = tile
    props = tile_override("A", name, unit, kind)["properties"]
    defaults = {"color": {"mode": "thresholds"}, "noValue": "–"}
    for prop in props:
        defaults[prop["id"]] = prop["value"]
    return {
        "id": pid, "type": "stat", "title": "", "datasource": DS, "transparent": False,
        "gridPos": {"x": x, "y": y, "w": w, "h": TILE_H},
        "targets": [{"datasource": DS, "refId": "A", "expr": expr.replace("$cluster", cluster),
                     "legendFormat": name, "instant": True, "range": False}],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"colorMode": "background", "graphMode": "none", "textMode": "value_and_name",
                    "justifyMode": "center", "orientation": "auto", "wideLayout": True,
                    "text": {"titleSize": 12, "valueSize": 16},
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
    }


def prometheus_clusters(url):
    """Ten cac cluster dang co so lieu tren Prometheus (label cluster cua vmware_cluster_hosts)."""
    q = urllib.parse.urlencode({"match[]": "vmware_cluster_hosts"})
    with urllib.request.urlopen("%s/api/v1/label/cluster/values?%s" % (url.rstrip("/"), q), timeout=20,
                                context=SSL_CTX) as r:
        data = json.load(r)
    if data.get("status") != "success":
        sys.exit("Prometheus tra loi loi: %s" % data)
    return data["data"]


def push_grafana(url, token, dash, n, folder_uid=None):
    body = {"dashboard": dict(dash, id=None), "overwrite": True,
            "message": "make_dashboard.py: %d cluster" % n}
    if folder_uid:
        body["folderUid"] = folder_uid
    req = urllib.request.Request(url.rstrip("/") + "/api/dashboards/db", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        return json.load(r)


def get_grafana(url, token, uid):
    """Dashboard dang co tren Grafana (None neu chua co) va UID thu muc chua no."""
    req = urllib.request.Request("%s/api/dashboards/uid/%s" % (url.rstrip("/"), uid),
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    return data["dashboard"], data.get("meta", {}).get("folderUid") or None


def all_panels(dash):
    for p in dash.get("panels", []):
        yield p
        for q in p.get("panels", []):  # panel ben trong hang (row) dang thu gon
            yield q


def clusters_on(dash):
    """Ten cluster da co o tren dashboard (doc tu PromQL cluster="..." cua moi o)."""
    names = set()
    for p in all_panels(dash):
        for t in p.get("targets", []):
            names.update(re.findall(r'cluster="([^"]+)"', t.get("expr", "")))
    return names


def block_panels(pid, cluster, bx, by):
    """1 khoi cua 1 cluster: thanh tieu de + 15 o, goc tren trai (bx, by)."""
    panels = [{  # thanh tieu de cua cluster (chi co title + link sang dashboard host)
        "id": pid, "type": "text", "title": cluster, "transparent": True,
        "gridPos": {"x": bx, "y": by, "w": BLOCK_W, "h": 1},
        "links": [{"title": "Chi tiết host của " + cluster,
                   "url": "/d/esxi-host-resource/?var-cluster=%s&${datasource:queryparam}" % cluster}],
        "options": {"mode": "markdown", "content": ""}}]
    col, row = 0, 0
    for tile, w in zip(TILES, WIDTHS):
        if col + w > 4:
            col, row = 0, row + 1
        panels.append(tile_panel(pid + len(panels), cluster, bx + col * TILE_W, by + 1 + row * TILE_H,
                                 w * TILE_W, tile))
        col += w
    return panels


def row_datasource(section):
    """Datasource cua hang: uid co dinh dung nhieu nhat trong hang (vd Prometheus HN), khong co -> ${datasource}."""
    uids = [p["datasource"]["uid"] for p in section
            if isinstance(p.get("datasource"), dict) and not str(p["datasource"].get("uid", "$")).startswith("$")]
    return max(set(uids), key=uids.count) if uids else DS["uid"]


def add_blocks(dash, clusters, row_title=None):
    """Chen khoi cluster moi vao hang (row) co tieu de chua row_title (mac dinh hang dau), 3 khoi / hang khoi.
    Khoi moi dat vao o trong dau tien (khong de len panel dang co); o datasource lay theo hang do.
    Hang dang mo ma het cho -> day cac hang ben duoi xuong. Hang dang thu gon -> them vao ben trong hang."""
    panels = dash.setdefault("panels", [])
    rows = sorted((p for p in panels if p.get("type") == "row"), key=lambda p: p["gridPos"]["y"])
    if row_title:
        match = [r for r in rows if row_title.lower() in r.get("title", "").lower()]
        if not match:
            sys.exit("Khong thay hang '%s'. Cac hang: %s" % (row_title, ", ".join(r.get("title", "") for r in rows)))
        row = match[0]
    else:
        row = rows[0] if rows else None
    top = row["gridPos"]["y"] if row else -1
    nxt = [r["gridPos"]["y"] for r in rows if r["gridPos"]["y"] > top]
    end = min(nxt) if nxt else float("inf")
    collapsed = bool(row and row.get("collapsed"))
    if collapsed:  # panel ben trong hang thu gon giu toa do luc mo -> tinh theo toa do cua chung
        section = row.setdefault("panels", [])
        if section:
            top = min(p["gridPos"]["y"] for p in section) - 1
    else:
        section = [p for p in panels if p.get("type") != "row" and top < p["gridPos"]["y"] < end]
    uid = row_datasource(section)
    pid = max([p.get("id", 0) for p in all_panels(dash)] + [0]) + 1

    def free(bx, by):
        return not any(p["gridPos"]["x"] < bx + BLOCK_W and bx < p["gridPos"]["x"] + p["gridPos"].get("w", 1)
                       and p["gridPos"]["y"] < by + BLOCK_H and by < p["gridPos"]["y"] + p["gridPos"].get("h", 1)
                       for p in section)

    for cluster in clusters:
        n = 0
        while not free((n % PER_ROW) * BLOCK_W, top + 1 + (n // PER_ROW) * BLOCK_H):
            n += 1
        bx, by = (n % PER_ROW) * BLOCK_W, top + 1 + (n // PER_ROW) * BLOCK_H
        if not collapsed and by + BLOCK_H > end:  # het cho trong hang: day cac hang ben duoi xuong
            shift = by + BLOCK_H - end
            for p in panels:
                if p["gridPos"]["y"] >= end:
                    p["gridPos"]["y"] += shift
            end += shift
        block = block_panels(pid, cluster, bx, by)
        for p in block:
            for obj in [p] + p.get("targets", []):
                if "datasource" in obj:
                    obj["datasource"] = {"type": "prometheus", "uid": uid}
        (row["panels"] if collapsed else panels).extend(block)
        if not collapsed:
            section.extend(block)
        pid += len(block)
        print("  + %s -> hang '%s' (x=%d, y=%d, datasource %s)" % (cluster, row.get("title") if row else "-",
                                                                 bx, by, uid))


def hide_datasource_picker(dash):
    """An o chon datasource tren dashboard (giu nguyen gia tri dang chon). Tra ve True neu co thay doi."""
    changed = False
    for v in dash.get("templating", {}).get("list", []):
        if v.get("type") == "datasource" and v.get("hide") != 2:
            v["hide"] = 2
            changed = True
    return changed


ap = argparse.ArgumentParser(description="Sinh dashboard Grafana VMWARE CLUSTER HCM")
ap.add_argument("--prometheus", help="URL Prometheus de tu tim cluster moi, vd http://10.0.0.x:9090")
ap.add_argument("--grafana", help="URL Grafana de day dashboard len (token lay tu bien GRAFANA_TOKEN)")
ap.add_argument("--folder-uid", help="UID thu muc Grafana (bo trong = giu thu muc hien tai / General)")
ap.add_argument("--out", help="Thu muc ghi file JSON (mac dinh canh script)")
ap.add_argument("--state", help="(khong con dung, giu de tuong thich lenh cu)")
ap.add_argument("--row", help="Chen cluster moi vao hang (row) co tieu de chua chuoi nay, vd HN (mac dinh hang dau)")
ap.add_argument("--rebuild", action="store_true",
                help="Sinh lai toan bo dashboard (mat chinh sua tay); mac dinh chi chen khoi cluster moi")
ap.add_argument("--insecure", action="store_true",
                help="Bo qua kiem tra chung chi HTTPS (Grafana/Prometheus dung chung chi tu ky)")
ap.add_argument("--datasource", default=PROM_DS,
                help="Ten datasource Prometheus tren Grafana co metric vmware_cluster_* (mac dinh %(default)s)")
args = ap.parse_args()
PROM_DS = args.datasource
SSL_CTX = ssl._create_unverified_context() if args.insecure else None
TOKEN = os.environ.get("GRAFANA_TOKEN")
if args.grafana and not TOKEN:
    sys.exit("Thieu bien moi truong GRAFANA_TOKEN (service account token, quyen Editor)")
found = prometheus_clusters(args.prometheus) if args.prometheus else []

here = os.path.dirname(os.path.abspath(__file__))
outdir = args.out or here
if not args.out and os.path.isdir(os.path.join(here, "..", "dashboard-grafana")):
    outdir = os.path.normpath(os.path.join(here, "..", "dashboard-grafana"))  # repo: script/ -> dashboard-grafana/
outs = [os.path.join(outdir, "vmware_cluster_hcm_grafana.json")]
if not args.out and os.path.isdir(os.path.join(here, "vmware-cluster-exporter")):
    outs.append(os.path.join(here, "vmware-cluster-exporter", "vmware_cluster_hcm_grafana.json"))


def save_and_push(dash, n, folder_uid):
    for out in outs:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(dash, f, ensure_ascii=False, indent=2)
        print("Da ghi", out, "-", n, "cluster")
    if args.grafana:
        r = push_grafana(args.grafana, TOKEN, dash, n, folder_uid)
        print("Da day len Grafana:", r.get("status"), r.get("url"))


# 1) Dashboard da co tren Grafana: giu nguyen moi chinh sua tay (tieu de, datasource, khoi/hang tu them),
#    chi chen khoi cho cluster moi vao cuoi phan dau tien.
existing, folder = (None, None)
if args.grafana and not args.rebuild:
    existing, folder = get_grafana(args.grafana, TOKEN, UID)
if existing is not None:
    present = clusters_on(existing)
    new = sorted(c for c in found if c not in present)
    hidden = hide_datasource_picker(existing)
    if not new and not hidden:
        print("Khong co cluster moi (%d cluster tren dashboard) - giu nguyen dashboard" % len(present))
        sys.exit(0)
    if hidden:
        print("An o chon datasource tren dashboard")
    if new:
        print("Cluster moi tu Prometheus:", ", ".join(new))
        add_blocks(existing, new, args.row)
    save_and_push(existing, len(present) + len(new), args.folder_uid or folder)
    sys.exit(0)

# 2) Chua co dashboard (hoac --rebuild): sinh moi toan bo tu CLUSTERS + cluster tren Prometheus.
new = sorted(c for c in found if c not in CLUSTERS)
if new:
    print("Cluster moi tu Prometheus:", ", ".join(new))
CLUSTERS += new  # cluster cu giu nguyen thu tu, cluster moi them vao cuoi
panels = [{"id": 1, "type": "row", "title": TITLE, "collapsed": False,
           "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}, "panels": []}]
pid = 2
for i, cluster in enumerate(CLUSTERS):
    block = block_panels(pid, cluster, (i % PER_ROW) * BLOCK_W, 1 + (i // PER_ROW) * BLOCK_H)
    panels += block
    pid += len(block)

dash = {
    "title": TITLE, "uid": UID, "tags": ["vmware", "vsan", "cluster", "vmware-cluster-exporter"],
    "description": "Tổng quan từng cluster VMware: trạng thái, DRS, host, VM, CPU/RAM, vSAN, resync, host cao nhất.",
    "timezone": "browser", "editable": True, "refresh": "1m", "schemaVersion": 39, "version": 1,
    "time": {"from": "now-1h", "to": "now"},
    "templating": {"list": [
        # Co dinh nguon PROM_DS (an o chon). Doi nguon: --datasource hoac sua PROM_DS roi chay lai.
        {"name": "datasource", "label": "Prometheus", "type": "datasource", "query": "prometheus",
         "regex": "/^%s$/" % PROM_DS, "hide": 2, "refresh": 1,
         "current": {"selected": True, "text": PROM_DS, "value": PROM_DS}},
    ]},
    "annotations": {"list": []},
    "panels": panels,
}
save_and_push(dash, len(CLUSTERS), args.folder_uid)
