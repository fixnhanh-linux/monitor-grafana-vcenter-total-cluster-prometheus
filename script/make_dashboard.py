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


DS_CACHE = {}


def grafana_json(path):
    req = urllib.request.Request(args.grafana.rstrip("/") + path, headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        return json.load(r)


def grafana_datasources():
    """{uid: ten} cac datasource Prometheus tren Grafana (rong neu token khong duoc xem danh sach)."""
    if "_list" not in DS_CACHE:
        try:
            DS_CACHE["_list"] = {d["uid"]: d.get("name", "") for d in grafana_json("/api/datasources")
                                 if d.get("type") == "prometheus"}
        except Exception:
            DS_CACHE["_list"] = {}
    return DS_CACHE["_list"]


def resolve_ds(dash, uid):
    """uid that: ${datasource} -> gia tri dang chon cua bien; ten datasource -> uid."""
    if str(uid).startswith("$"):
        uid = None
        for v in dash.get("templating", {}).get("list", []):
            if v.get("type") == "datasource":
                cur = v.get("current") or {}
                uid = cur.get("value") or cur.get("text")
    names = {name: u for u, name in grafana_datasources().items()}
    return names.get(uid, uid)


def ds_clusters(uid):
    """Cac cluster co tren datasource (hoi qua Grafana); None = khong hoi duoc."""
    if uid not in DS_CACHE:
        try:
            q = urllib.parse.urlencode({"match[]": "vmware_cluster_hosts"})
            data = grafana_json("/api/datasources/proxy/uid/%s/api/v1/label/cluster/values?%s" % (uid, q))
            DS_CACHE[uid] = set(data.get("data") or [])
        except Exception:
            DS_CACHE[uid] = None
    return DS_CACHE[uid]


def pick_datasource(dash, cluster, preferred):
    """Datasource cho khoi cua cluster: uu tien `preferred` (datasource cua hang) neu no co cluster do,
    khong thi datasource Prometheus khac tren Grafana co cluster do (vd Prometheus site HN). Khong ro -> preferred."""
    cands = [preferred]
    seen = {resolve_ds(dash, preferred)}
    others = list(grafana_datasources()) + [p["datasource"]["uid"] for p in all_panels(dash)
                                            if isinstance(p.get("datasource"), dict)
                                            and not str(p["datasource"].get("uid", "$")).startswith("$")]
    for u in others:
        if u not in seen:
            seen.add(u)
            cands.append(u)
    for u in cands:
        names = ds_clusters(resolve_ds(dash, u))
        if names and cluster in names:
            return u
    return preferred


def fix_datasources(dash):
    """O nao query 1 cluster ma datasource cua o khong co cluster do (o hien '-') -> doi sang datasource co no."""
    changed = {}
    for p in all_panels(dash):
        names = {c for t in p.get("targets", []) for c in re.findall(r'cluster="([^"]+)"', t.get("expr", ""))}
        if len(names) != 1 or not isinstance(p.get("datasource"), dict):
            continue
        cluster = names.pop()
        cur = p["datasource"].get("uid", "${datasource}")
        have = ds_clusters(resolve_ds(dash, cur))
        if have is None or cluster in have:
            continue
        new = pick_datasource(dash, cluster, cur)
        if new == cur:
            continue
        for obj in [p] + p.get("targets", []):
            if "datasource" in obj:
                obj["datasource"] = {"type": "prometheus", "uid": new}
        changed.setdefault(cluster, [new, 0])[1] += 1
    for cluster, (uid, n) in sorted(changed.items()):
        name = grafana_datasources().get(uid, "")
        print("  ~ %s: %d o -> datasource %s%s" % (cluster, n, uid, " (%s)" % name if name else ""))
    return bool(changed)


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
        cuid = pick_datasource(dash, cluster, uid)  # Prometheus thuc su co cluster nay (vd site HN)
        for p in block:
            for obj in [p] + p.get("targets", []):
                if "datasource" in obj:
                    obj["datasource"] = {"type": "prometheus", "uid": cuid}
        (row["panels"] if collapsed else panels).extend(block)
        if not collapsed:
            section.extend(block)
        pid += len(block)
        name = grafana_datasources().get(resolve_ds(dash, cuid), "")
        print("  + %s -> hang '%s' (x=%d, y=%d, datasource %s%s)" % (
            cluster, row.get("title") if row else "-", bx, by, cuid, " = %s" % name if name else ""))


def remove_clusters(dash, clusters):
    """Xoa khoi cua cac cluster (thanh tieu de + cac o query cluster do), ca trong hang thu gon. Tra ve so panel xoa."""
    names = {c.lower() for c in clusters}

    def belongs(p):
        found = {c.lower() for t in p.get("targets", []) for c in re.findall(r'cluster="([^"]+)"', t.get("expr", ""))}
        found |= {c.lower() for l in p.get("links", []) for c in re.findall(r"var-cluster=([^&]+)", l.get("url", ""))}
        if p.get("type") == "text" and p.get("title", "").lower() in names:
            return True
        return bool(found) and found <= names

    removed = 0
    for holder in [dash] + [p for p in dash.get("panels", []) if p.get("type") == "row"]:
        keep = [p for p in holder.get("panels", []) if p.get("type") == "row" or not belongs(p)]
        removed += len(holder.get("panels", [])) - len(keep)
        holder["panels"] = keep
    return removed


def list_rows(dash):
    """In tung hang (row) cua dashboard: datasource va cac cluster trong hang."""
    panels = dash.get("panels", [])
    rows = sorted((p for p in panels if p.get("type") == "row"), key=lambda p: p["gridPos"]["y"])
    ys = [r["gridPos"]["y"] for r in rows] + [float("inf")]
    groups = [("(khong co hang)", [p for p in panels if p.get("type") != "row" and p["gridPos"]["y"] < ys[0]])]
    for i, r in enumerate(rows):
        inside = r.get("panels") if r.get("collapsed") else \
            [p for p in panels if p.get("type") != "row" and ys[i] < p["gridPos"]["y"] < ys[i + 1]]
        groups.append((r.get("title", ""), inside or []))
    for title, section in groups:
        if not section and title == "(khong co hang)":
            continue
        names = sorted(clusters_on({"panels": section}))
        print("%-28s datasource %-14s %d cluster: %s" % (title, row_datasource(section), len(names),
                                                        ", ".join(names) or "-"))


# ---------- sap xep vi tri khoi (--arrange): xem truoc tren SSH, luu moi day len Grafana ----------

def panel_cluster(p):
    """Cluster ma 1 panel thuoc ve (tu PromQL cluster="..." hoac link var-cluster=...)."""
    names = {c for t in p.get("targets", []) for c in re.findall(r'cluster="([^"]+)"', t.get("expr", ""))}
    names |= {c for l in p.get("links", []) for c in re.findall(r"var-cluster=([^&]+)", l.get("url", ""))}
    return names.pop() if len(names) == 1 else None


def sections(dash):
    """Moi hang (row): {title, row, cont (list chua panel), top, end, collapsed}."""
    panels = dash.setdefault("panels", [])
    rows = sorted((p for p in panels if p.get("type") == "row"), key=lambda p: p["gridPos"]["y"])
    out = []
    for i, r in enumerate(rows):
        top = r["gridPos"]["y"]
        end = rows[i + 1]["gridPos"]["y"] if i + 1 < len(rows) else float("inf")
        if r.get("collapsed"):
            kids = r.setdefault("panels", [])
            top = min(p["gridPos"]["y"] for p in kids) - 1 if kids else top
            out.append({"title": r.get("title", ""), "row": r, "cont": kids, "top": top, "end": float("inf"),
                        "collapsed": True})
        else:
            out.append({"title": r.get("title", ""), "row": r, "cont": panels, "top": top, "end": end,
                        "collapsed": False})
    if not rows:
        out.append({"title": "(khong co hang)", "row": None, "cont": panels, "top": -1, "end": float("inf"),
                    "collapsed": False})
    return out


def sec_panels(sec):
    return [p for p in sec["cont"] if p.get("type") != "row"
            and (sec["collapsed"] or sec["top"] < p["gridPos"]["y"] < sec["end"])]


def blocks_of(dash):
    """Danh sach khoi: {cluster, title, sec (chi so hang), panels, col, line}."""
    out = []
    for si, sec in enumerate(sections(dash)):
        groups = {}
        for p in sec_panels(sec):
            c = panel_cluster(p)
            if c:
                groups.setdefault(c, []).append(p)
        for c, ps in groups.items():
            bx = min(p["gridPos"]["x"] for p in ps)
            by = min(p["gridPos"]["y"] for p in ps)
            head = [p for p in ps if p.get("type") == "text" and p.get("title")]
            out.append({"cluster": c, "title": head[0]["title"] if head else c, "sec": si, "panels": ps,
                        "key": min(p.get("id", 0) for p in ps),  # id co dinh -> so thu tu khoi khong doi khi di chuyen
                        "col": min(PER_ROW - 1, int(round(bx / BLOCK_W))),
                        "line": max(0, (by - sec["top"] - 1) // BLOCK_H)})
    out.sort(key=lambda b: (b["sec"], b["line"], b["col"], b["cluster"]))
    return out


def slot_xy(sec, col, line):
    return col * BLOCK_W, sec["top"] + 1 + line * BLOCK_H


def move_block(dash, b, col, line):
    """Dich ca khoi toi o (col, line) trong hang cua no; hang dang mo ma vuot cho -> day cac hang duoi xuong."""
    sec = sections(dash)[b["sec"]]
    x, y = slot_xy(sec, col, line)
    if not sec["collapsed"] and y + BLOCK_H > sec["end"]:
        shift = y + BLOCK_H - sec["end"]
        mine = {id(p) for p in b["panels"]}
        for p in dash["panels"]:
            if p["gridPos"]["y"] >= sec["end"] and id(p) not in mine:
                p["gridPos"]["y"] += shift
    bx = min(p["gridPos"]["x"] for p in b["panels"])
    by = min(p["gridPos"]["y"] for p in b["panels"])
    for p in b["panels"]:
        p["gridPos"]["x"] += x - bx
        p["gridPos"]["y"] += y - by


def move_block_to_section(dash, b, si, slot=None):
    """Chuyen khoi (giu nguyen cac o, datasource) sang hang si, vao o slot=(col, line) hoac o trong dau tien."""
    secs = sections(dash)
    src, dst = secs[b["sec"]], secs[si]
    for p in b["panels"]:
        src["cont"].remove(p)
    if slot is None:
        taken = {(o["col"], o["line"]) for o in blocks_of(dash) if o["sec"] == si}
        n = 0
        while (n % PER_ROW, n // PER_ROW) in taken:
            n += 1
        slot = (n % PER_ROW, n // PER_ROW)
    dst["cont"].extend(b["panels"])
    y0 = min(q["gridPos"]["y"] for q in b["panels"])
    for p in b["panels"]:  # tam dat vao dau hang de khoi thuoc hang moi, roi dich vao o can toi
        p["gridPos"]["y"] += dst["top"] + 1 - y0
    move_block(dash, dict(b, sec=si), slot[0], slot[1])


def compact(dash):
    """Bo cac dong khoi trong giua cac khoi trong moi hang (khoi ben duoi dich len)."""
    for si in range(len(sections(dash))):
        used = sorted({b["line"] for b in blocks_of(dash) if b["sec"] == si})
        for new, old in enumerate(used):
            if new != old:
                for b in [x for x in blocks_of(dash) if x["sec"] == si and x["line"] == old]:
                    move_block(dash, b, b["col"], new)


def drop_block(dash, b, si, col, line):
    """Tha khoi b vao o (col, line) cua hang si. O co khoi -> doi cho (ke ca khac hang). Tra ve cau thong bao."""
    if (si, col, line) == (b["sec"], b["col"], b["line"]):
        return ""
    others = [o for o in blocks_of(dash) if o["sec"] == si and (o["col"], o["line"]) == (col, line)
              and o["key"] != b["key"]]
    src = (b["sec"], b["col"], b["line"])
    if si == b["sec"]:
        for o in others:
            move_block(dash, o, b["col"], b["line"])
        move_block(dash, b, col, line)
    else:
        for o in others:  # khoi o o dich sang cho cu cua b (hang cu)
            move_block_to_section(dash, o, src[0], (src[1], src[2]))
        b = [x for x in blocks_of(dash) if x["key"] == b["key"]][0]
        move_block_to_section(dash, b, si, (col, line))
    if others:
        return "Da doi cho %s <-> %s." % (b["title"], ", ".join(o["title"] for o in others))
    return "Da chuyen %s." % b["title"]


def arrange_tui(dash):
    """Giao dien keo tha bang chuot (curses) tren SSH. Tra ve True neu nguoi dung xac nhan luu."""
    import copy
    import curses

    before = copy.deepcopy(dash)
    compact(dash)  # don cac dong trong co san (vd tu lan sap xep truoc)
    order = [b["key"] for b in blocks_of(dash)]
    state = {"undo": [], "sel": order[0] if order else None, "msg": "", "drag": None, "changed": False}
    if dash != before:
        state.update(undo=[before], changed=True, msg="Da don cac dong trong giua cac khoi (bam u de giu nguyen).")
    CW, CH = 24, 2  # be rong o (ke ca vien trai), cao o (dong chu + vien duoi)

    def num(b):
        if b["key"] not in order:
            order.append(b["key"])
        return order.index(b["key"]) + 1

    def layout(scr):
        """Ve so do, tra ve {(si, col, line): (y, x)} toa do tung o tren man hinh."""
        h, w = scr.getmaxyx()
        width = PER_ROW * CW + 1
        x0 = max(0, (w - width) // 2)
        cells, y = {}, 1
        blocks = blocks_of(dash)

        def put(yy, xx, s, attr=0):
            if 0 <= yy < h - 1 and xx < w:
                try:
                    scr.addstr(yy, xx, s[:max(0, w - xx - 1)], attr)
                except curses.error:
                    pass

        title = "VMWARE CLUSTER HCM  -  SAP XEP VI TRI KHOI (KEO THA)"
        put(0, max(0, (w - len(title)) // 2), title, curses.A_BOLD | curses.color_pair(3))
        for si, sec in enumerate(sections(dash)):
            mine = [b for b in blocks if b["sec"] == si]
            y += 1
            put(y, x0, "HANG %d: %s%s" % (si + 1, sec["title"], "  (dang thu gon)" if sec["collapsed"] else ""),
                curses.A_BOLD | curses.color_pair(1))
            y += 1
            last = max([b["line"] for b in mine] + [-1]) + 1  # them 1 hang khoi trong de tha xuong duoi
            put(y, x0, "+" + ("-" * (CW - 1) + "+") * PER_ROW)
            for ln in range(last + 1):
                y += 1
                for col in range(PER_ROW):
                    cx = x0 + col * CW
                    cells[(si, col, ln)] = (y, cx)
                    here = [b for b in mine if (b["col"], b["line"]) == (col, ln)]
                    txt = ", ".join("%d %s" % (num(b), b["title"]) for b in here)
                    attr = 0
                    if any(b["key"] == state["sel"] for b in here):
                        attr = curses.A_REVERSE | curses.A_BOLD | curses.color_pair(2)
                    if state["drag"] and state["drag"].get("over") == (si, col, ln):
                        attr = curses.A_REVERSE | curses.color_pair(3)
                    put(y, cx, "|")
                    put(y, cx + 1, (" " + txt).ljust(CW - 1)[:CW - 1], attr)
                put(y, x0 + PER_ROW * CW, "|")
                y += 1
                put(y, x0, "+" + ("-" * (CW - 1) + "+") * PER_ROW)
        y += 2
        help1 = "CHUOT: bam giu khoi, keo sang o khac roi tha  |  hoac bam khoi roi bam o can toi"
        help2 = "PHIM: Tab chon khoi | mui ten di chuyen | u hoan tac | s luu | q thoat"
        put(y, max(0, (w - len(help1)) // 2), help1, curses.color_pair(3))
        put(y + 1, max(0, (w - len(help2)) // 2), help2, curses.color_pair(3))
        if state["msg"]:
            put(y + 3, max(0, (w - len(state["msg"])) // 2), state["msg"], curses.A_BOLD | curses.color_pair(2))
        return cells

    def cell_at(cells, my, mx):
        for k, (cy, cx) in cells.items():
            if cy == my and cx <= mx < cx + CW:
                return k
        return None

    def do(fn):
        before = copy.deepcopy(dash)
        msg = fn()
        if msg:
            compact(dash)  # khong de lai dong trong giua cac khoi
            state["undo"].append(before)
            state["changed"] = True
        state["msg"] = msg or state["msg"]

    def sel_block():
        return next((b for b in blocks_of(dash) if b["key"] == state["sel"]), None)

    def confirm(scr, q):
        h, w = scr.getmaxyx()
        scr.addstr(h - 2, max(0, (w - len(q)) // 2), q[:w - 1], curses.A_BOLD | curses.color_pair(3))
        scr.refresh()
        while True:
            k = scr.getch()
            if k in (ord("y"), ord("Y")):
                return True
            if k in (ord("n"), ord("N"), ord("q")):
                return False

    def main(scr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)  # nhan rieng su kien bam / tha de keo tha
        sys.stdout.write("\033[?1002h")  # bao vi tri chuot khi dang giu nut (keo)
        sys.stdout.flush()
        try:
            while True:
                scr.erase()
                cells = layout(scr)
                scr.refresh()
                k = scr.getch()
                if k == curses.KEY_MOUSE:
                    try:
                        _, mx, my, _, bs = curses.getmouse()
                    except curses.error:
                        continue
                    c = cell_at(cells, my, mx)
                    if bs & curses.BUTTON1_PRESSED:
                        here = [b for b in blocks_of(dash) if c and (b["sec"], b["col"], b["line"]) == c]
                        if here:
                            state["drag"] = {"key": here[0]["key"], "from": c, "over": c}
                            state["sel"] = here[0]["key"]
                        elif c and state["sel"] is not None:  # bam khoi truoc, bam o trong sau -> chuyen
                            b = sel_block()
                            do(lambda: drop_block(dash, b, *c))
                    elif bs & curses.BUTTON1_RELEASED:
                        d, state["drag"] = state["drag"], None
                        if d and c and c != d["from"]:
                            b = next(x for x in blocks_of(dash) if x["key"] == d["key"])
                            do(lambda: drop_block(dash, b, *c))
                    elif state["drag"] and c:  # dang keo: to o dang tro toi
                        state["drag"]["over"] = c
                    elif bs & curses.BUTTON1_CLICKED and c:  # terminal chi bao "click"
                        here = [b for b in blocks_of(dash) if (b["sec"], b["col"], b["line"]) == c]
                        if here:
                            state["sel"] = here[0]["key"]
                        elif state["sel"] is not None:
                            b = sel_block()
                            do(lambda: drop_block(dash, b, *c))
                elif k in (9, curses.KEY_BTAB):  # Tab: chon khoi ke tiep
                    keys = [b["key"] for b in blocks_of(dash)]
                    if keys:
                        i = keys.index(state["sel"]) if state["sel"] in keys else -1
                        state["sel"] = keys[(i + (1 if k == 9 else -1)) % len(keys)]
                elif k in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT,
                           ord("w"), ord("a"), ord("d")):
                    b = sel_block()
                    if not b:
                        continue
                    dc, dl = {curses.KEY_UP: (0, -1), ord("w"): (0, -1), curses.KEY_DOWN: (0, 1),
                              curses.KEY_LEFT: (-1, 0), ord("a"): (-1, 0), curses.KEY_RIGHT: (1, 0),
                              ord("d"): (1, 0)}[k]
                    col, line = b["col"] + dc, b["line"] + dl
                    nsec = len(sections(dash))
                    blocks = blocks_of(dash)
                    last = max([o["line"] for o in blocks if o["sec"] == b["sec"] and o["key"] != b["key"]] + [-1])

                    def free_slot(si, want):
                        """O trong trong hang si: uu tien want=(col, line), khong thi o trong dau tien."""
                        taken = {(o["col"], o["line"]) for o in blocks if o["sec"] == si and o["key"] != b["key"]}
                        if want not in taken:
                            return want
                        n = 0
                        while (n % PER_ROW, n // PER_ROW) in taken:
                            n += 1
                        return n % PER_ROW, n // PER_ROW

                    if dl == 1 and line > last + 1 and b["sec"] + 1 < nsec:  # xuong qua cuoi hang -> sang hang duoi
                        si = b["sec"] + 1
                        do(lambda: drop_block(dash, b, si, *free_slot(si, (b["col"], 0))))
                    elif line < 0 and b["sec"] > 0:  # len qua dau hang -> xuong cuoi hang tren
                        si = b["sec"] - 1
                        plast = max([o["line"] for o in blocks if o["sec"] == si] + [-1])
                        do(lambda: drop_block(dash, b, si, *free_slot(si, (b["col"], plast + 1))))
                    elif 0 <= col < PER_ROW and line >= 0:
                        do(lambda: drop_block(dash, b, b["sec"], col, line))
                elif k == ord("u"):
                    if state["undo"]:
                        prev = state["undo"].pop()
                        dash.clear()
                        dash.update(prev)
                        state["msg"] = "Da hoan tac."
                    else:
                        state["msg"] = "Khong con buoc nao de hoan tac."
                elif k in (ord("s"), ord("l")):
                    if confirm(scr, "Luu so do nay len dashboard Grafana? (y/n)"):
                        return True
                    state["msg"] = "Chua luu."
                elif k == ord("q"):
                    if not state["changed"] or confirm(scr, "Thoat va BO cac thay doi? (y/n)"):
                        return False
        finally:
            sys.stdout.write("\033[?1002l")
            sys.stdout.flush()

    return curses.wrapper(main)


def draw_layout(dash, order, mark=None):
    """Ve so do cac khoi theo tung hang (o 3 cot) de xem truoc tren SSH. order: key khoi theo so thu tu."""
    G, Y, C, B, N = "\033[32m", "\033[33m", "\033[36m", "\033[1m", "\033[0m"
    if os.environ.get("NO_COLOR"):
        G = Y = C = B = N = ""
    cw = 22
    blocks = blocks_of(dash)
    lines = []
    for si, sec in enumerate(sections(dash)):
        mine = [b for b in blocks if b["sec"] == si]
        lines.append("%s%sHANG %d: %s%s%s" % (C, B, si + 1, sec["title"], " (dang thu gon)" if sec["collapsed"] else "", N))
        sep = "+" + ("-" * cw + "+") * PER_ROW
        lines.append(sep)
        last = max([b["line"] for b in mine] + [-1])
        for ln in range(last + 1):
            cells = []
            for col in range(PER_ROW):
                here = [b for b in mine if b["line"] == ln and b["col"] == col]
                txt = ", ".join("%d %s" % (order.index(b["key"]) + 1, b["title"]) for b in here)
                txt = txt[:cw - 2].ljust(cw - 2)
                if mark is not None and any(b["key"] == mark for b in here):
                    txt = G + B + txt + N
                cells.append(" " + txt + " ")
            lines.append("|" + "|".join(cells) + "|")
            lines.append(sep)
        if last < 0:
            lines.append("|" + "|".join([" " * cw] * PER_ROW) + "|")
            lines.append(sep)
        lines.append("")
    print("\n".join(lines))


ARRANGE_HELP = """Lenh (go roi Enter):
  5 w | 5 len      dua khoi so 5 len 1 hang khoi     5 s | 5 xuong   xuong
  5 a | 5 trai     sang trai                         5 d | 5 phai    sang phai
  5 7              doi cho khoi 5 va khoi 7
  5 h2             chuyen khoi 5 sang HANG 2 (vao o trong dau tien)
  u                hoan tac buoc vua lam             ?               xem lai huong dan
  l                LUU len Grafana (hoi xac nhan)    q               thoat, KHONG luu"""


def arrange(dash):
    """Vong lap sap xep tren SSH. Tra ve True neu nguoi dung xac nhan luu."""
    import copy
    undo, mark, msg = [], None, ""
    order = [b["key"] for b in blocks_of(dash)]  # so thu tu co dinh trong ca phien
    print(ARRANGE_HELP)
    while True:
        print()
        draw_layout(dash, order, mark)
        if msg:
            print(msg)
        msg = ""
        try:
            cmd = input("Lenh (? = huong dan, l = luu, q = thoat): ").strip().lower().split()
        except EOFError:
            return False
        if not cmd:
            continue
        blocks = blocks_of(dash)
        by_num = {order.index(b["key"]) + 1: b for b in blocks}
        if cmd[0] in ("q", "thoat"):
            return False
        if cmd[0] == "?":
            print(ARRANGE_HELP)
            continue
        if cmd[0] == "u":
            if undo:
                dash.clear()
                dash.update(undo.pop())
                msg = "Da hoan tac."
            else:
                msg = "Khong con buoc nao de hoan tac."
            continue
        if cmd[0] in ("l", "luu"):
            yn = input("Luu so do nay len dashboard Grafana? (y/N): ").strip().lower()
            if yn in ("y", "yes", "co"):
                return True
            msg = "Chua luu."
            continue
        if not cmd[0].isdigit() or int(cmd[0]) not in by_num or len(cmd) < 2:
            msg = "Lenh khong hop le. Vd: 5 w | 5 7 | 5 h2 (go ? de xem huong dan)"
            continue
        b = by_num[int(cmd[0])]
        act = cmd[1] if len(cmd) == 2 else cmd[1] + cmd[2]
        before = copy.deepcopy(dash)
        dirs = {"w": (0, -1), "len": (0, -1), "s": (0, 1), "xuong": (0, 1),
                "a": (-1, 0), "trai": (-1, 0), "d": (1, 0), "phai": (1, 0)}
        if act in dirs:
            col, line = b["col"] + dirs[act][0], b["line"] + dirs[act][1]
            if not (0 <= col < PER_ROW) or line < 0:
                msg = "Khoi %s da o sat mep, khong di duoc." % b["title"]
                continue
            other = [o for o in blocks if o["sec"] == b["sec"] and (o["col"], o["line"]) == (col, line)]
            if other:  # o dich co khoi -> doi cho
                for o in other:
                    move_block(dash, o, b["col"], b["line"])
            move_block(dash, b, col, line)
            msg = "Da dua %s %s." % (b["title"], {"w": "len", "s": "xuong", "a": "sang trai", "d": "sang phai"}.get(
                act, act))
        elif act.isdigit() and int(act) in by_num:
            o = by_num[int(act)]
            if o is b:
                continue
            if o["sec"] != b["sec"]:
                msg = "Hai khoi o 2 hang khac nhau - dung lenh '%s h%d' truoc de dua ve cung hang." % (
                    cmd[0], o["sec"] + 1)
                continue
            bc, bl = b["col"], b["line"]
            move_block(dash, b, o["col"], o["line"])
            move_block(dash, o, bc, bl)
            msg = "Da doi cho %s <-> %s." % (b["title"], o["title"])
        elif re.fullmatch(r"(h|hang)\d+", act):
            si = int(re.sub(r"\D", "", act)) - 1
            if not (0 <= si < len(sections(dash))) or si == b["sec"]:
                msg = "So hang khong hop le."
                continue
            move_block_to_section(dash, b, si)
            b = dict(b, sec=si)
            msg = "Da chuyen %s sang HANG %d." % (b["title"], si + 1)
        else:
            msg = "Lenh khong hop le. Go ? de xem huong dan."
            continue
        undo.append(before)
        mark = b["key"]


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
ap.add_argument("--fix-datasource", action="store_true",
                help="Khoi nao hien '-' vi datasource khong co cluster do -> doi sang Prometheus tren Grafana co cluster")
ap.add_argument("--arrange", action="store_true",
                help="Sap xep vi tri khoi tren SSH (len/xuong/trai/phai, doi cho, doi hang), xem truoc roi moi luu")
ap.add_argument("--rows", action="store_true", help="In ten cac hang (row) tren dashboard, moi dong 1 ten")
ap.add_argument("--move", nargs="+", metavar="CLUSTER",
                help="Chuyen khoi cluster sang hang --row, vd --move POOL5 --row HN")
ap.add_argument("--list", action="store_true", help="Liet ke hang / datasource / cluster tren dashboard roi thoat")
ap.add_argument("--remove", nargs="+", metavar="CLUSTER",
                help="Xoa khoi cua cluster khoi dashboard tren Grafana roi thoat, vd --remove POOL5")
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
if args.list:
    if existing is None:
        sys.exit("Chua co dashboard %s tren Grafana (can --grafana va GRAFANA_TOKEN)" % UID)
    print("Dashboard: %s (uid %s)" % (existing.get("title"), UID))
    list_rows(existing)
    if found:
        miss = sorted(c for c in found if c not in clusters_on(existing))
        print("Cluster tren Prometheus chua co tren dashboard:", ", ".join(miss) or "khong co")
    sys.exit(0)
if args.arrange:  # sap xep vi tri khoi tren SSH, chi day len Grafana khi nguoi dung xac nhan luu
    if existing is None:
        sys.exit("Chua co dashboard %s tren Grafana (can --grafana va GRAFANA_TOKEN)" % UID)
    use_tui = not os.environ.get("ARRANGE_TEXT")
    if use_tui and not (sys.stdin.isatty() and sys.stdout.isatty()):
        # Bi goi qua pipe (vd menu can giua ket qua): mo thang terminal cua phien SSH cho giao dien keo tha
        try:
            tty = os.open("/dev/tty", os.O_RDWR)
            sys.stdout.flush()
            os.dup2(tty, 0)
            os.dup2(tty, 1)
            sys.stdin = open(0, "r", closefd=False)
            sys.stdout = open(1, "w", closefd=False)
        except OSError:
            use_tui = False
    if use_tui:
        try:
            import curses  # noqa: F401
        except ImportError:
            use_tui = False
    if arrange_tui(existing) if use_tui else arrange(existing):
        save_and_push(existing, len(clusters_on(existing)), args.folder_uid or folder)
    else:
        print("Thoat, KHONG luu - dashboard tren Grafana giu nguyen.")
    sys.exit(0)
if args.rows:  # ten cac hang, moi dong 1 ten (menu.sh dung de cho chon)
    if existing is None:
        sys.exit("Chua co dashboard %s tren Grafana" % UID)
    for r in sorted((p for p in existing.get("panels", []) if p.get("type") == "row"),
                    key=lambda p: p["gridPos"]["y"]):
        print(r.get("title", ""))
    sys.exit(0)
if args.move:  # chuyen khoi cluster sang hang --row: xoa khoi cu, chen lai vao hang moi (datasource tu chon)
    if existing is None:
        sys.exit("Chua co dashboard %s tren Grafana (can --grafana va GRAFANA_TOKEN)" % UID)
    if not args.row:
        sys.exit("Can --row <ten hang> de chuyen toi")
    n = remove_clusters(existing, args.move)
    if not n:
        sys.exit("Khong thay khoi nao cua: %s" % ", ".join(args.move))
    print("Go %d panel cu cua: %s" % (n, ", ".join(args.move)))
    add_blocks(existing, args.move, args.row)
    save_and_push(existing, len(clusters_on(existing)), args.folder_uid or folder)
    sys.exit(0)
if args.remove:
    if existing is None:
        sys.exit("Chua co dashboard %s tren Grafana (can --grafana va GRAFANA_TOKEN)" % UID)
    n = remove_clusters(existing, args.remove)
    if not n:
        sys.exit("Khong thay khoi nao cua: %s" % ", ".join(args.remove))
    print("Da xoa %d panel cua: %s" % (n, ", ".join(args.remove)))
    save_and_push(existing, len(clusters_on(existing)), args.folder_uid or folder)
    sys.exit(0)
if existing is not None:
    present = clusters_on(existing)
    new = sorted(c for c in found if c not in present)
    hidden = hide_datasource_picker(existing)
    if hidden:
        print("An o chon datasource tren dashboard")
    if new:
        print("Cluster moi tu Prometheus:", ", ".join(new))
        add_blocks(existing, new, args.row)
    fixed = False
    if args.fix_datasource:
        print("Kiem tra datasource tung khoi (o hien '-' vi datasource khong co cluster):")
        fixed = fix_datasources(existing)
        if not fixed:
            print("  Khong co khoi nao can doi datasource")
    if not new and not hidden and not fixed:
        print("Khong co cluster moi (%d cluster tren dashboard) - giu nguyen dashboard" % len(present))
        sys.exit(0)
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
