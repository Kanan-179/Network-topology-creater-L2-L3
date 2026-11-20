#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLDP manager - Cisco IOS-XR (NCS) + IOS-XE (ASR 920 / 901)

Qurulum : pip install netmiko colorama openpyxl
Isletmek: python lldp_manager.py [ips.txt]
          (ips.txt yoxdursa IP-leri yapisdirirsan; txt/csv ferq etmir)
Dayandir: Ctrl+C  (baglantini baglayib tehlukesiz cixir)
"""
import os
import re
import sys
import getpass
import traceback
import time
from datetime import datetime

try:
    from netmiko import ConnectHandler, SSHDetect
except ImportError as _e:      # pencere ani baglanmasin: xetani goster ve ENTER gozle
    print("XETA: netmiko modulu tapilmadi ->", _e)
    print("Qurulum: python -m pip install -r requirements.txt   (eyni Python ile isletdiyine emin ol)")
    input("Press ENTER to exit.")
    sys.exit(1)

try:
    from live_monitor import Monitor
except ImportError:
    Monitor = None

try:
    import colorama
    colorama.init()
except ImportError:
    os.system("")  # Windows ANSI

# ----------------------------- AYARLAR --------------------------------
SHOW_RAW = False      # True -> cihazdan gelen xam cavabi da goster
SERVICE_INST = 1      # ASR: service instance nomresi
SERVICE_BD = 1        # ASR: bridge-domain nomresi
# -----------------------------------------------------------------------

W = "\033[97m"        # ag    - adi yazilar
R = "\033[91m"        # qirmizi - dirnaqlar
G = "\033[92m"        # yasil - commandlar
B = "\033[1;94m"      # mavi  - IP-ler
Y = "\033[93m"        # sari  - suallar
X = "\033[0m"

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG = None
ALL_YES = False
REC = []              # excel ucun


class StopRequested(Exception):
    pass


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


def rec(ip, host, action, detail, status):
    REC.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, host, action, detail, status])


# --------------------------- ad normallasdirma --------------------------
PFX = [("twentyfivegige", "twe"), ("hundredgige", "hu"), ("fortygige", "fo"),
       ("tengigabitethernet", "te"), ("tengige", "te"), ("gigabitethernet", "gi"),
       ("fastethernet", "fa"), ("bundle-ether", "be"), ("port-channel", "po")]


def norm(n):
    n = n.strip().lower()
    for a, b in PFX:
        if n.startswith(a):
            return b + n[len(a):]
    return n


def mac_port_norm(p):
    """MAC cedvelindeki Po0/0/4.Efp3995 -> po4 ; Te0/0/25.Efp3998 -> te0/0/25"""
    p = p.split(".")[0]
    m = re.match(r"(?i)^po\d+/\d+/(\d+)$", p)
    if m:
        return "po" + m.group(1)
    return norm(p)


def base(n):
    return norm(n).split(".")[0]


def is_phys(n):
    n = norm(n)
    return bool(re.match(r"^(te|gi|hu|fo|twe|fa)\d+/", n)) and "." not in n


# ------------------------------ yardimcilar -----------------------------
def header(ip):
    out()
    out(f"{B}{'=' * 78}{X}")
    out(f"{B}  CIHAZ: {ip}{X}")
    out(f"{B}{'=' * 78}{X}")


def run(conn, c):
    out(f"  {white('Icra olunur:')} {cmdc(c)}")
    o = conn.send_command(c, read_timeout=60)
    if SHOW_RAW:
        out(white(o))
    return o


def confirm(label, lines):
    global ALL_YES
    out()
    out(white(f"  Plan: {label}"))
    for l in lines:
        out("      " + cmdc(l))
    if ALL_YES:
        out(white("  -> avtomatik BELI (all yes)"))
        return True
    while True:
        a = input(f"{Y}  Icra edilsin? [y]es / [n]o / [a]ll yes / [s]top : {X}").strip().lower()
        if LOG:
            LOG.write(f"  > cavab: {a}\n")
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        if a in ("a", "all"):
            ALL_YES = True
            return True
        if a in ("s", "stop"):
            raise StopRequested()


def parse_desc_up(o):
    ports = []
    for l in o.splitlines():
        t = l.split()
        if len(t) >= 3 and t[1].lower() == "up" and t[2].lower() == "up":
            ports.append(t[0])
    return ports


def parse_ospf(o):
    res = set()
    for l in o.splitlines():
        if re.match(r"^\d+\.\d+\.\d+\.\d+\s", l.strip()):
            res.add(base(l.split()[-1]))
    return res


def _connect_once(ip, user, pw):
    dtype = "cisco_xe"
    try:
        g = SSHDetect(device_type="autodetect", host=ip, username=user,
                      password=pw, timeout=30, conn_timeout=30)
        best = g.autodetect()
        g.connection.disconnect()
        if best == "cisco_xr":
            dtype = "cisco_xr"
    except Exception as e:
        out(white(f"  Autodetect xetasi ({e}), cisco_xe ile cehd edilir"))
    time.sleep(1)  # XR vty sessiyasi bosalsin
    conn = ConnectHandler(device_type=dtype, host=ip, username=user,
                          password=pw, timeout=60, conn_timeout=30)
    return conn, dtype


def connect(ip, user, pw, tries=3):
    for i in range(1, tries + 1):
        try:
            return _connect_once(ip, user, pw)
        except Exception as e:
            out(f"{R}  Qosulma cehdi {i}/{tries} ugursuz: {e}{X}")
            if i == tries:
                raise
            time.sleep(4)


def select_blocks(ip, host, blocks):
    approved = []
    for label, lines in blocks:
        if confirm(label, lines):
            approved.append((label, lines))
        else:
            out(white("  -> kecildi"))
            rec(ip, host, label, "; ".join(lines), "SKIPPED")
    return approved


# -------------------------------- IOS-XR --------------------------------
def xr_isis(o):
    res = set()
    for l in o.splitlines():
        m = re.match(r"^\S+\s+(\S+)\s+\S+\s+Up\b", l.strip())
        if m:
            res.add(base(m.group(1)))
    return res


# ----------------------- commit confirmed (ping monitor) -----------------------
CONFIRM_MIN = 1        # "commit confirmed minutes N"
CONFIRM_MARGIN = 3     # son bu qeder saniyede tesdiq qebul olunmur (cihaz ozu geri alacaq)
CFG_PROMPT = r"\(config[^)]*\)#"
MON = None


def console_confirm(deadline):
    left = int(deadline - time.time())
    a = input(f"{Y}  Pencere baglanib. {left} san icinde commit tesdiq edilsin? [y/n]: {X}").strip().lower()
    if time.time() >= deadline:
        return "timeout"
    return "confirm" if a in ("y", "yes") else "rollback"


def xr_commit(conn, ip, host):
    """Ping monitor aciqdirsa: commit confirmed minutes N -> pencerede tesdiq -> commit.
    Qaytarir: CONFIRMED | ROLLED_BACK | APPLIED | ERROR"""
    if MON is None or not MON.alive():
        out(f"  {white('Icra olunur:')} {cmdc('commit')}")
        o = conn.send_command("commit", expect_string=CFG_PROMPT, read_timeout=120, strip_prompt=False)
        out(white(o))
        failed = bool(re.search(r"(?i)fail|error", o))
        if failed:
            out(f"{R}  COMMIT XETASI - yuxaridaki cavaba bax, candidate config abort edilir{X}")
        conn.write_channel(("abort" if failed else "end") + "\n")
        time.sleep(0.5)
        return "ERROR" if failed else "APPLIED"

    cmd = f"commit confirmed minutes {CONFIRM_MIN}"
    out(f"  {white('Icra olunur:')} {cmdc(cmd)}")
    t0 = time.time()
    o = conn.send_command(cmd, expect_string=CFG_PROMPT, read_timeout=120, strip_prompt=False)
    out(white(o))
    if re.search(r"(?i)fail|error", o):
        out(f"{R}  COMMIT XETASI - candidate config abort edilir{X}")
        conn.write_channel("abort\n")
        time.sleep(0.5)
        return "ERROR"
    expire = t0 + CONFIRM_MIN * 60
    deadline = expire - CONFIRM_MARGIN
    out(white(f"  Ping monitor penceresinde tesdiq gozlenilir ({int(deadline - time.time())} san)..."))
    ans = MON.ask_confirm(ip, host, expire, CONFIRM_MARGIN)
    if ans is None:
        ans = console_confirm(deadline)
    if ans == "confirm":
        out(f"  {white('Icra olunur:')} {cmdc('commit')}")
        o = conn.send_command("commit", expect_string=CFG_PROMPT, read_timeout=60, strip_prompt=False)
        out(white(o))
        conn.write_channel("end\n")
        time.sleep(0.5)
        out(white("  commit TESDIQLENDI"))
        return "CONFIRMED"
    out(f"{R}  Tesdiq verilmedi ({ans}) -> cihaz konfiqurasiyani avtomatik geri alacaq{X}")
    wait = expire + 3 - time.time()
    if wait > 0:
        out(white(f"  Geri alinma gozlenilir ({int(wait)} san)..."))
        time.sleep(wait)
    conn.write_channel("end\n")
    time.sleep(0.5)
    return "ROLLED_BACK"


# ------------- movcud LLDP veziyyetini oxu (artiq duzgundurse komanda yazma) -------------
LONG_XE = {"te": "TenGigabitEthernet", "gi": "GigabitEthernet", "fo": "FortyGigabitEthernet",
           "hu": "HundredGigE", "fa": "FastEthernet", "twe": "TwentyFiveGigE"}


def onoff(b):
    return "ON" if b else "OFF"


def parse_lldp_if(o):
    """'show lldp interface' -> {norm(port): {'tx': bool, 'rx': bool}}"""
    res, cur = {}, None
    for l in o.splitlines():
        m = re.match(r"^\s*([A-Za-z][\w-]*[\d/.]+):\s*$", l)
        if m:
            cur = norm(m.group(1))
            res[cur] = {}
            continue
        t = re.match(r"^\s*(Tx|Rx):\s*(enabled|disabled)", l, re.I)
        if t and cur:
            res[cur][t.group(1).lower()] = (t.group(2).lower() == "enabled")
    return {k: v for k, v in res.items() if "tx" in v and "rx" in v}


def lldp_show_cmd(p, xe):
    if not xe:
        return f"show lldp interface {p}"
    m = re.match(r"^([A-Za-z]+)([\d/]+)$", p)
    if not m:
        return f"show lldp interface {p}"
    return f"show lldp interface {LONG_XE.get(m.group(1).lower(), m.group(1))} {m.group(2)}"


def get_lldp_states(conn, ports, xe):
    """Butun interfeyslerin LLDP tx/rx veziyyeti; siyahida olmayan portlar ayrica sorgulanir"""
    st = parse_lldp_if(run(conn, "show lldp interface"))
    for p in ports:
        if norm(p) in st:
            continue
        o = run(conn, lldp_show_cmd(p, xe))
        one = parse_lldp_if(o)
        if norm(p) in one:
            st[norm(p)] = one[norm(p)]
            continue
        t = re.search(r"(?im)^\s*Tx:\s*(enabled|disabled)", o)
        r = re.search(r"(?im)^\s*Rx:\s*(enabled|disabled)", o)
        if t and r:
            st[norm(p)] = {"tx": t.group(1).lower() == "enabled", "rx": r.group(1).lower() == "enabled"}
    return st


def show_state(p, st, want):
    name = f"{p:<14}"
    if st is None:
        out(white(f"    {name} cari: oxunmadi            hedef: {onoff(want)}  -> tetbiq olunacaq"))
        return
    ok = st["tx"] == want and st["rx"] == want
    col = G if ok else Y
    verdict = "duzgundur" if ok else "deyisecek"
    out(white(f"    {name} tx={onoff(st['tx']):<3} rx={onoff(st['rx']):<3}  hedef={onoff(want)}  ")
        + f"{col}-> {verdict}{X}")


def xr_lldp_global(conn):
    o = run(conn, "show running-config lldp")
    return bool(re.search(r"(?m)^\s*lldp\b", o)) and "No such configuration" not in o


def xe_lldp_global(conn):
    o = run(conn, "show running-config | include ^lldp run")
    return bool(re.search(r"(?m)^lldp run\b", o))


BUNDLE_MEM_RE = re.compile(
    r"^\s*((?:TenGigE|HundredGigE|FortyGigE|TwentyFiveGigE|GigabitEthernet|Te|Hu|Fo|Twe|Gi)\d+(?:/\d+)+)\b",
    re.M | re.I)


def xr_bundle_members(conn, num):
    """Bundle-Ether<num> uzv portlari (norm adlarla set). Tapilmasa: raw cixisi gosterir, istifadeciden sorusur.
    None qaytarsa -> cihaz kecilsin."""
    o = run(conn, f"show bundle bundle-ether{num}")
    mem = {norm(x) for x in BUNDLE_MEM_RE.findall(o)}
    if not mem:   # ikinci yol: umumi 'show bundle' icinden bu bundle-in bolmesi
        o2 = run(conn, "show bundle")
        sec = re.search(rf"(?ims)^\s*Bundle-Ether{num}\b.*?(?=^\s*Bundle-Ether\d+\b|\Z)", o2)
        if sec:
            mem = {norm(x) for x in BUNDLE_MEM_RE.findall(sec.group(0))}
            o = sec.group(0)
    if mem:
        return mem
    out(f"{R}  XEBERDARLIQ: Bundle-Ether{num} uzv portlari avtomatik tapilmadi. Cihazin cavabi:{X}")
    for l in o.splitlines()[:40]:
        out(white("      | " + l))
    while True:
        a = input(f"{Y}  Uzv portlari yaz (meselen Te0/0/0/16,Te0/0/0/17) / [s]=cihazi kec : {X}").strip()
        if LOG:
            LOG.write(f"  > cavab: {a}\n")
        if a.lower() in ("s", "skip"):
            return None
        mem = {norm(x) for x in re.split(r"[,\s]+", a) if re.match(r"(?i)^[a-z]+\d+(/\d+)+$", x)}
        if mem:
            return mem


def xr_flow(conn, ip, host):
    ospf = parse_ospf(run(conn, "show ip ospf neighbor"))
    isis = xr_isis(run(conn, "show isis neighbors"))
    out(white("  OSPF interfeysleri : " + (", ".join(sorted(ospf)) or "yoxdur")))
    out(white("  ISIS interfeysleri : " + (", ".join(sorted(isis)) or "yoxdur")))

    keep = set()
    for n in ospf | isis:
        keep.add(n)
        m = re.match(r"be(\d+)$", n)
        if m:
            mem = xr_bundle_members(conn, m.group(1))
            if mem is None:
                out(white("  -> cihaz kecildi (bundle uzvleri mueyyen edilmedi)"))
                rec(ip, host, "skip", f"BE{m.group(1)} uzvleri tapilmadi", "SKIPPED")
                return
            out(white(f"  {n} uzvleri: " + (", ".join(sorted(mem)) or "tapilmadi")))
            keep |= mem

    if not (ospf or isis):
        out(white("  OSPF/ISIS qonsusu yoxdur -> tehlukesizlik ucun cihaz kecildi"))
        rec(ip, host, "skip", "no neighbors", "SKIPPED")
        return

    if not any(is_phys(k) for k in keep):
        out(f"{R}  Qonsuluq portu (fiziki) tapilmadi -> tehlukesizlik ucun cihaz kecildi: "
            f"{', '.join(sorted(keep)) or '-'}{X}")
        rec(ip, host, "skip", "no physical keep port", "SKIPPED")
        return

    up = parse_desc_up(run(conn, "show interfaces description | i up"))
    phys = [p for p in up if is_phys(p)]
    disable = [p for p in phys if norm(p) not in keep]
    out(white("  LLDP qalan  : " + (", ".join(sorted(keep)) or "-")))
    out(white("  LLDP sonecek: " + (", ".join(disable) or "yoxdur")))

    names = {norm(p): p for p in phys}
    enable = [names.get(p, p) for p in sorted(k for k in keep if is_phys(k))]

    glob = xr_lldp_global(conn)
    out(white("  Global LLDP: " + ("aktivdir (komanda yazilmayacaq)" if glob else "AKTIV DEYIL -> aktiv edilecek")))
    states = get_lldp_states(conn, enable + disable, xe=False) if glob else {}
    out(white("  Portlarin movcud LLDP veziyyeti:"))

    blocks = []
    if not glob:
        blocks.append(("Global LLDP aktiv", ["lldp"]))
    for p in enable:
        st = states.get(norm(p))
        show_state(p, st, True)
        cmds = []
        if st is None or not st["tx"]:
            cmds.append("no lldp transmit disable")
        if st is None or not st["rx"]:
            cmds.append("no lldp receive disable")
        if cmds:
            blocks.append((f"{p} - LLDP aktiv et", [f"interface {p}"] + cmds + ["root"]))
    for p in disable:
        st = states.get(norm(p))
        show_state(p, st, False)
        sub = []
        if st is None or st["rx"]:
            sub.append("receive disable")
        if st is None or st["tx"]:
            sub.append("transmit disable")
        if sub:
            blocks.append((f"{p} - LLDP disable", [f"interface {p}", "lldp"] + sub + ["root"]))
    if not blocks:
        out(f"{G}  Butun LLDP veziyyeti artiq duzgundur -> hecne edilmir{X}")
        rec(ip, host, "lldp", "already correct", "UNCHANGED")
        return
    approved = select_blocks(ip, host, blocks)
    if not approved:
        return
    lines = [l for _, ls in approved for l in ls]
    out(white("  Config gonderilir..."))
    o = conn.send_config_set(lines, cmd_verify=False, exit_config_mode=False, read_timeout=60)
    out(white(o))
    status = xr_commit(conn, ip, host)
    for label, ls in approved:
        rec(ip, host, label, "; ".join(ls), status)


# -------------------------------- IOS-XE --------------------------------
def nbr_ips(ospf_out, isis_out):
    """interfeys (norm) -> [qonsu IP-leri]; ping ile MAC yenilemek ucun"""
    res = {}
    for l in ospf_out.splitlines():
        t = l.split()
        if len(t) >= 6 and re.match(r"^\d+\.\d+\.\d+\.\d+$", t[0]):
            res.setdefault(base(t[-1]), []).append(t[-2])
    for l in isis_out.splitlines():
        m = re.match(r"^\S+\s+L\d\s+(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+UP\b", l.strip(), re.I)
        if m:
            res.setdefault(base(m.group(1)), []).append(m.group(2))
    return res


MAC_ANY = re.compile(r"[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}")
MAC_TYPES = {"dynamic", "static", "secure", "self", "dynamic-ex", "pseudo"}


def parse_mac_ports(text):
    """MAC cedvelinden (bridge-domain ve ya mac address-table cixisi) portlari cixarir"""
    res = set()
    for l in text.splitlines():
        m = MAC_ANY.search(l)
        if not m:
            continue
        rest = [t for t in l[m.end():].split() if t.lower() not in MAC_TYPES]
        if rest:
            res.add(mac_port_norm(rest[0]))
    return res


def bd_ports(conn, num, ips):
    """bridge-domain portlari; birden cox portdursa MAC cedvelinden secir:
    1) show bridge-domain  2) show mac address-table bdomain  3) show mac-address-table dynamic vlan  4) ping ile yenile  5) istifadeciden sorus"""
    o = run(conn, f"show bridge-domain {num}")
    ports = re.findall(r"^\s+(\S+)\s+service instance\s+\d+", o, re.M)
    out(white(f"  BD{num} portlari: " + (", ".join(ports) or "tapilmadi")))
    if len(ports) < 2:
        return ports

    def pick(text, src):
        macp = parse_mac_ports(text)
        out(white(f"  MAC portlari ({src}): " + (", ".join(sorted(macp)) or "yoxdur")))
        return [p for p in ports if norm(p) in macp]

    def lookup(bd_text):
        sel = pick(bd_text, "show bridge-domain")
        if sel:
            return sel
        t = run(conn, f"show mac address-table bdomain {num}")
        if re.search(r"(?i)invalid|incomplete|ambiguous", t):
            t = run(conn, f"show mac-address-table bdomain {num}")
        sel = pick(t, "mac address-table")
        if sel:
            return sel
        t = run(conn, f"show mac-address-table dynamic vlan {num}")   # alternativ yol
        return pick(t, "mac-address-table dynamic vlan")

    sel = lookup(o)
    if not sel and ips:
        out(white(f"  MAC tapilmadi -> {ips[0]} ping edilir (MAC ogrenmek ucun)"))
        run(conn, f"ping {ips[0]} repeat 5")
        sel = lookup(run(conn, f"show bridge-domain {num}"))
    if sel:
        out(white(f"  MAC cedvelinden secildi: {', '.join(sel)}"))
        return sel
    out(f"{R}  XEBERDARLIQ: BD{num} ucun MAC ile port mueyyen edilmedi, ozun sec:{X}")
    for i, p in enumerate(ports, 1):
        out(white(f"      {i}) {p}"))
    while True:
        a = input(f"{Y}  LLDP qalacaq port nomresi (meselen 2 ve ya 1,2) / [a]=hamisi / [s]=cihazi kec : {X}").strip().lower()
        if LOG:
            LOG.write(f"  > cavab: {a}\n")
        if a in ("s", "skip"):
            return None
        if a in ("a", "all"):
            return ports
        try:
            idx = [int(x) for x in a.replace(" ", "").split(",") if x]
            chosen = [ports[i - 1] for i in idx if 1 <= i <= len(ports)]
        except ValueError:
            chosen = []
        if chosen:
            return chosen


def xe_isis(o):
    res = set()
    for l in o.splitlines():
        m = re.match(r"^\S+\s+L\d\s+(\S+)\s+\S+\s+UP\b", l.strip(), re.I)
        if m:
            res.add(base(m.group(1)))
    return res


def xe_etherchannel(o):
    pc = {}
    for l in o.splitlines():
        m = re.match(r"^\s*\d+\s+(Po\d+)\(", l)
        if m:
            mem = re.findall(r"([A-Za-z]+\d+(?:/\d+)+)\(\w+\)", l)
            pc[norm(m.group(1))] = {norm(x) for x in mem}
    return pc


def xe_flow(conn, ip, host):
    ospf_out = run(conn, "show ip ospf neighbor")
    isis_out = run(conn, "show isis neighbors")
    ospf = parse_ospf(ospf_out)
    isis = xe_isis(isis_out)
    ips = nbr_ips(ospf_out, isis_out)
    out(white("  OSPF interfeysleri : " + (", ".join(sorted(ospf)) or "yoxdur")))
    out(white("  ISIS interfeysleri : " + (", ".join(sorted(isis)) or "yoxdur")))

    if not (ospf or isis):
        out(white("  OSPF/ISIS qonsusu yoxdur -> tehlukesizlik ucun cihaz kecildi"))
        rec(ip, host, "skip", "no neighbors", "SKIPPED")
        return

    keep = set()
    si_ports = {}  # norm -> orijinal ad
    for n in ospf | isis:
        m = re.match(r"(bdi|bd|vlan|vl)(\d+)$", n)
        if m:
            kind, num = m.group(1), m.group(2)
            ports = bd_ports(conn, num, ips.get(n, []))
            if ports is None:
                out(white("  -> cihaz kecildi (istifadeci secimi)"))
                rec(ip, host, "skip", f"BD{num} port secilmedi", "SKIPPED")
                return
            for p in ports:
                keep.add(norm(p))
                if kind in ("bdi", "bd"):  # ASR920: service instance + l2protocol peer lldp
                    si_ports[norm(p)] = p
        else:
            keep.add(n)

    pc = xe_etherchannel(run(conn, "show etherchannel summary"))
    for k in list(keep):
        if k in pc:
            out(white(f"  {k} uzvleri: " + ", ".join(sorted(pc[k]))))
            keep |= pc[k]

    if not any(is_phys(k) for k in keep):
        out(f"{R}  Qonsuluq portu (fiziki) tapilmadi -> tehlukesizlik ucun cihaz kecildi: "
            f"{', '.join(sorted(keep)) or '-'}{X}")
        rec(ip, host, "skip", "no physical keep port", "SKIPPED")
        return

    up = parse_desc_up(run(conn, "show interfaces description | i up"))
    phys = [p for p in up if is_phys(p)]
    disable = [p for p in phys if norm(p) not in keep]
    out(white("  LLDP qalan  : " + (", ".join(sorted(keep)) or "-")))
    out(white("  LLDP sonecek: " + (", ".join(disable) or "yoxdur")))

    names = {norm(p): p for p in phys}
    enable = [names.get(p, p) for p in sorted(k for k in keep if is_phys(k))]

    glob = xe_lldp_global(conn)
    out(white("  Global LLDP: " + ("aktivdir (lldp run yazilmayacaq)" if glob else "AKTIV DEYIL -> lldp run yazilacaq")))
    states = get_lldp_states(conn, enable + disable, xe=True) if glob else {}
    out(white("  Portlarin movcud LLDP veziyyeti:"))

    blocks = []
    if not glob:
        blocks.append(("Global LLDP aktiv", ["lldp run"]))
    for n, p in si_ports.items():
        cfg = run(conn, f"show running-config interface {p}")
        if re.search(r"l2protocol peer lldp", cfg):
            out(f"{G}    {p}: l2protocol peer lldp artiq var -> duzgundur{X}")
            continue
        blocks.append((f"{p} - L2 LLDP peer",
                       [f"interface {p}",
                        f"service instance {SERVICE_INST} ethernet",
                        "encapsulation untagged",
                        "l2protocol peer lldp",
                        f"bridge-domain {SERVICE_BD}",
                        "exit", "exit"]))
    for p in enable:
        st = states.get(norm(p))
        show_state(p, st, True)
        cmds = []
        if st is None or not st["tx"]:
            cmds.append("lldp transmit")
        if st is None or not st["rx"]:
            cmds.append("lldp receive")
        if cmds:
            blocks.append((f"{p} - LLDP aktiv et", [f"interface {p}"] + cmds + ["exit"]))
    for p in disable:
        st = states.get(norm(p))
        show_state(p, st, False)
        cmds = []
        if st is None or st["rx"]:
            cmds.append("no lldp receive")
        if st is None or st["tx"]:
            cmds.append("no lldp transmit")
        if cmds:
            blocks.append((f"{p} - LLDP disable", [f"interface {p}"] + cmds + ["exit"]))
    if not blocks:
        out(f"{G}  Butun LLDP veziyyeti artiq duzgundur -> hecne edilmir{X}")
        rec(ip, host, "lldp", "already correct", "UNCHANGED")
        return
    approved = select_blocks(ip, host, blocks)
    if not approved:
        return
    lines = [l for _, ls in approved for l in ls]
    out(white("  Config gonderilir..."))
    o = conn.send_config_set(lines, cmd_verify=False, read_timeout=60)
    out(white(o))
    for label, ls in approved:
        rec(ip, host, label, "; ".join(ls), "APPLIED")
    if confirm("Konfiqurasiyani yadda saxla", ["write memory"]):
        out(white(conn.send_command("write memory", read_timeout=120)))
        rec(ip, host, "write memory", "", "APPLIED")


# --------------------------------- main ---------------------------------
def process(ip, user, pw):
    header(ip)
    conn = None
    try:
        conn, dtype = connect(ip, user, pw)
        host = conn.find_prompt().strip("#> ")
        out(f"{white('Qosuldu:')} {ipc(ip)} {white(host + '  [' + dtype + ']')}")
        if MON:
            MON.set_host(ip, host)
        out(f"{B}{'-' * 78}{X}")
        if dtype == "cisco_xr":
            xr_flow(conn, ip, host)
        else:
            xe_flow(conn, ip, host)
    except (StopRequested, KeyboardInterrupt):
        raise
    except Exception as e:
        out(f"{R}  XETA {ip}: {e}{X}")
        rec(ip, "", "connect/run", str(e), "ERROR")
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                pass


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


def save_xlsx(path):
    try:
        from openpyxl import Workbook
    except ImportError:
        out(white("openpyxl yoxdur, Excel yaradilmadi (pip install openpyxl)"))
        return
    wb = Workbook()
    ws = wb.active
    ws.append(["Time", "IP", "Hostname", "Action", "Commands", "Status"])
    for r in REC:
        ws.append(r)
    wb.save(path)
    out(white(f"Excel: {path}"))


def flush_stdin():
    """Icra zamani tesadufen basilmis ENTER/duymeleri sil ki, 'Press ENTER' sorgusu atlanmasin"""
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
    global LOG, MON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = safe_prefix(ask_name("Log/hesabat fayllari ucun ad (bos buraxa bilersen): "))
    out_dir = pick_out_dir()
    log_path = os.path.join(out_dir, f"{prefix}lldp_execution_log_{ts}.txt")
    LOG = open(log_path, "w", encoding="utf-8")

    ips = load_ips()
    if not ips:
        print("IP tapilmadi.")
        wait_enter()
        return
    user = input("Username: ").strip()
    pw = getpass.getpass("Password: ")
    want_xlsx = input("Excel hesabat da yaradilsin? [y/N]: ").strip().lower() == "y"

    out(white(f"{len(ips)} cihaz: ") + "  ".join(ipc(i) for i in ips))
    if Monitor and input("Ping monitor penceresi acilsin? (XR ucun commit confirmed tesdiqi orada olacaq) [Y/n]: ").strip().lower() != "n":
        try:
            MON = Monitor(ips)
            time.sleep(1.5)
            if not MON.alive():
                out(f"{R}Ping monitor acilmadi (tkinter yoxdur?) -> adi commit istifade olunacaq{X}")
                MON = None
        except Exception as e:
            out(f"{R}Ping monitor xetasi: {e} -> adi commit istifade olunacaq{X}")
            MON = None
    elif not Monitor:
        out(white("live_monitor.py tapilmadi -> adi commit istifade olunacaq"))
    try:
        for ip in ips:
            process(ip, user, pw)
            time.sleep(2)  # ardicil cihazlar arasinda fasile
    except (StopRequested, KeyboardInterrupt):
        out(f"\n{R}Istifadeci terefinden dayandirildi.{X}")
    finally:
        try:
            out()
            out(f"{B}{'=' * 78}{X}")
            out(white(f"Execution log: {log_path}"))
            if MON:
                MON.done()
            if want_xlsx:
                save_xlsx(os.path.join(out_dir, f"{prefix}lldp_report_{ts}.xlsx"))
            LOG.flush()
        except Exception as e:
            print("Son emeliyyatlarda xeta:", e)
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
