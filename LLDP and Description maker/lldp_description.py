#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLDP qonsularina gore description duzeldir - Cisco IOS-XR (NCS) + IOS-XE (ASR 920 / 901)
Format: TO_<System Name>_<Port id>     (meselen TO_YVLK_NCS5508_B_Te0/2/0/0)

Qurulum : pip install netmiko colorama openpyxl
Isletmek: python lldp_description.py [ips.txt]
Dayandir: Ctrl+C
"""
import os
import re
import sys
import getpass
import time
from datetime import datetime

from netmiko import ConnectHandler, SSHDetect

try:
    from live_monitor import Monitor
except ImportError:
    Monitor = None

try:
    import colorama
    colorama.init()
except ImportError:
    os.system("")

# ----------------------------- AYARLAR --------------------------------
SHOW_RAW = False       # True -> xam cavabi da goster
SHORT_NUMBERS = False  # True -> TenGigE0/2/0/0 -> Te0/2 , TenGigE0/0/0/15 -> Te0/15 (4 hisseli nomrelerde sifirlari atir)
# -----------------------------------------------------------------------

W = "\033[97m"
R = "\033[91m"
G = "\033[92m"
B = "\033[1;94m"
Y = "\033[93m"
X = "\033[0m"

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOG = None
ALL_YES = False
REC = []

SHORT_PFX = {"tengige": "Te", "tengigabitethernet": "Te", "gigabitethernet": "Gi",
             "hundredgige": "Hu", "fortygige": "Fo", "twentyfivegige": "Twe",
             "fastethernet": "Fa", "te": "Te", "gi": "Gi", "hu": "Hu",
             "fo": "Fo", "twe": "Twe", "fa": "Fa"}
LONG_XE = {"te": "TenGigabitEthernet", "gi": "GigabitEthernet", "fo": "FortyGigabitEthernet",
           "hu": "HundredGigE", "fa": "FastEthernet", "twe": "TwentyFiveGigE"}

LOC_RE = re.compile(
    r"((?:TenGigE|HundredGigE|FortyGigE|TwentyFiveGigE|GigabitEthernet|Te|Hu|Fo|Twe|Gi|Fa)\d+(?:/\d+)+)"
    r"\s+(\d+)\s+(\S+)\s+(\S+)\s*$")
BUNDLE_RE = re.compile(r"(?i)^(bundle|be\d|port-channel|po\d)")


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


def finalize_status(ip, status):
    for r in REC:
        if r[1] == ip and r[5] == "PENDING":
            r[5] = status


def rec(ip, host, action, detail, status):
    REC.append([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, host, action, detail, status])


def norm(n):
    n = n.strip().lower()
    for a, b in (("twentyfivegige", "twe"), ("hundredgige", "hu"), ("fortygige", "fo"),
                 ("tengigabitethernet", "te"), ("tengige", "te"),
                 ("gigabitethernet", "gi"), ("fastethernet", "fa"),
                 ("bundle-ether", "be"), ("port-channel", "po")):
        if n.startswith(a):
            return b + n[len(a):]
    return n


def short_port(p):
    m = re.match(r"^([A-Za-z-]+)([\d/]+)$", p.strip())
    if not m:
        return p.strip()
    pre = SHORT_PFX.get(m.group(1).lower(), m.group(1))
    nums = m.group(2)
    if SHORT_NUMBERS:
        parts = nums.split("/")
        if len(parts) >= 4:
            rest = [x for x in parts[1:] if x != "0"] or ["0"]
            nums = "/".join([parts[0]] + rest)
    return pre + nums


def make_desc(sysname, portid):
    host = sysname.strip().split(".")[0]  # Mingachevir-C920-2.aztelekom.az -> Mingachevir-C920-2
    return "TO_" + re.sub(r"\s+", "_", host) + "_" + short_port(portid)


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


# ------------------------------ LLDP parse ------------------------------
def lldp_locals(o):
    """show lldp neighbors -> local interfeys siyahisi (Bundle port id olan setirler kecilir)"""
    locs = []
    for l in o.splitlines():
        m = LOC_RE.search(l.rstrip())
        if not m:
            continue
        loc, pid = m.group(1), m.group(4)
        if BUNDLE_RE.match(pid):
            out(white(f"  kecildi (bundle port id): {l.strip()}"))
            continue
        if loc not in locs:
            locs.append(loc)
    return locs


def lldp_detail(o):
    """detail cixisindan (port id bundle olmayan) ilk qonsunu qaytarir: (system name, port id, parent bundle)"""
    par = re.search(r"^Parent Interface:\s*(\S+)", o, re.M)
    parent = par.group(1) if par else None
    for block in re.split(r"^-{10,}\s*$", o, flags=re.M):
        pid = re.search(r"^Port id:\s*(.+?)\s*$", block, re.M)
        sn = re.search(r"^System Name:\s*(.+?)\s*$", block, re.M)
        if not pid or not sn:
            continue
        if BUNDLE_RE.match(pid.group(1)):
            continue
        return sn.group(1), pid.group(1), parent
    return None


def current_desc(o):
    d = {}
    for l in o.splitlines():
        t = l.split()
        if len(t) >= 3 and t[1].lower() == "up" and t[2].lower() == "up":
            d[norm(t[0])] = " ".join(t[3:])
    return d


def collect_changes(conn, locs, detail_cmd, cur, parent_fn=None):
    changes = []
    bundles = {}  # parent bundle -> qonsu hostname
    for loc in locs:
        o = run(conn, detail_cmd(loc))
        r = lldp_detail(o)
        if not r:
            out(white(f"  {loc}: uygun qonsu tapilmadi, kecildi"))
            continue
        sysname, pid, parent = r
        if not parent and parent_fn:
            parent = parent_fn(loc)
        if parent and BUNDLE_RE.match(parent):
            bundles.setdefault(parent, sysname)
        new = make_desc(sysname, pid)
        old = cur.get(norm(loc), "")
        out(white(f"  {loc}: qonsu={sysname}  port={pid}"))
        if old == new:
            out(white(f"      description artiq duzgundur: {new}"))
            continue
        changes.append((loc, old, new))
    for parent, sysname in bundles.items():
        new = "TO_" + re.sub(r"\s+", "_", sysname.strip().split(".")[0])
        old = cur.get(norm(parent), "")
        out(white(f"  {parent} (bundle): qonsu={sysname}"))
        if old == new:
            out(white(f"      description artiq duzgundur: {new}"))
            continue
        changes.append((parent, old, new))
    return changes


# -------------------------------- flows ---------------------------------
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


def xr_flow(conn, ip, host):
    locs = lldp_locals(run(conn, "show lldp neighbors"))
    if not locs:
        out(white("  LLDP qonsusu yoxdur"))
        return
    cur = current_desc(run(conn, "show interfaces description | i up"))
    changes = collect_changes(conn, locs, lambda l: f"show lldp neighbors {l} detail", cur)
    approved = []
    for loc, old, new in changes:
        out(white(f"  kohne: {old or '(bos)'}"))
        if confirm(f"{loc} description", [f"interface {loc}", f"description {new}", "root"]):
            approved.append((loc, new))
            rec(ip, host, f"{loc} description", f"{old} -> {new}", "PENDING")
        else:
            out(white("  -> kecildi"))
            rec(ip, host, f"{loc} description", f"{old} -> {new}", "SKIPPED")
    if not approved:
        return
    lines = []
    for loc, new in approved:
        lines += [f"interface {loc}", f"description {new}", "root"]
    out(white("  Config gonderilir..."))
    out(white(conn.send_config_set(lines, cmd_verify=False, exit_config_mode=False, read_timeout=60)))
    status = xr_commit(conn, ip, host)
    finalize_status(ip, status)


def xe_po_map(o):
    """show etherchannel summary -> {norm(uzv port): 'Port-channelN'}"""
    res = {}
    for l in o.splitlines():
        m = re.match(r"^\s*\d+\s+Po(\d+)\(", l)
        if m:
            for mem in re.findall(r"([A-Za-z]+\d+(?:/\d+)+)\(\w+\)", l):
                res[norm(mem)] = f"Port-channel{m.group(1)}"
    return res


def xe_flow(conn, ip, host):
    locs = lldp_locals(run(conn, "show lldp neighbors"))
    if not locs:
        out(white("  LLDP qonsusu yoxdur"))
        return
    cur = current_desc(run(conn, "show interfaces description | i up"))

    def dcmd(l):
        m = re.match(r"^([A-Za-z]+)([\d/]+)$", l)
        long_name = LONG_XE.get(m.group(1).lower(), m.group(1)) if m else l
        num = m.group(2) if m else ""
        return f"show lldp neighbors {long_name} {num} detail".replace("  ", " ")

    pm = xe_po_map(run(conn, "show etherchannel summary"))
    changes = collect_changes(conn, locs, dcmd, cur, parent_fn=lambda l: pm.get(norm(l)))
    approved = []
    for loc, old, new in changes:
        out(white(f"  kohne: {old or '(bos)'}"))
        if confirm(f"{loc} description", [f"interface {loc}", f"description {new}", "exit"]):
            approved.append((loc, new))
            rec(ip, host, f"{loc} description", f"{old} -> {new}", "APPLIED")
        else:
            out(white("  -> kecildi"))
            rec(ip, host, f"{loc} description", f"{old} -> {new}", "SKIPPED")
    if not approved:
        return
    lines = []
    for loc, new in approved:
        lines += [f"interface {loc}", f"description {new}", "exit"]
    out(white("  Config gonderilir..."))
    out(white(conn.send_config_set(lines, cmd_verify=False, read_timeout=60)))
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
    else:
        print(f"{path} tapilmadi. IP-leri yapisdir, bitirmek ucun bos setir:")
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
    ws.append(["Time", "IP", "Hostname", "Action", "Detail", "Status"])
    for r in REC:
        ws.append(r)
    wb.save(path)
    out(white(f"Excel: {path}"))


def main():
    global LOG, MON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = input("Log/hesabat fayllari ucun ad (bos buraxa bilersen): ").strip()
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")
    prefix = f"{name}_" if name else ""
    log_path = f"{prefix}lldp_description_log_{ts}.txt"
    LOG = open(log_path, "w", encoding="utf-8")

    ips = load_ips()
    if not ips:
        print("IP tapilmadi.")
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
        out()
        out(f"{B}{'=' * 78}{X}")
        out(white(f"Execution log: {log_path}"))
        if MON:
            MON.done()
        if want_xlsx:
            save_xlsx(f"{prefix}lldp_description_report_{ts}.xlsx")
        LOG.flush()
        input("Press ENTER to exit.")
        LOG.close()


if __name__ == "__main__":
    main()
