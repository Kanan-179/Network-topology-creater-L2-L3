#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLDP topologiya - Cisco IOS-XR (NCS) + IOS-XE (ASR 920 / 901)

Cihazlara qosulur, "show lldp neighbors detail" oxuyur ve qonsular arasinda baglantilari qurur.
YALNIZ OXUYUR - konfiqurasiyada hecne deyismir.

Cixis: <ad>_lldp_topology_<tarix>.drawio / .png / .xlsx / _log.txt

Qurulum : pip install netmiko colorama openpyxl matplotlib
Isletmek: python lldp_topology.py [ips.txt]
Dayandir: Ctrl+C  (o ana qeder toplanan melumatla topologiya yene de cizilir)
"""
import os
import re
import sys
import time
import getpass
import ipaddress
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from xml.sax.saxutils import quoteattr

try:
    from netmiko import ConnectHandler, SSHDetect
except ImportError as _e:      # pencere ani baglanmasin: xetani goster ve ENTER gozle
    print("XETA: netmiko modulu tapilmadi ->", _e)
    print("Qurulum: python -m pip install -r requirements.txt   (eyni Python ile isletdiyine emin ol)")
    input("Press ENTER to exit.")
    sys.exit(1)

try:
    import colorama
    colorama.init()
except ImportError:
    os.system("")

# ----------------------------- AYARLAR --------------------------------
MAX_WORKERS = 8        # eyni anda nece cihaza qosulsun (yalniz oxuma oldugu ucun tehlukesizdir)
CRAWL_DEPTH = 2        # avtomatik kesf rejiminde maksimum derinlik
NODE_W, NODE_H = 190, 56
DX, DY = 270, 200      # cihazlar arasi mesafe (en / hundurluk)
# -----------------------------------------------------------------------

W = "\033[97m"
R = "\033[91m"
G = "\033[92m"
B = "\033[1;94m"
Y = "\033[93m"
X = "\033[0m"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG = None

TYPE_COLORS = {            # (doldurma, cizgi)
    "xr": ("#dae8fc", "#6c8ebf"),     # IOS-XR (mavi)
    "xe": ("#d5e8d4", "#82b366"),     # IOS-XE (yasil)
    "other": ("#f5f5f5", "#9a9a9a"),  # qosulmadigim / tanimadigim qonsu (boz)
}
TYPE_LABEL = {"xr": "IOS-XR", "xe": "IOS-XE", "other": "qonsu (qosulmayib)"}


def out(s=""):
    print(s)
    if LOG:
        LOG.write(ANSI.sub("", s) + "\n")
        LOG.flush()


def white(t):
    return f"{W}{t}{X}"


def ipc(ip):
    return f"{B}{ip}{X}"


def cmdc(c):
    return f'{R}"{G}{c}{R}"{X}'


# ----------------------------- adlar ----------------------------------
SHORT = [("twentyfivegige", "Twe"), ("hundredgige", "Hu"), ("fortygige", "Fo"),
         ("tengigabitethernet", "Te"), ("tengige", "Te"), ("gigabitethernet", "Gi"),
         ("fastethernet", "Fa"), ("bundle-ether", "BE"), ("port-channel", "Po")]
BUNDLE_RE = re.compile(r"(?i)^(bundle|be\d|port-channel|po\d)")
MAC_RE = re.compile(r"^([0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}$|^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")


def short_if(n):
    n = n.strip()
    low = n.lower()
    for a, b in SHORT:
        if low.startswith(a):
            return b + n[len(a):]
    return n


def canon(name):
    """Cihazin tek (unikal) acari: domen atilir, kicik herf"""
    return name.strip().split(".")[0].lower()


def disp(name):
    return name.strip().split(".")[0]


def type_from_desc(d):
    l = d.lower()
    if re.search(r"\bncs\b|ncs-|ios xr|asr ?9\d\d\d|\bcrs\b", l):
        return "xr"
    if "ios" in l or "asr" in l or "cisco" in l:
        return "xe"
    return "other"


# ------------------------------ parse ---------------------------------
def parse_detail(o):
    """show lldp neighbors detail -> qonsu girisleri (XR ve XE formati)"""
    ents = []
    for block in re.split(r"^-{10,}\s*$", o, flags=re.M):
        loc = re.search(r"^Local (?:Intf|Interface):[ \t]*(\S+)", block, re.M)
        pid = re.search(r"^Port id:[ \t]*(.+?)[ \t]*$", block, re.M)
        if not loc or not pid:
            continue

        def g(rx):
            m = re.search(rx, block, re.M)
            return m.group(1).strip() if m else ""

        ip = ""
        i = block.find("Management Address")
        if i >= 0:
            m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", block[i:])
            ip = m.group(1) if m else ""
        dm = re.search(r"^System Description:(.*?)(?=^Time remaining|\n[ \t]*\n)", block, re.M | re.S)
        ents.append({
            "local": loc.group(1),
            "parent": g(r"^Parent Interface:[ \t]*(\S+)"),
            "pid": pid.group(1).strip(),
            "pdesc": g(r"^Port Description:[ \t]*(.*)$"),
            "sysname": g(r"^System Name:[ \t]*(.*)$"),
            "chassis": g(r"^Chassis id:[ \t]*(\S+)"),
            "ip": ip,
            "desc": " ".join(dm.group(1).split()) if dm else "",
        })
    return ents


def parse_etherchannel(o):
    """XE: {uzv port (kicik herf, qisa): 'Po4'}"""
    res = {}
    for l in o.splitlines():
        m = re.match(r"^\s*\d+\s+(Po\d+)\(", l)
        if m:
            for mem in re.findall(r"([A-Za-z]+\d+(?:/\d+)+)\(\w+\)", l):
                res[short_if(mem).lower()] = m.group(1)
    return res


def clean_entries(raw, po_map=None):
    """Bundle tipli port-id girisleri (uzaq bundle adi) ayrilir, fiziki baglantilar qalir"""
    po_map = po_map or {}
    remote_bundle = {}
    phys = []
    for e in raw:
        name = canon(e["sysname"] or e["chassis"])
        if BUNDLE_RE.match(e["pid"]):
            remote_bundle[(e["local"].lower(), name)] = short_if(e["pid"])
        else:
            phys.append(e)
    for e in phys:
        name = canon(e["sysname"] or e["chassis"])
        e["rbundle"] = remote_bundle.get((e["local"].lower(), name), "")
        e["lbundle"] = short_if(e["parent"]) if e["parent"] else po_map.get(short_if(e["local"]).lower(), "")
        port = e["pid"]
        if MAC_RE.match(port) and e["pdesc"]:
            port = e["pdesc"]            # port id MAC-dirsa Port Description istifade et
        e["rport"] = short_if(port)
        e["lport"] = short_if(e["local"])
        e["rname"] = disp(e["sysname"] or e["chassis"])
    return phys


# ----------------------------- qosulma --------------------------------
def _connect_once(ip, user, pw, log):
    dtype = "cisco_xe"
    try:
        g = SSHDetect(device_type="autodetect", host=ip, username=user,
                      password=pw, timeout=30, conn_timeout=30)
        best = g.autodetect()
        g.connection.disconnect()
        if best == "cisco_xr":
            dtype = "cisco_xr"
        elif best and best not in ("cisco_ios", "cisco_xe"):
            raise RuntimeError(f"desteklenmeyen cihaz tipi: {best}")
    except RuntimeError:
        raise
    except Exception as e:
        log(white(f"  Autodetect xetasi ({e}), cisco_xe ile cehd edilir"))
    time.sleep(1)
    conn = ConnectHandler(device_type=dtype, host=ip, username=user,
                          password=pw, timeout=60, conn_timeout=30)
    return conn, dtype


def connect(ip, user, pw, tries, log):
    for i in range(1, tries + 1):
        try:
            return _connect_once(ip, user, pw, log)
        except RuntimeError:
            raise
        except Exception as e:
            log(f"{R}  Qosulma cehdi {i}/{tries} ugursuz: {e}{X}")
            if i == tries:
                raise
            time.sleep(4)


def collect(ip, user, pw, tries):
    """Bir cihazdan LLDP qonsularini oxuyur. Cixis loglari bufere yigilir (paralel isleyende qarisiq cixmasin)"""
    buf = []
    res = {"ip": ip, "ok": False, "buf": buf, "entries": [], "host": "", "dtype": ""}
    log = buf.append

    def run(conn, c):
        log(f"  {white('Icra olunur:')} {cmdc(c)}")
        return conn.send_command(c, read_timeout=90)

    conn = None
    try:
        conn, dtype = connect(ip, user, pw, tries, log)
        host = conn.find_prompt().strip("#> ").split(":")[-1]
        res["host"], res["dtype"] = host, dtype
        log(f"{white('Qosuldu:')} {ipc(ip)} {white(host + '  [' + dtype + ']')}")
        raw = parse_detail(run(conn, "show lldp neighbors detail"))
        po_map = {}
        if dtype != "cisco_xr":
            po_map = parse_etherchannel(run(conn, "show etherchannel summary"))
        res["entries"] = clean_entries(raw, po_map)
        res["ok"] = True
        log(white(f"  LLDP qonsu sayi: {len(res['entries'])}"))
        for e in res["entries"]:
            lb = f" [{e['lbundle']}]" if e["lbundle"] else ""
            rb = f" [{e['rbundle']}]" if e["rbundle"] else ""
            log(white(f"    {e['lport']}{lb}  <->  {e['rname']} {e['rport']}{rb}"
                      + (f"   ({e['ip']})" if e["ip"] else "")))
    except Exception as e:
        log(f"{R}  XETA {ip}: {e}{X}")
        res["error"] = str(e)
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass
    return res


# ------------------------------ qraf ----------------------------------
def build_graph(results):
    """results -> nodes, links, pairs"""
    # queried cihazlar (yalniz ilk rast gelen host adi)
    nodes = {}      # canon -> {name, type, ip, queried, descs}
    ip2name = {}
    for r in results:
        if not r["ok"]:
            continue
        c = canon(r["host"])
        if c in nodes and nodes[c]["queried"]:
            continue
        nodes[c] = {"name": disp(r["host"]), "type": "xr" if r["dtype"] == "cisco_xr" else "xe",
                    "ip": r["ip"], "queried": True}
        ip2name[r["ip"]] = c

    links = {}
    for r in results:
        if not r["ok"]:
            continue
        a = canon(r["host"])
        if a not in nodes:
            continue
        for e in r["entries"]:
            b = canon(e["rname"])
            if e["ip"] in ip2name:          # qonsunun idareetme IP-si bizim qosulduq cihazdir -> eyni ad
                b = ip2name[e["ip"]]
            if not b or b == a:
                continue
            if b not in nodes:
                nodes[b] = {"name": e["rname"], "type": type_from_desc(e["desc"]),
                            "ip": e["ip"], "queried": False}
            elif not nodes[b]["ip"] and e["ip"]:
                nodes[b]["ip"] = e["ip"]
            ka, kb = (a, e["lport"].lower()), (b, e["rport"].lower())
            key = frozenset((ka, kb))
            if key in links:
                L = links[key]
                if a == L["a"]:
                    L["abundle"] = L["abundle"] or e["lbundle"]
                    L["bbundle"] = L["bbundle"] or e["rbundle"]
                else:
                    L["abundle"] = L["abundle"] or e["rbundle"]
                    L["bbundle"] = L["bbundle"] or e["lbundle"]
                continue
            links[key] = {"a": a, "aport": e["lport"], "abundle": e["lbundle"],
                          "b": b, "bport": e["rport"], "bbundle": e["rbundle"]}

    # cihaz cutleri (A-B): sorted ad, portlar uygun cevrilir
    pairs = {}
    for L in links.values():
        n1, n2 = sorted((L["a"], L["b"]))
        if n1 == L["a"]:
            item = (L["aport"], L["abundle"], L["bport"], L["bbundle"])
        else:
            item = (L["bport"], L["bbundle"], L["aport"], L["abundle"])
        pairs.setdefault((n1, n2), []).append(item)
    for k in pairs:
        pairs[k].sort()
    return nodes, links, pairs


def pair_labels(items):
    """Cutun uc yazilari ve orta yazisi"""
    end1 = "\n".join(i[0] for i in items)
    end2 = "\n".join(i[2] for i in items)
    b1 = {i[1] for i in items if i[1]}
    b2 = {i[3] for i in items if i[3]}
    mid = ""
    if b1 or b2:
        mid = f"{'/'.join(sorted(b1)) or '?'} <-> {'/'.join(sorted(b2)) or '?'}  ({len(items)}x)"
    elif len(items) > 1:
        mid = f"{len(items)}x"
    return end1, end2, mid


# ------------------------------ layout --------------------------------
def layout(nodes, pairs):
    """Laylı (layered) yerlesdirme: en cox baglantisi olan cihaz kokde, qonsular novbeti setirlerde;
    cizgi kesismelerini azaltmaq ucun barycenter siralamasi. Boyuk/en topologiya normaldir."""
    adj = {n: set() for n in nodes}
    for a, b in pairs:
        adj[a].add(b)
        adj[b].add(a)
    seen, comps = set(), []
    for n in sorted(nodes, key=lambda x: (-len(adj[x]), x)):
        if n in seen:
            continue
        comp, queue = [], [n]
        seen.add(n)
        while queue:
            cur = queue.pop(0)
            comp.append(cur)
            for m in sorted(adj[cur]):
                if m not in seen:
                    seen.add(m)
                    queue.append(m)
        comps.append(comp)
    comps.sort(key=lambda c: -len(c))

    pos = {}
    x_off = 0.0
    for comp in comps:
        root = max(comp, key=lambda n: (len(adj[n]), n))
        depth = {root: 0}
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for m in sorted(adj[cur]):
                if m in comp and m not in depth:
                    depth[m] = depth[cur] + 1
                    queue.append(m)
        layers = {}
        for n, d in depth.items():
            layers.setdefault(d, []).append(n)
        for d in layers:
            layers[d].sort()

        def xs(layer):
            return {n: i - (len(layer) - 1) / 2 for i, n in enumerate(layer)}

        for sweep in range(4):
            order = sorted(layers) if sweep % 2 == 0 else sorted(layers, reverse=True)
            for d in order:
                ref = d - 1 if sweep % 2 == 0 else d + 1
                if ref not in layers:
                    continue
                refx = xs(layers[ref])
                cur = xs(layers[d])
                def key(n):
                    ns = [refx[m] for m in adj[n] if m in refx]
                    return (sum(ns) / len(ns)) if ns else cur[n]
                layers[d].sort(key=lambda n: (key(n), n))
        width = max(len(l) for l in layers.values())
        for d, layer in layers.items():
            for i, n in enumerate(layer):
                pos[n] = (x_off + ((width - 1) / 2 + i - (len(layer) - 1) / 2) * DX, d * DY)
        x_off += width * DX + DX * 0.6
    return pos


# ---------------------------- draw.io ---------------------------------
def write_drawio(nodes, pairs, pos, path):
    x0 = min(p[0] for p in pos.values())
    y0 = min(p[1] for p in pos.values())
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    ids = {}
    for i, (n, d) in enumerate(sorted(nodes.items()), 1):
        ids[n] = f"n{i}"
        fill, stroke = TYPE_COLORS[d["type"]]
        label = d["name"] + (f"&lt;br&gt;&lt;font style=&quot;font-size:9px&quot;&gt;{d['ip']}&lt;/font&gt;" if d["ip"] else "")
        dash = "" if d["queried"] else "dashed=1;"
        x = pos[n][0] - x0 + 60
        y = pos[n][1] - y0 + 60
        cells.append(
            f'<mxCell id="{ids[n]}" value="{label}" style="rounded=1;whiteSpace=wrap;html=1;'
            f'fillColor={fill};strokeColor={stroke};strokeWidth=2;fontStyle=1;{dash}" vertex="1" parent="1">'
            f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{NODE_W}" height="{NODE_H}" as="geometry"/></mxCell>')
    for j, ((a, b), items) in enumerate(sorted(pairs.items()), 1):
        end1, end2, mid = pair_labels(items)
        eid = f"e{j}"
        cells.append(
            f'<mxCell id="{eid}" value={quoteattr(mid)} style="endArrow=none;html=1;rounded=0;strokeWidth=2;'
            f'fontSize=10;labelBackgroundColor=#ffffff;" edge="1" parent="1" source="{ids[a]}" target="{ids[b]}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')
        for suffix, text, xpos in (("s", end1, -0.72), ("t", end2, 0.72)):
            val = text.replace("\n", "<br>")
            cells.append(
                f'<mxCell id="{eid}{suffix}" value={quoteattr(val)} style="edgeLabel;html=1;align=center;'
                f'verticalAlign=middle;resizable=0;points=[];fontSize=9;labelBackgroundColor=#ffffff;" '
                f'vertex="1" connectable="0" parent="{eid}"><mxGeometry x="{xpos}" relative="1" as="geometry">'
                f'<mxPoint as="offset"/></mxGeometry></mxCell>')
    xml = ('<mxfile host="app.diagrams.net"><diagram name="LLDP Topology" id="lldp-topology">'
           '<mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
           'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1654" pageHeight="1169" math="0" shadow="0">'
           '<root>' + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


# ------------------------------- PNG ----------------------------------
def write_png(nodes, pairs, pos, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Patch

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    pad = NODE_W
    wpx = max(xs) - min(xs) + 2 * pad
    hpx = max(ys) - min(ys) + 2 * pad
    fig = plt.figure(figsize=(max(9, wpx / 80), max(6, hpx / 80)), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(max(ys) + pad, min(ys) - pad)
    ax.axis("off")

    def edge_point(c1, c2, dist):
        """c1 merkezinden c2 istiqametinde, qutunun cixis noqtesindən 'dist' px kenara"""
        dx, dy = c2[0] - c1[0], c2[1] - c1[1]
        length = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / length, dy / length
        tx = (NODE_W / 2) / abs(ux) if ux else 1e9
        ty = (NODE_H / 2) / abs(uy) if uy else 1e9
        exit_d = min(tx, ty)
        return c1[0] + ux * (exit_d + dist), c1[1] + uy * (exit_d + dist)

    for (a, b), items in pairs.items():
        pa, pb = pos[a], pos[b]
        ax.plot([pa[0], pb[0]], [pa[1], pb[1]], color="#444444", lw=1.8, zorder=1)
        end1, end2, mid = pair_labels(items)
        for txt, c1, c2 in ((end1, pa, pb), (end2, pb, pa)):
            lx, ly = edge_point(c1, c2, 16 + 6 * (txt.count("\n")))
            ax.text(lx, ly, txt, ha="center", va="center", fontsize=6.5, zorder=3,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))
        if mid:
            ax.text((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, mid, ha="center", va="center",
                    fontsize=6.5, color="#7a4b00", zorder=3,
                    bbox=dict(boxstyle="round,pad=0.2", fc="#fff2cc", ec="#d6b656", alpha=0.95))
    for n, d in nodes.items():
        x, y = pos[n]
        fill, stroke = TYPE_COLORS[d["type"]]
        ax.add_patch(FancyBboxPatch((x - NODE_W / 2, y - NODE_H / 2), NODE_W, NODE_H,
                                    boxstyle="round,pad=0,rounding_size=10", fc=fill, ec=stroke,
                                    lw=2.2, ls="-" if d["queried"] else "--", zorder=4))
        ax.text(x, y - 6, d["name"], ha="center", va="center", fontsize=8.5, fontweight="bold", zorder=5)
        if d["ip"]:
            ax.text(x, y + 13, d["ip"], ha="center", va="center", fontsize=7, color="#555555", zorder=5)
    legend = [Patch(fc=TYPE_COLORS[t][0], ec=TYPE_COLORS[t][1], label=TYPE_LABEL[t]) for t in ("xr", "xe", "other")]
    ax.legend(handles=legend, loc="upper left", fontsize=8, frameon=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)


# ------------------------------ Excel ---------------------------------
def write_xlsx(nodes, links, results, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Links"
    ws.append(["Device A", "Port A", "Bundle A", "Device B", "Port B", "Bundle B"])
    for L in sorted(links.values(), key=lambda x: (x["a"], x["b"], x["aport"])):
        ws.append([nodes[L["a"]]["name"], L["aport"], L["abundle"], nodes[L["b"]]["name"], L["bport"], L["bbundle"]])
    ws2 = wb.create_sheet("Devices")
    ws2.append(["Name", "IP", "Type", "Queried", "Links"])
    cnt = {}
    for L in links.values():
        cnt[L["a"]] = cnt.get(L["a"], 0) + 1
        cnt[L["b"]] = cnt.get(L["b"], 0) + 1
    for n, d in sorted(nodes.items()):
        ws2.append([d["name"], d["ip"], TYPE_LABEL[d["type"]], "yes" if d["queried"] else "no", cnt.get(n, 0)])
    ws3 = wb.create_sheet("Errors")
    ws3.append(["IP", "Error"])
    for r in results:
        if not r["ok"]:
            ws3.append([r["ip"], r.get("error", "")])
    for sh in (ws, ws2, ws3):
        for c in sh[1]:
            c.font = Font(bold=True)
        for col in sh.columns:
            sh.column_dimensions[col[0].column_letter].width = max(12, min(45, max(len(str(c.value or "")) for c in col) + 2))
    wb.save(path)


# ------------------------------- main ---------------------------------
def load_ips():
    path = sys.argv[1] if len(sys.argv) > 1 else "ips.txt"
    if os.path.exists(path):
        text = open(path, encoding="utf-8", errors="ignore").read()
        print(f"IP siyahisi fayldan oxundu: {os.path.abspath(path)}")
    else:
        print(f"{path} tapilmadi (axtarilan yer: {os.path.abspath(path)}). IP-leri yapisdir, bitirmek ucun bos setir:")
        lines = []
        while True:
            l = input()
            if not l.strip():
                break
            lines.append(l)
        text = "\n".join(lines)
    return list(dict.fromkeys(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)))


def flush_stdin():
    try:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def wait_enter():
    flush_stdin()
    try:
        input("Press ENTER to exit.")
    except (EOFError, KeyboardInterrupt):
        pass


def parse_prefixes(text):
    nets = []
    for p in re.split(r"[,\s]+", text.strip()):
        if p:
            try:
                nets.append(ipaddress.ip_network(p, strict=False))
            except ValueError:
                out(f"{R}Prefiks tanınmadı, kecildi: {p}{X}")
    return nets


def ask_name(prompt):
    try:
        return input(prompt).strip()
    except UnicodeDecodeError:
        print("Ad oxuna bilmedi (simvol kodlamasi), ad olmadan davam edilir.")
        return ""


def safe_prefix(name):
    """Fayl adi ucun tehlukesiz prefiks (Windows-da etibarsiz simvollar, rezerv adlar, uzun ad)"""
    name = re.sub(r"[^\w\-.() ]+", "_", name.strip(), flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("._ ")[:60]
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if name.upper() in reserved:
        name = "_" + name
    return f"{name}_" if name else ""


def pick_out_dir():
    """Fayllar yazila bilen ilk qovluga yazilir: cari qovluq -> script qovlugu -> ev qovlugu"""
    for d in (os.getcwd(), os.path.dirname(os.path.abspath(__file__)), os.path.expanduser("~")):
        try:
            p = os.path.join(d, ".write_test")
            open(p, "w").close()
            os.remove(p)
            return d
        except Exception:
            continue
    return os.getcwd()


def main():
    global LOG
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = safe_prefix(ask_name("Fayllar ucun ad (bos buraxa bilersen): "))
    out_dir = pick_out_dir()
    base_path = os.path.join(out_dir, f"{prefix}lldp_topology_{ts}")
    LOG = open(base_path + "_log.txt", "w", encoding="utf-8")

    ips = load_ips()
    if not ips:
        print("IP tapilmadi.")
        wait_enter()
        return
    user = input("Username: ").strip()
    pw = getpass.getpass("Password: ")
    want_xlsx = input("Excel hesabat da yaradilsin? [Y/n]: ").strip().lower() != "n"
    crawl = input("LLDP-de tapilan yeni qonsulara da avtomatik qosulub kesf edilsin? [y/N]: ").strip().lower() == "y"
    nets = []
    if crawl:
        nets = parse_prefixes(input("Yalniz bu prefikslerde kesf et (meselen 10.53.0.0/16,10.71.0.0/16; bos=hamisi): "))

    out(white(f"{len(ips)} cihaz: ") + "  ".join(ipc(i) for i in ips))
    results = []
    visited = set(ips)
    frontier = [(ip, 0) for ip in ips]
    try:
        while frontier:
            batch, frontier = frontier, []
            ex = ThreadPoolExecutor(max_workers=MAX_WORKERS)
            futs = {ex.submit(collect, ip, user, pw, 3 if depth == 0 else 1): (ip, depth) for ip, depth in batch}
            try:
                for fut in as_completed(futs):
                    ip, depth = futs[fut]
                    r = fut.result()
                    results.append(r)
                    out()
                    out(f"{B}{'=' * 78}{X}")
                    out(f"{B}  CIHAZ: {ip}{X}")
                    out(f"{B}{'=' * 78}{X}")
                    for line in r["buf"]:
                        out(line)
                    if crawl and r["ok"] and depth < CRAWL_DEPTH:
                        for e in r["entries"]:
                            nip = e["ip"]
                            if not nip or nip in visited:
                                continue
                            if nets:
                                try:
                                    if not any(ipaddress.ip_address(nip) in n for n in nets):
                                        continue
                                except ValueError:
                                    continue
                            visited.add(nip)
                            frontier.append((nip, depth + 1))
            except KeyboardInterrupt:
                ex.shutdown(wait=False, cancel_futures=True)
                raise
            ex.shutdown(wait=True)
            if frontier:
                out(white(f"\nKesf: {len(frontier)} yeni qonsu IP tapildi, novbeti sevie kecilir..."))
    except KeyboardInterrupt:
        out(f"\n{R}Istifadeci terefinden dayandirildi, toplanan melumatla topologiya cizilir.{X}")

    try:
        nodes, links, pairs = build_graph(results)
        out()
        out(f"{B}{'=' * 78}{X}")
        ok = sum(1 for r in results if r["ok"])
        out(white(f"Qosulan cihaz: {ok}/{len(results)}   |   cemi cihaz (qonsular daxil): {len(nodes)}   |   baglanti: {len(links)}"))
        for r in results:
            if not r["ok"]:
                out(f"{R}  Qosulmadi: {r['ip']}  ({r.get('error', '')}){X}")
        if nodes:
            pos = layout(nodes, pairs) if nodes else {}
            write_drawio(nodes, pairs, pos, base_path + ".drawio")
            out(white(f"draw.io: {base_path}.drawio"))
            try:
                write_png(nodes, pairs, pos, base_path + ".png")
                out(white(f"PNG: {base_path}.png"))
            except ImportError:
                out(white("matplotlib yoxdur, PNG yaradilmadi (pip install matplotlib)"))
            if want_xlsx:
                try:
                    write_xlsx(nodes, links, results, base_path + ".xlsx")
                    out(white(f"Excel: {base_path}.xlsx"))
                except ImportError:
                    out(white("openpyxl yoxdur, Excel yaradilmadi (pip install openpyxl)"))
        else:
            out(white("Topologiya ucun melumat yoxdur."))
        out(white(f"Execution log: {base_path}_log.txt"))
        LOG.flush()
    except Exception:
        traceback.print_exc()
    wait_enter()
    try:
        LOG.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        wait_enter()
