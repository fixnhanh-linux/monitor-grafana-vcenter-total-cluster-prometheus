#!/usr/bin/env python3
"""Lam viec voi Zabbix qua API (Zabbix 6.0+) cho template "VMware Cluster HCM By VMware Cluster Exporter".

Token API lay tu bien moi truong ZBX_TOKEN (menu.sh hoi, go khong hien), khong ghi ra dia, khong in ra.

  zabbix_tool.py --url URL [--insecure] version
  zabbix_tool.py --url URL [--insecure] import FILE.yaml     # import/cap nhat template + chay lai discovery
  zabbix_tool.py --url URL [--insecure] proxies              # liet ke proxy: "id<TAB>ten"
  zabbix_tool.py --url URL [--insecure] groups               # nhom host goi y
  zabbix_tool.py --url URL [--insecure] addhost --name N --ip IP --port 9821 [--proxy ID] [--group G]
                                               [--matches REGEX] [--not-matches REGEX]
  zabbix_tool.py --url URL [--insecure] checknow             # chay lai discovery moi host gan template
  zabbix_tool.py --url URL [--insecure] autosync --instances pool18:9821,pool25:9822 --ip IP [--proxy ID]
                                               [--group G] [--template FILE.yaml]  # timer: tao host cho pool moi

Ma thoat: 0 OK, 1 loi, 3 loi chung chi SSL (chung chi tu ky -> chay lai voi --insecure).
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

TPL = "VMware Cluster HCM By VMware Cluster Exporter"
MASTER_KEY = "vmc.metrics"


class SSLFail(Exception):
    pass


class Zabbix:
    def __init__(self, url, insecure=False, token=""):
        url = url.rstrip("/")
        if not url.endswith("api_jsonrpc.php"):
            url += "/api_jsonrpc.php"
        if "://" not in url:
            url = "http://" + url
        self.url, self.token, self.n = url, token, 0
        self.ctx = ssl._create_unverified_context() if insecure else None
        self.ver = None

    def call(self, method, params, auth=True):
        self.n += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self.n}
        headers = {"Content-Type": "application/json-rpc"}
        if auth:
            if self.ver is None:
                self.version()
            if self.ver >= (6, 4):
                headers["Authorization"] = "Bearer " + self.token
            else:
                body["auth"] = self.token
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.ctx) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise SSLFail(str(e.reason))
            raise SystemExit("Khong ket noi duoc %s: %s" % (self.url, e.reason))
        except ValueError:
            raise SystemExit("%s khong tra ve JSON - sai URL? (vd http://ip/zabbix)" % self.url)
        if "error" in res:
            e = res["error"]
            msg = "%s: %s %s" % (method, e.get("message", ""), e.get("data", ""))
            if "Not authorized" in msg or "Session terminated" in msg or "API token" in msg:
                msg += "\n-> Token sai/het han hoac khong du quyen (can quyen Admin tren nhom host/template)."
            raise SystemExit(msg)
        return res["result"]

    def version(self):
        if self.ver is None:
            v = self.call("apiinfo.version", {}, auth=False)
            self.ver = tuple(int(x) for x in v.split(".")[:2])
            self.ver_str = v
        return self.ver

    def template_id(self):
        r = self.call("template.get", {"output": ["templateid"], "filter": {"host": [TPL]}})
        return r[0]["templateid"] if r else None


def import_rules(ver):
    full = {"createMissing": True, "updateExisting": True, "deleteMissing": True}
    rules = {"templates": {"createMissing": True, "updateExisting": True},
             "items": full, "triggers": full, "discoveryRules": full, "graphs": full,
             "valueMaps": full, "templateLinkage": {"createMissing": True}}
    if ver >= (6, 2):
        rules["template_groups"] = {"createMissing": True}
    else:
        rules["groups"] = {"createMissing": True}
    return rules


def linked_hosts(z, tid):
    return z.call("host.get", {"output": ["hostid", "host", "name"], "templateids": [tid]})


def check_now(z, hosts):
    """Lay du lieu ngay cho item chinh -> discovery (item phu thuoc) chay lai, tao/xoa trigger theo template moi."""
    if not hosts:
        return
    items = z.call("item.get", {"output": ["itemid", "hostid"], "hostids": [h["hostid"] for h in hosts],
                                "filter": {"key_": [MASTER_KEY]}})
    if items:
        z.call("task.create", [{"type": 6, "request": {"itemid": i["itemid"]}} for i in items])
    names = {h["hostid"]: h["name"] for h in hosts}
    for i in items:
        print("  Chay lai discovery: %s" % names.get(i["hostid"], i["hostid"]))


def cmd_import(z, a):
    with open(a.file, encoding="utf-8") as f:
        src = f.read()
    z.call("configuration.import", {"format": "yaml", "source": src, "rules": import_rules(z.version())})
    tid = z.template_id()
    print("Da import / cap nhat template \"%s\" (Zabbix %s)." % (TPL, z.ver_str))
    hosts = linked_hosts(z, tid) if tid else []
    print("Host dang gan template: %d" % len(hosts))
    check_now(z, hosts)
    if hosts:
        print("Khoang 1-2 phut sau trigger cua cac host doi theo template moi.")


def cmd_proxies(z, a):
    if z.version() >= (7, 0):
        r = z.call("proxy.get", {"output": ["proxyid", "name"]})
        rows = [(p["proxyid"], p["name"]) for p in r]
    else:
        r = z.call("proxy.get", {"output": ["proxyid", "host"]})
        rows = [(p["proxyid"], p["host"]) for p in r]
    for pid, name in sorted(rows, key=lambda x: x[1].lower()):
        print("%s\t%s" % (pid, name))


def cmd_groups(z, a):
    """Nhom cua cac host dang gan template (goi y mac dinh)."""
    tid = z.template_id()
    seen = []
    if tid:
        for h in z.call("host.get", {"output": ["hostid"], "templateids": [tid], "selectHostGroups" if z.version() >= (6, 2) else "selectGroups": ["name"]}):
            for g in h.get("hostgroups") or h.get("groups") or []:
                if g["name"] not in seen:
                    seen.append(g["name"])
    for g in seen:
        print(g)


def group_id(z, name):
    g = z.call("hostgroup.get", {"output": ["groupid"], "filter": {"name": [name]}})
    if g:
        return g[0]["groupid"]
    print("Da tao nhom host \"%s\"." % name)
    return z.call("hostgroup.create", {"name": name})["groupids"][0]


def create_host(z, tid, name, ip, port, proxy="", group="VMware", matches="", not_matches=""):
    """Tao host Zabbix cho 1 instance exporter, gan template + macro port / loc cluster. Tra ve hostid."""
    macros = [{"macro": "{$VMC.PORT}", "value": str(port)}]
    if matches and matches != ".*":
        macros.append({"macro": "{$VMC.CLUSTER.MATCHES}", "value": matches})
    if not_matches:
        macros.append({"macro": "{$VMC.CLUSTER.NOT_MATCHES}", "value": not_matches})
    p = {"host": name, "name": name,
         "interfaces": [{"type": 1, "main": 1, "useip": 1, "ip": ip, "dns": "", "port": "10050"}],
         "groups": [{"groupid": group_id(z, group)}], "templates": [{"templateid": tid}], "macros": macros,
         "tags": [{"tag": "monitor", "value": "vmware"}]}
    if proxy:
        if z.version() >= (7, 0):
            p.update({"monitored_by": 1, "proxyid": proxy})
        else:
            p["proxy_hostid"] = proxy
    hid = z.call("host.create", p)["hostids"][0]
    print("Da tao host \"%s\" (IP %s, port exporter %s%s), gan template." %
          (name, ip, port, ", qua proxy" if proxy else ""))
    return hid


def cmd_addhost(z, a):
    tid = z.template_id()
    if not tid:
        raise SystemExit("Chua co template \"%s\" tren Zabbix - import truoc (menu muc 17)." % TPL)
    if z.call("host.get", {"output": ["hostid"], "filter": {"host": [a.name]}}):
        raise SystemExit("Host \"%s\" da co tren Zabbix - doi ten khac hoac sua tren giao dien Zabbix." % a.name)
    hid = create_host(z, tid, a.name, a.ip, a.port, a.proxy, a.group, a.matches, a.not_matches)
    check_now(z, [{"hostid": hid, "name": a.name}])
    print("Khoang 1-2 phut sau vao Monitoring > Latest data, loc host \"%s\" de xem so lieu." % a.name)


def cmd_autosync(z, a):
    """Timer: moi instance exporter tren may (ten:port) chua co host Zabbix -> tao host, gan template.
    Da co = co host gan template voi macro {$VMC.PORT} = port va interface IP = --ip (ten host bat ky,
    ke ca host tao tay / loc cluster). Khong sua, khong xoa host nao."""
    tid = z.template_id()
    if not tid and a.template:
        with open(a.template, encoding="utf-8") as f:
            z.call("configuration.import", {"format": "yaml", "source": f.read(), "rules": import_rules(z.version())})
        print("Chua co template tren Zabbix -> da import \"%s\"." % TPL)
        tid = z.template_id()
    if not tid:
        raise SystemExit("Chua co template \"%s\" tren Zabbix - import truoc (menu muc 17)." % TPL)
    hosts = z.call("host.get", {"output": ["hostid", "host"], "templateids": [tid],
                                "selectMacros": ["macro", "value"], "selectInterfaces": ["ip"]})
    have = set()
    for h in hosts:
        ports = {m["value"] for m in h.get("macros", []) if m["macro"] == "{$VMC.PORT}"} or {"9821"}  # mac dinh template
        ips = {i["ip"] for i in h.get("interfaces", [])}
        have |= {(ip, p) for ip in ips for p in ports}
    created = []
    for inst in filter(None, a.instances.split(",")):
        name, _, port = inst.partition(":")
        if not port.isdigit():
            print("Bo qua \"%s\": khong co port" % inst)
            continue
        if (a.ip, port) in have:
            continue
        host = "%s%s" % (a.prefix, name.upper())
        if z.call("host.get", {"output": ["hostid"], "filter": {"host": [host]}}):
            print("Bo qua %s (port %s): da co host \"%s\" nhung khong dung IP/port - kiem tra tren Zabbix" % (name, port, host))
            continue
        hid = create_host(z, tid, host, a.ip, port, a.proxy, a.group)
        created.append({"hostid": hid, "name": host})
    check_now(z, created)
    print("Them %d host moi (%d instance, %d host dang gan template)." %
          (len(created), len(list(filter(None, a.instances.split(",")))), len(hosts)))


def cmd_checknow(z, a):
    tid = z.template_id()
    if not tid:
        raise SystemExit("Chua co template tren Zabbix.")
    check_now(z, linked_hosts(z, tid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--insecure", action="store_true")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("version")
    i = sp.add_parser("import")
    i.add_argument("file")
    sp.add_parser("proxies")
    sp.add_parser("groups")
    sp.add_parser("checknow")
    h = sp.add_parser("addhost")
    h.add_argument("--name", required=True)
    h.add_argument("--ip", required=True)
    h.add_argument("--port", required=True, type=int)
    h.add_argument("--proxy", default="")
    h.add_argument("--group", default="VMware")
    h.add_argument("--matches", default="")
    h.add_argument("--not-matches", default="")
    s = sp.add_parser("autosync")
    s.add_argument("--instances", required=True, help="ten:port,ten:port (vd pool18:9821,pool25:9822)")
    s.add_argument("--ip", required=True, help="IP may chay exporter (interface cua host Zabbix)")
    s.add_argument("--proxy", default="")
    s.add_argument("--group", default="VMware")
    s.add_argument("--prefix", default="VMware Cluster ", help="Ten host = prefix + TEN INSTANCE viet hoa")
    s.add_argument("--template", default="", help="File YAML import khi Zabbix chua co template")
    a = ap.parse_args()
    z = Zabbix(a.url, a.insecure, os.environ.get("ZBX_TOKEN", ""))
    try:
        if a.cmd == "version":
            print("Zabbix %s - ket noi OK" % (z.version() and z.ver_str))
            if os.environ.get("ZBX_TOKEN"):
                z.call("host.get", {"output": ["hostid"], "limit": 1})
                print("Token OK")
            return
        {"import": cmd_import, "proxies": cmd_proxies, "groups": cmd_groups,
         "addhost": cmd_addhost, "checknow": cmd_checknow, "autosync": cmd_autosync}[a.cmd](z, a)
    except SSLFail as e:
        print("Loi chung chi SSL: %s" % e, file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
