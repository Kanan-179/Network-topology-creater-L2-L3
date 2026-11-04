#!/usr/bin/env python3
"""
Aztelekom IP/MPLS Core & Access L2/L3 Routing Adjacency Weather Map Generator
=============================================================================
Xüsusiyyətlər:
  1. İşə düşdükdə sizdən ardıcıl olaraq soruşur:
       - Nəticələrin saxlanacağı **Qovluğun adı** (avtomatik yaradır)
       - **Mərkəz (Core NCS) IP-ləri** (məs: x.x.x.x, x,x,x,y)
       - **Access (ASR 920 / 901) IP-ləri** (vergül və ya boşluqla)
       - **SSH Login və Parol**
  2. Yalnız sizin verdiyiniz NCS və ASR IP-lərinə **paralel (eyni anda)** qoşulur.
  3. Qutuların içində yalnız cihazın **Hostname** və **IP** ünvanı yazılır.
  4. **Qatlı (Layered) Düzülüş + Anti-Overlap:**
       - Yuxarıdan aşağıya məntiqi ardıcıllıq: REMOTE CORE -> CORE NCS -> ACCESS L1 -> ACCESS L2.
       - Qutu ölçüləri real ölçülür, cihazlar heç vaxt üst-üstə düşmür; çox cihaz olduqda
         təbəqə sətirlərə bölünür və şəkil avtomatik böyüyür.
       - Xətt kəsişmələri barycenter sıralaması ilə minimuma endirilir; xətt nömrələri
         qutulara və bir-birinə toxunmur. Sağda nömrələnmiş link siyahısı verilir.
  5. Nəticədə yüksək keyfiyyətli **PNG (`l2_weathermap.png`)** və draw.io-da redaktə olunan
     **`l2_weathermap.drawio`** faylı verir.
"""

import argparse
import concurrent.futures
import getpass
import ipaddress
import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath

try:
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
    NETMIKO_AVAILABLE = True
except ImportError:
    NETMIKO_AVAILABLE = False


# =====================================================================
# 1. KÖMƏKÇİ FUNKSİYALAR VƏ PARSERLƏR (IOS-XR VƏ IOS-XE/IOS)
# =====================================================================

def parse_ip_list(raw_input: str) -> List[str]:
    """
    İstifadəçinin vergül, boşluq və ya nöqtəli vergüllə daxil etdiyi IP siyahısını təmizləyir.
    """
    tokens = re.split(r"[,\s;]+", raw_input.strip())
    ips: List[str] = []
    for tok in tokens:
        t = tok.strip()
        if t and t not in ips:
            ips.append(t)
    return ips


def clean_hostname(raw_name: str) -> str:
    """
    Promptdan və ya IS-IS System ID-dən gələn cihaz adını təmizləyir.
    """
    name = raw_name.strip()
    name = re.sub(r"^(RP/\d+/RP\d+/CPU\d+:)", "", name)
    name = name.rstrip("#>").strip()
    name = re.sub(r"\.\d{2}$", "", name)
    return name


def has_100_in_third_octet(ips: List[str]) -> bool:
    """
    Cihazın IP ünvanlarından hər hansı birinin 3-cü oktetinin '100' olub-olmadığını yoxlayır.
    """
    for ip in ips:
        parts = ip.strip().split(".")
        if len(parts) == 4 and parts[2] == "100":
            return True
    return False


def shorten_interface_name(intf: str) -> str:
    """
    Uzun interfeys adlarını qısaldır.
    """
    replacements = [
        ("Bundle-Ether", "BE"),
        ("TenGigabitEthernet", "Te"),
        ("TenGigE", "Te"),
        ("HundredGigE", "Hu"),
        ("GigabitEthernet", "Gi"),
        ("FastEthernet", "Fa"),
        ("TwentyFiveGigE", "TF"),
        ("FortyGigE", "Fo"),
        ("Port-channel", "Po"),
    ]
    short = intf.strip()
    for old, new in replacements:
        short = short.replace(old, new)
    return short


def get_subnet_30_key(ip_str: str) -> str:
    """
    P2P OSPF linkinin hər iki tərəfini eyni link kimi tanımaq üçün /30 şəbəkə ünvanını qaytarır.
    """
    try:
        net = ipaddress.IPv4Network(f"{ip_str}/30", strict=False)
        return str(net)
    except ValueError:
        return ip_str


def parse_isis_adjacency(output: str) -> List[Dict[str, str]]:
    """
    Cisco IOS-XR (`show isis adjacency`) və IOS-XE (`show isis neighbors`) çıxışını emal edir.
    """
    adjacencies = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("IS-IS", "System Id", "BFD", "Tag ", "Tue ", "Mon ", "Wed ", "Thu ", "Fri ", "Sat ", "Sun ")):
            continue
        match_xr = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            r"([A-Za-z0-9/\.\-:]+)\s+"
            r"(\*PtoP\*|[0-9a-fA-F\.]+)\s+"
            r"(Up|Init|Down|UP|INIT|DOWN)\s+"
            r"(\d+)\s+"
            r"([0-9a-zA-Z:]+)",
            line,
        )
        if match_xr:
            sys_id, intf, snpa, state, hold, uptime = match_xr.groups()
            adjacencies.append({
                "protocol": "IS-IS",
                "neighbor_id": clean_hostname(sys_id),
                "neighbor_ip": "",
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": uptime,
            })
            continue

        match_xe = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            r"(L1|L2|L1L2)\s+"
            r"([A-Za-z0-9/\.\-:]+)\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(UP|Up|INIT|Init|DOWN|Down)\s+"
            r"(\d+)",
            line,
        )
        if match_xe:
            sys_id, isis_type, intf, nbr_ip, state, hold = match_xe.groups()
            adjacencies.append({
                "protocol": "IS-IS",
                "neighbor_id": clean_hostname(sys_id),
                "neighbor_ip": nbr_ip,
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": f"Hold:{hold}s",
            })
    return adjacencies


def parse_ospf_neighbors(output: str) -> List[Dict[str, str]]:
    """
    Həm IOS-XR (`show ip ospf neighbor` / `show ospf neighbor`), həm də
    IOS-XE/IOS (`ASR 920` / `ASR 901`) çıxışlarını oxuyur.
    """
    neighbors = []
    current_nbr = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(
            r"^(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(\d+)\s+"
            r"([A-Za-z0-9]+\s*/\s*[A-Za-z0-9\-]+)\s+"
            r"([0-9:]+|\d+[wdhms]+)\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(\S+)",
            line,
        )
        if match:
            nbr_id, pri, state, dead_time, addr, intf = match.groups()
            current_nbr = {
                "protocol": "OSPF",
                "neighbor_id": nbr_id,
                "priority": pri,
                "state": state.replace(" ", ""),
                "dead_time": dead_time,
                "neighbor_ip": addr,
                "local_interface": intf,
                "uptime": "Up",
            }
            neighbors.append(current_nbr)
        elif current_nbr and "Neighbor is up for" in line:
            up_match = re.search(r"Neighbor is up for\s+(.+)$", line)
            if up_match:
                current_nbr["uptime"] = up_match.group(1).strip()
    return neighbors


def parse_local_ips(output: str) -> Set[str]:
    """
    `show ipv4 interface brief` və `show ip interface brief` çıxışından bütün lokal IP-ləri toplayır.
    """
    ips: Set[str] = set()
    for line in output.splitlines():
        for ip_match in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
            try:
                ipaddress.IPv4Address(ip_match)
                ips.add(ip_match)
            except ValueError:
                pass
    return ips


# =====================================================================
# 2. SÜRƏTLİ PARALEL SSH QOŞULMA (YALNIZ VERİLƏN NCS VƏ ASR İP-LƏRİNƏ)
# =====================================================================

def poll_single_device(
    ip: str,
    username: str,
    password: str,
    is_ncs: bool,
    timeout: int = 10,
) -> Optional[Dict]:
    """
    Verilmiş tək bir IP-yə (NCS və ya ASR) qoşulub Hostname, Lokal IP-lər,
    IS-IS və OSPF qonşuluqlarını sürətlə götürür.
    """
    if not NETMIKO_AVAILABLE:
        raise RuntimeError("Netmiko kitabxanası tapılmadı. Yükləmək üçün: pip install netmiko")

    device_types = ["cisco_xr", "cisco_xe"] if is_ncs else ["cisco_ios", "cisco_xe", "cisco_xr"]

    for dev_type in device_types:
        params = {
            "device_type": dev_type,
            "host": ip,
            "username": username,
            "password": password,
            "secret": password,
            "timeout": timeout,
            "conn_timeout": timeout,
            "fast_cli": False,
        }
        try:
            with ConnectHandler(**params) as conn:
                raw_prompt = conn.find_prompt()
                is_xr_prompt = raw_prompt.strip().startswith("RP/")
                hostname = clean_hostname(raw_prompt)

                if not is_xr_prompt and dev_type != "cisco_xr":
                    try:
                        conn.enable()
                        raw_prompt = conn.find_prompt()
                        hostname = clean_hostname(raw_prompt)
                    except Exception:
                        pass

                if not hostname or hostname == ip:
                    host_out = conn.send_command("show running-config | include hostname")
                    h_match = re.search(r"hostname\s+(\S+)", host_out)
                    if h_match:
                        hostname = clean_hostname(h_match.group(1))

                if is_xr_prompt:
                    ip_brief = conn.send_command("show ipv4 interface brief")
                    isis_out = conn.send_command("show isis adjacency")
                    ospf_out = conn.send_command("show ospf neighbor")
                    if "Invalid" in ospf_out or not ospf_out.strip():
                        ospf_out = conn.send_command("show ip ospf neighbor")
                else:
                    ip_brief = conn.send_command("show ip interface brief")
                    if "Invalid" in ip_brief or not ip_brief.strip():
                        ip_brief = conn.send_command("show ipv4 interface brief")
                    ospf_out = conn.send_command("show ip ospf neighbor")
                    if "Invalid" in ospf_out or not ospf_out.strip():
                        ospf_out = conn.send_command("show ospf neighbor")
                    isis_out = conn.send_command("show isis neighbors")
                    if "Invalid" in isis_out or not isis_out.strip():
                        isis_out = conn.send_command("show isis adjacency")

                local_ips = parse_local_ips(ip_brief)
                local_ips.add(ip)
                isis_neighbors = parse_isis_adjacency(isis_out)
                ospf_neighbors = parse_ospf_neighbors(ospf_out)

                actual_os = "cisco_xr" if (is_xr_prompt or is_ncs or "NCS" in hostname.upper()) else "cisco_xe"
                print(
                    f"  [OK] {hostname} ({ip}) | "
                    f"IS-IS qonşu: {len(isis_neighbors)}, OSPF qonşu: {len(ospf_neighbors)}"
                )
                return {
                    "hostname": hostname,
                    "mgmt_ip": ip,
                    "is_ncs": is_ncs or ("NCS" in hostname.upper()) or is_xr_prompt,
                    "device_type": actual_os,
                    "local_ips": sorted(local_ips),
                    "isis_neighbors": isis_neighbors,
                    "ospf_neighbors": ospf_neighbors,
                }
        except NetmikoAuthenticationException:
            print(f"  [!] Autentifikasiya xətası ({ip}): Login və ya parol yanlışdır.")
            return None
        except (NetmikoTimeoutException, Exception):
            continue

    print(f"  [X] Cihaza qoşulmaq mümkün olmadı: {ip}")
    return None


def collect_all_devices_parallel(
    ncs_ips: List[str],
    asr_ips: List[str],
    username: str,
    password: str,
    max_workers: int = 10,
) -> Tuple[Dict[str, Dict], Dict[str, str], Set[str]]:
    """
    İstifadəçinin verdiyi bütün NCS və ASR IP-lərinə paralel (eyni anda) qoşulur.
    """
    discovered_devices: Dict[str, Dict] = {}
    ip_to_host: Dict[str, str] = {}
    ncs_hosts: Set[str] = set()

    tasks: List[Tuple[str, bool]] = []
    seen_input_ips: Set[str] = set()
    for ip in ncs_ips:
        if ip not in seen_input_ips:
            seen_input_ips.add(ip)
            tasks.append((ip, True))
    for ip in asr_ips:
        if ip not in seen_input_ips:
            seen_input_ips.add(ip)
            tasks.append((ip, False))

    print(f"\n[*] Cəmi {len(tasks)} cihaza ({len(ncs_ips)} NCS, {len(asr_ips)} ASR) paralel qoşulma başladı...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(poll_single_device, ip, username, password, is_ncs): (ip, is_ncs)
            for ip, is_ncs in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            ip, is_ncs = future_map[future]
            try:
                dev_info = future.result()
            except Exception as exc:
                print(f"  [!] Xəta ({ip}): {exc}")
                continue

            if not dev_info:
                continue

            hostname = dev_info["hostname"]
            if dev_info["is_ncs"]:
                ncs_hosts.add(hostname)

            for lip in dev_info["local_ips"]:
                ip_to_host[lip] = hostname

            if hostname in discovered_devices:
                existing = discovered_devices[hostname]
                existing["local_ips"] = sorted(set(existing["local_ips"]) | set(dev_info["local_ips"]))
            else:
                discovered_devices[hostname] = dev_info

    return discovered_devices, ip_to_host, ncs_hosts


# =====================================================================
# 3. TOPOLOGİYANIN QURULMASI (YALNIZ HOSTNAME VƏ IP İLƏ)
# =====================================================================

def resolve_neighbor_hostname(
    nbr_id: str,
    nbr_ip: str,
    ip_to_host: Dict[str, str],
    current_host: str,
) -> Optional[str]:
    """
    OSPF qonşusunun `Neighbor ID` (RID) və ya `Address` (P2P IP) dəyərinə görə
    onun artıq daxil olduğumuz hansısa NCS və ya ASR cihazı olub-olmadığını tapır.
    """
    if nbr_id in ip_to_host and ip_to_host[nbr_id] != current_host:
        return ip_to_host[nbr_id]
    if nbr_ip in ip_to_host and ip_to_host[nbr_ip] != current_host:
        return ip_to_host[nbr_ip]
    return None


def build_unified_topology(
    discovered_devices: Dict[str, Dict],
    ip_to_host: Dict[str, str],
    ncs_hosts: Set[str],
) -> Tuple[Dict[str, Dict], List[Dict]]:
    """
    Toplanmış NCS və ASR məlumatlarından vahid, təkrarsız topologiya qurur.
    Qutuların üzərində yalnız Hostname və IP saxlanılır.
    """
    nodes: Dict[str, Dict] = {}
    links: List[Dict] = []

    total_ncs = len(ncs_hosts)

    for hostname, dev in discovered_devices.items():
        all_ips = [dev["mgmt_ip"]] + list(dev["local_ips"])
        if hostname in ncs_hosts or dev.get("is_ncs"):
            if has_100_in_third_octet(all_ips) or total_ncs == 1:
                role, tier = "deg1_core", 1
            else:
                role, tier = "deg2_core", 2
        else:
            role, tier = "deg3_access", 3

        nodes[hostname] = {
            "id": hostname,
            "label": f"{hostname}\n{dev['mgmt_ip']}",
            "mgmt_ip": dev["mgmt_ip"],
            "role": role,
            "tier": tier,
            "device_type": dev["device_type"],
            "local_ips": dev["local_ips"],
        }

    if ncs_hosts and not any(n["role"] == "deg1_core" for n in nodes.values()):
        first_ncs = sorted(ncs_hosts)[0]
        nodes[first_ncs]["role"] = "deg1_core"
        nodes[first_ncs]["tier"] = 1

    # IS-IS Linklərinin Birləşdirilməsi
    processed_isis_pairs: Set[Tuple[str, str]] = set()

    for host_a, dev_a in discovered_devices.items():
        isis_by_nbr: Dict[str, List[Dict]] = defaultdict(list)
        for adj in dev_a["isis_neighbors"]:
            nbr_name = clean_hostname(adj["neighbor_id"])
            if adj.get("neighbor_ip") and adj["neighbor_ip"] in ip_to_host:
                nbr_name = ip_to_host[adj["neighbor_ip"]]
            isis_by_nbr[nbr_name].append(adj)

        for nbr_name, adj_list in isis_by_nbr.items():
            pair_key = tuple(sorted([host_a, nbr_name]))
            if pair_key in processed_isis_pairs:
                continue
            processed_isis_pairs.add(pair_key)

            if nbr_name not in nodes:
                is_remote_core = any(k in nbr_name.upper() for k in ("NCS", "PE", "ROUTER", "MSE"))
                nbr_ip_str = adj_list[0].get("neighbor_ip", "")
                nodes[nbr_name] = {
                    "id": nbr_name,
                    "label": f"{nbr_name}\n{nbr_ip_str}".strip(),
                    "mgmt_ip": nbr_ip_str,
                    "role": "remote_isis_core" if is_remote_core else "deg3_access",
                    "tier": 0 if is_remote_core else 3,
                    "device_type": "cisco_xr" if is_remote_core else "cisco_xe",
                    "local_ips": [nbr_ip_str] if nbr_ip_str else [],
                }

            reverse_intfs: List[str] = []
            if nbr_name in discovered_devices:
                for rev_adj in discovered_devices[nbr_name]["isis_neighbors"]:
                    if clean_hostname(rev_adj["neighbor_id"]) == host_a:
                        reverse_intfs.append(shorten_interface_name(rev_adj["local_interface"]))

            for idx, adj in enumerate(adj_list):
                intf_a = shorten_interface_name(adj["local_interface"])
                if idx < len(reverse_intfs) and reverse_intfs[idx] != intf_a:
                    intf_label = f"{intf_a} ↔ {reverse_intfs[idx]}"
                else:
                    intf_label = intf_a

                links.append({
                    "source": host_a,
                    "target": nbr_name,
                    "protocol": "IS-IS",
                    "local_interface": intf_label,
                    "neighbor_ip": adj.get("neighbor_ip") or "P2P",
                    "p2p_ip": adj.get("neighbor_ip", ""),
                    "state": adj["state"],
                    "uptime": adj["uptime"],
                })

    # OSPF Linklərinin Birləşdirilməsi
    seen_ospf_links: Dict[Tuple[str, str, str], Dict] = {}
    directly_connected_to_ncs: Set[str] = set()

    for host_a, dev_a in discovered_devices.items():
        for nbr in dev_a["ospf_neighbors"]:
            nbr_id = nbr["neighbor_id"]
            nbr_ip = nbr["neighbor_ip"]
            intf_a = shorten_interface_name(nbr["local_interface"])
            subnet_key = get_subnet_30_key(nbr_ip)

            known_host_b = resolve_neighbor_hostname(nbr_id, nbr_ip, ip_to_host, host_a)

            if known_host_b:
                host_b = known_host_b
            else:
                host_b = f"OSPF_{nbr_id}"
                if host_b not in nodes:
                    is_from_ncs = (host_a in ncs_hosts)
                    nodes[host_b] = {
                        "id": host_b,
                        "label": f"{nbr_id}\n{nbr_ip}",
                        "mgmt_ip": nbr_id,
                        "role": "deg3_access",
                        "tier": 3 if is_from_ncs else 4,
                        "device_type": "cisco_xe",
                        "local_ips": [nbr_id, nbr_ip],
                    }

            if host_b == host_a:
                continue

            if host_a in ncs_hosts:
                directly_connected_to_ncs.add(host_b)
            elif host_b in ncs_hosts:
                directly_connected_to_ncs.add(host_a)

            pair = tuple(sorted([host_a, host_b]))
            dedup_key = (pair[0], pair[1], subnet_key)

            if dedup_key not in seen_ospf_links:
                link_obj = {
                    "source": host_a,
                    "target": host_b,
                    "protocol": "OSPF",
                    "local_interface": intf_a,
                    "intf_by_host": {host_a: intf_a},
                    "neighbor_ip": f"RID: {nbr_id} | P2P: {nbr_ip}",
                    "p2p_ip": nbr_ip,
                    "state": nbr["state"],
                    "uptime": nbr["uptime"],
                }
                seen_ospf_links[dedup_key] = link_obj
                links.append(link_obj)
            else:
                existing = seen_ospf_links[dedup_key]
                existing["intf_by_host"][host_a] = intf_a
                if len(existing["intf_by_host"]) == 2:
                    h1, h2 = existing["source"], existing["target"]
                    i1 = existing["intf_by_host"].get(h1, "")
                    i2 = existing["intf_by_host"].get(h2, "")
                    existing["local_interface"] = f"{i1} ↔ {i2}" if i1 != i2 else i1

    # Əgər ASR birbaşa NCS-ə deyil, başqa ASR-in üzərindən 2-ci OSPF qonşusu kimi çıxıbsa, tier=4 et
    for nid, n in nodes.items():
        if n["role"] == "deg3_access" and n["tier"] == 3:
            if nid not in directly_connected_to_ncs:
                connected_to_asr = any(
                    (l["source"] == nid and l["target"] not in ncs_hosts)
                    or (l["target"] == nid and l["source"] not in ncs_hosts)
                    for l in links
                )
                if connected_to_asr:
                    n["tier"] = 4

    # Linkləri ardıcıl nömrələ (1, 2, 3...) — əvvəlcə IS-IS, sonra OSPF
    links.sort(key=lambda x: (0 if x["protocol"] == "IS-IS" else 1, x["source"], x["target"], x["local_interface"]))
    for idx, link in enumerate(links, start=1):
        link["link_num"] = idx

    return nodes, links



# =====================================================================
# 4. QATLI (LAYERED) DÜZÜLÜŞ, ANTI-OVERLAP VƏ XƏTT MARŞRUTLAMASI
# =====================================================================
#   Məntiq (yuxarıdan aşağıya):
#       REMOTE CORE  ->  CORE NCS  ->  ACCESS L1  ->  ACCESS L2 ...
#   - Hər təbəqə ayrıca üfüqi zolaqdır (solda adı yazılır).
#   - Qutu ölçüləri matplotlib ilə REAL ölçülür -> qutular heç vaxt üst-üstə düşmür.
#   - Təbəqədə cihaz çox olduqda, sətirlərə bölünür (zigzaq/staggered), şəkil böyüyür.
#   - Təbəqə daxilində sıralama barycenter üsulu ilə aparılır (xətt kəsişmələri minimuma enir).
#   - Eyni düzülüş .drawio (redaktə olunan) faylı kimi də yazılır.
#   - Mgmt IP-sinin ilk 2 oktetı eyni olan cihazlar təbəqədə yan-yana saxlanılır.
#   - Xətlər ortoqonaldır (yumru künclü): hər xəttin öz portu və öz zolağı var,
#     iki xətt heç vaxt eyni koordinatı paylaşmır (üst-üstə düşmə = 0).

INCH_PER_UNIT = 0.30   # 1 koordinat vahidi = 0.30 düym (qutu ölçüləri ilə uyğun)
H_GAP = 3.6            # eyni sətirdə qutular arası üfüqi boşluq
V_GAP = 2.6            # eyni təbəqənin sətirləri arası şaquli boşluq
LAYER_GAP = 4.2        # təbəqələr arası şaquli boşluq (xətt və nömrələr üçün)
LANE_STEP = 0.5        # paralel üfüqi zolaqlar (lane) arası məsafə
PORT_SP = 0.30         # eyni kanalda iki şaquli xətt arası minimum məsafə
MAX_COLS = 8           # bir sətirdə maksimum cihaz (çox olduqda sətirlərə bölünür)


def node_style(n: Dict) -> Dict:
    role = n.get("role", "deg3_access")
    tier = n.get("tier", 3)
    if role == "deg1_core":
        return {"bg": "#1E1B4B", "border": "#FACC15", "border_w": 3.0, "font_sz": 11.0}
    if role == "deg2_core":
        return {"bg": "#2E1065", "border": "#C084FC", "border_w": 2.6, "font_sz": 10.5}
    if role == "remote_isis_core":
        return {"bg": "#1E293B", "border": "#FF5722", "border_w": 2.2, "font_sz": 9.8}
    return {"bg": "#0F172A", "border": "#00B0FF" if tier == 3 else "#38BDF8", "border_w": 2.1, "font_sz": 9.6}


def estimate_box_size(label: str) -> Tuple[float, float]:
    """Ehtiyat (fallback) ölçü hesablaması."""
    lines = label.splitlines() or [label]
    max_chars = max(len(line) for line in lines)
    return max(5.2, max_chars * 0.31 + 1.4), max(1.9, len(lines) * 0.82 + 0.7)


def measure_box_sizes(nodes: Dict[str, Dict]) -> Dict[str, Tuple[float, float]]:
    """Hər qutunun REAL en/hündürlüyünü (koordinat vahidi ilə) matplotlib-dən ölçür."""
    sizes: Dict[str, Tuple[float, float]] = {}
    try:
        fig = plt.figure(figsize=(4, 4), dpi=100)
        renderer = fig.canvas.get_renderer()
        for nid, n in nodes.items():
            st = node_style(n)
            fs = st["font_sz"]
            t = fig.text(0, 0, n["label"], fontsize=fs, fontweight="bold", fontfamily="DejaVu Sans")
            bb = t.get_window_extent(renderer)
            pad_in = 0.55 * fs / 72.0
            border_in = st["border_w"] / 72.0
            w_in = bb.width / fig.dpi + 2 * pad_in + border_in + 0.06
            h_in = bb.height / fig.dpi + 2 * pad_in + border_in + 0.06
            sizes[nid] = (w_in / INCH_PER_UNIT, h_in / INCH_PER_UNIT)
        plt.close(fig)
    except Exception:
        sizes = {nid: estimate_box_size(n["label"]) for nid, n in nodes.items()}
    return sizes


def widen_for_ports(nodes, links, sizes):
    """Linki çox olan cihazın qutusunu enləndirir ki, bütün xətlər qutunun kənarında ayrı port tapsın."""
    lv = {nid: _layer_value(n) for nid, n in nodes.items()}
    up: Dict[str, int] = defaultdict(int)
    down: Dict[str, int] = defaultdict(int)
    for l in links:
        a, b = l["source"], l["target"]
        if a == b or a not in lv or b not in lv:
            continue
        if lv[a] == lv[b]:
            up[a] += 1
            up[b] += 1
        elif lv[a] < lv[b]:
            down[a] += 1
            up[b] += 1
        else:
            down[b] += 1
            up[a] += 1
    out: Dict[str, Tuple[float, float]] = {}
    for nid, (w, h) in sizes.items():
        need = 0.40 * max(up[nid], down[nid]) / 0.92
        out[nid] = (max(w, need), h)
    return out


def _isotonic_spaced(desired: List[float], spacing: float) -> List[float]:
    """
    Sıranı pozmadan, qonşular arası məsafə >= spacing olmaqla, 'desired' mövqelərinə
    ən yaxın koordinatları tapır (Pool-Adjacent-Violators). Üst-üstə düşmə = 0.
    """
    n = len(desired)
    y = [d - i * spacing for i, d in enumerate(desired)]
    blocks: List[List[float]] = []
    for v in y:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    res: List[float] = []
    for s, c in blocks:
        res += [s / c] * int(c)
    return [res[i] + i * spacing for i in range(n)]


def _arrange_layer(order: List[str], desired: Dict[str, float], cell: float, rows: int):
    """Təbəqəni sütunlara böl (sütun-major). Sütunlar düz şəbəkədir ki, xətlər aralarından keçə bilsin."""
    cols = [order[i:i + rows] for i in range(0, len(order), rows)]
    col_des = [sum(desired[v] for v in c) / len(c) for c in cols]
    col_x = _isotonic_spaced(col_des, cell)
    xs: Dict[str, float] = {}
    sub: Dict[str, int] = {}
    for ci, c in enumerate(cols):
        for r, v in enumerate(c):
            xs[v] = col_x[ci]
            sub[v] = r
    return xs, sub


def _ip_group(n: Dict, nid: str) -> str:
    """Cihazın mgmt IP-sinin ilk 2 oktetini qrup açarı kimi qaytarır (məs. '10.53')."""
    parts = (n.get("mgmt_ip") or "").strip().split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:2])
    return "?" + nid      # IP bilinmirsə cihaz tək qrupdur


def _layer_value(n: Dict) -> int:
    t = int(n.get("tier", 3))
    if t == 0:
        return 0
    if t in (1, 2):
        return 1
    return t - 1          # tier3 -> 2, tier4 -> 3 ...


def _band_label(tiers: Set[int]) -> str:
    if 0 in tiers:
        return "REMOTE CORE"
    if tiers & {1, 2}:
        return "CORE NCS"
    return f"ACCESS L{min(tiers) - 2}"


def compute_layer_columns(
    nodes: Dict[str, Dict],
    links: List[Dict],
    sizes: Optional[Dict[str, Tuple[float, float]]] = None,
):
    """
    Qatlı düzülüşün X hissəsi. Qaytarır: (xs, row_of, rows, layer_rows)
      xs[nid]      - qutunun mərkəz X-i
      row_of[nid]  - qlobal sətir nömrəsi (yuxarıdan aşağı)
      rows[i]      - {"layer", "nodes", "h"}
      layer_rows   - [{"label", "rows": [...]}]  (təbəqə zolaqları üçün)
    """
    if sizes is None:
        sizes = {nid: estimate_box_size(n["label"]) for nid, n in nodes.items()}

    layer_val = {nid: _layer_value(n) for nid, n in nodes.items()}
    keys = sorted(set(layer_val.values()))
    layers: List[List[str]] = [
        sorted([nid for nid in nodes if layer_val[nid] == k], key=lambda i: (nodes[i].get("tier", 3), i))
        for k in keys
    ]
    layer_idx = {nid: i for i, lay in enumerate(layers) for nid in lay}
    fixed = {i for i, k in enumerate(keys) if k == 1}   # Core təbəqəsinin sırası sabit (tier, ad)

    nbrs: Dict[str, Set[str]] = defaultdict(set)
    for l in links:
        s, t = l["source"], l["target"]
        if s != t and s in nodes and t in nodes:
            nbrs[s].add(t)
            nbrs[t].add(s)

    total = len(nodes)
    max_cols = MAX_COLS if total <= 40 else MAX_COLS + 3
    rows = [max(1, math.ceil(len(lay) / max_cols)) for lay in layers]
    cells = [max(sizes[v][0] for v in lay) + H_GAP for lay in layers]

    grp = {nid: _ip_group(n, nid) for nid, n in nodes.items()}

    def order_layer(lay: List[str], des: Dict[str, float]) -> List[str]:
        """Eyni ilk-2-oktet qrupu təbəqədə bitişik qalır; qruplar və qrup daxili sıra barycenter-ə görədir."""
        acc: Dict[str, List[float]] = defaultdict(list)
        for v in lay:
            acc[grp[v]].append(des[v])
        gmean = {k: sum(x) / len(x) for k, x in acc.items()}
        return sorted(lay, key=lambda v: (gmean[grp[v]], grp[v], des[v], xs[v], v))

    xs: Dict[str, float] = {}
    subs: Dict[str, int] = {}

    def arrange(i: int, order: List[str], desired: Dict[str, float], center: bool = False) -> None:
        x, s = _arrange_layer(order, desired, cells[i], rows[i])
        if center and x:
            mid = (min(x.values()) + max(x.values())) / 2.0
            x = {v: val - mid for v, val in x.items()}
        xs.update(x)
        subs.update(s)
        layers[i] = order

    # İlkin düzülüş (təbəqələr mərkəzdə)
    for i, lay in enumerate(layers):
        if i not in fixed:
            lay = sorted(lay, key=lambda v: (grp[v], v))
        arrange(i, lay, {v: k * cells[i] for k, v in enumerate(lay)}, center=True)

    def desired_for(v: str, direction: str) -> float:
        me = layer_idx[v]
        if direction == "down":
            ref = [u for u in nbrs[v] if layer_idx[u] < me]
        else:
            ref = [u for u in nbrs[v] if layer_idx[u] > me]
        if not ref:
            ref = list(nbrs[v])
        if not ref:
            return xs[v]
        return sum(xs[u] for u in ref) / len(ref)

    sweeps = [("down", range(len(layers))), ("up", range(len(layers) - 1, -1, -1))]
    for _ in range(8):
        for direction, rng in sweeps:
            for i in rng:
                if i in fixed:
                    continue
                des = {v: desired_for(v, direction) for v in layers[i]}
                order = order_layer(layers[i], des)
                arrange(i, order, des)
    # Son keçid: yuxarıdan aşağı, valideynlərə görə dəqiqləşdir
    for i in range(len(layers)):
        if i in fixed:
            continue
        des = {v: desired_for(v, "down") for v in layers[i]}
        order = order_layer(layers[i], des)
        arrange(i, order, des)

    # Qlobal mərkəzləmə
    lo = min(xs[v] - sizes[v][0] / 2 for v in xs)
    hi = max(xs[v] + sizes[v][0] / 2 for v in xs)
    shift = (lo + hi) / 2.0
    xs = {v: x - shift for v, x in xs.items()}

    rows_list: List[Dict] = []
    row_of: Dict[str, int] = {}
    layer_rows: List[Dict] = []
    for i, lay in enumerate(layers):
        hmax = max(sizes[v][1] for v in lay)
        nrows = max(subs[v] for v in lay) + 1
        idxs: List[int] = []
        for r in range(nrows):
            members = [v for v in lay if subs[v] == r]
            rows_list.append({"layer": i, "nodes": members, "h": hmax})
            for v in members:
                row_of[v] = len(rows_list) - 1
            idxs.append(len(rows_list) - 1)
        layer_rows.append({
            "label": _band_label({int(nodes[v].get("tier", 3)) for v in lay}),
            "rows": idxs,
        })
    return xs, row_of, rows_list, layer_rows


def _route_edges(nodes, links, sizes, xs, row_of, rows):
    """
    Ortoqonal marşrutlama. Hər xəttin:
      - öz şaquli port nöqtəsi (qutunun kənarında, başqa xətlə eyni x-də deyil),
      - öz üfüqi zolağı (lane) var -> iki xətt heç vaxt eyni koordinatı paylaşmır.
    Qaytarır: (row_y, row_top, row_bot, paths)
    """
    nrows = len(rows)
    used: Dict[int, List[float]] = defaultdict(list)
    row_boxes = [[(xs[v], sizes[v][0]) for v in r["nodes"]] for r in rows]

    def alloc(chs, desired, lo, hi, sp=PORT_SP, avoid_rows=(), span=60.0):
        def ok(x):
            if x < lo or x > hi:
                return False
            for c in chs:
                for u in used[c]:
                    if abs(u - x) < sp:
                        return False
            for r in avoid_rows:
                for bx, bw in row_boxes[r]:
                    if abs(x - bx) < bw / 2.0 + 0.3:
                        return False
            return True
        steps = int(min(span, max(hi - lo, 1.0)) / 0.1) + 2
        for k in range(steps):
            for sgn in ((1, -1) if k else (1,)):
                x = desired + sgn * k * 0.1
                if ok(x):
                    for c in chs:
                        used[c].append(x)
                    return x
        return None

    def alloc_safe(chs, desired, lo, hi, **kw):
        for sp in (PORT_SP, 0.2, 0.12):
            x = alloc(chs, desired, lo, hi, sp=sp, **kw)
            if x is not None:
                return x
        x = min(max(desired, lo), hi)
        for c in chs:
            used[c].append(x)
        return x

    # --- Kənarların təsnifatı ---
    E: List[Dict] = []
    for li, l in enumerate(links):
        u, v = l["source"], l["target"]
        if u not in xs or v not in xs or u == v:
            continue
        ru, rv = row_of[u], row_of[v]
        if ru > rv or (ru == rv and xs[u] > xs[v]):
            u, v, ru, rv = v, u, rv, ru
        if ru == rv:
            lo_x, hi_x = xs[u], xs[v]
            between = any(lo_x < xs[c] < hi_x for c in rows[ru]["nodes"] if c not in (u, v))
            kind = "arc" if between else "side"
        else:
            kind = "adj" if rv == ru + 1 else "long"
        E.append({"li": li, "a": u, "b": v, "ra": ru, "rb": rv, "kind": kind, "num": l["link_num"], "lanes": []})

    # --- İstənilən port mövqeyi (qutu kənarında qonşunun x-inə görə sıralanır) ---
    side_items: Dict[Tuple[str, str], List[Tuple[int, str, str]]] = defaultdict(list)
    for e in E:
        if e["kind"] == "side":
            continue
        sa = "top" if e["kind"] == "arc" else "bottom"
        side_items[(e["a"], sa)].append((e["li"], "a", e["b"]))
        side_items[(e["b"], "top")].append((e["li"], "b", e["a"]))
    desired: Dict[Tuple[int, str], float] = {}
    for (nid, side), items in side_items.items():
        items.sort(key=lambda it: (xs[it[2]], links[it[0]]["link_num"]))
        n_it = len(items)
        step = min(1.0, 0.92 * sizes[nid][0] / n_it)
        for i, (li, end, _o) in enumerate(items):
            desired[(li, end)] = xs[nid] + (i - (n_it - 1) / 2.0) * step

    # --- Port və koridor ayrılması ---
    for e in sorted(E, key=lambda e: (e["rb"] - e["ra"], e["num"])):
        a, b, ra, rb, kind = e["a"], e["b"], e["ra"], e["rb"], e["kind"]
        if kind == "side":
            continue
        da, db = desired[(e["li"], "a")], desired[(e["li"], "b")]
        a_lo, a_hi = xs[a] - 0.46 * sizes[a][0], xs[a] + 0.46 * sizes[a][0]
        b_lo, b_hi = xs[b] - 0.46 * sizes[b][0], xs[b] + 0.46 * sizes[b][0]
        if kind == "arc":
            ch = ra - 1
            e["xa"] = alloc_safe([ch], da, a_lo, a_hi)
            e["xb"] = alloc_safe([ch], db, b_lo, b_hi)
            e["lanes"].append((ch, e["xa"], e["xb"], "A"))
        elif kind == "adj":
            ch = ra
            lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
            xm = None
            if lo <= hi and abs(da - db) <= 1.2:
                xm = alloc([ch], min(max((da + db) / 2.0, lo), hi), lo, hi)
            if xm is not None:
                e["xa"] = e["xb"] = xm
            else:
                e["xa"] = alloc_safe([ch], da, a_lo, a_hi)
                e["xb"] = alloc_safe([ch], db, b_lo, b_hi)
                e["lanes"].append((ch, e["xa"], e["xb"], "A"))
        else:  # long
            chs = list(range(ra, rb))
            mid_rows = list(range(ra + 1, rb))
            xc = alloc(chs, db, -1e9, 1e9, avoid_rows=mid_rows, span=400.0)
            if xc is None:
                xc = db
                for c in chs:
                    used[c].append(xc)
            e["xc"] = xc
            if a_lo <= xc <= a_hi:
                e["xa"] = xc
            else:
                e["xa"] = alloc_safe([ra], da, a_lo, a_hi)
                e["lanes"].append((ra, e["xa"], xc, "L1"))
            if b_lo <= xc <= b_hi:
                e["xb"] = xc
            else:
                e["xb"] = alloc_safe([rb - 1], db, b_lo, b_hi)
                e["lanes"].append((rb - 1, xc, e["xb"], "L2"))

    # --- Zolaqların (lane) paylanması: üst-üstə düşən aralıqlar fərqli zolağa düşür ---
    chan_items: Dict[int, List[Tuple[float, float, Tuple[int, str]]]] = defaultdict(list)
    for e in E:
        for ch, x1, x2, tag in e["lanes"]:
            if abs(x1 - x2) > 0.01:
                chan_items[ch].append((min(x1, x2), max(x1, x2), (e["li"], tag)))
    lane_idx: Dict[Tuple[int, str], int] = {}
    nlanes: Dict[int, int] = {}
    for ch, items in chan_items.items():
        items.sort()
        ends: List[float] = []
        for x1, x2, key in items:
            for j, en in enumerate(ends):
                if en + 0.4 <= x1:
                    ends[j] = x2
                    lane_idx[key] = j
                    break
            else:
                ends.append(x2)
                lane_idx[key] = len(ends) - 1
        nlanes[ch] = len(ends)

    # --- Şaquli yerləşdirmə (kanal hündürlüyü zolaq sayına görə) ---
    chan_h: Dict[int, float] = {}
    for c in range(-1, nrows - 1):
        nl = nlanes.get(c, 0)
        base = 0.0 if c == -1 else (LAYER_GAP if rows[c]["layer"] != rows[c + 1]["layer"] else V_GAP)
        chan_h[c] = max(base, (1.8 + (nl - 1) * LANE_STEP) if nl else 0.0)
    cursor = -chan_h[-1]
    row_top: List[float] = []
    row_bot: List[float] = []
    row_y: List[float] = []
    for r in range(nrows):
        h = rows[r]["h"]
        row_top.append(cursor)
        row_y.append(cursor - h / 2.0)
        row_bot.append(cursor - h)
        cursor -= h
        if r < nrows - 1:
            cursor -= chan_h[r]

    def chan_top(c: int) -> float:
        return row_top[0] + chan_h[-1] if c == -1 else row_bot[c]

    def lane_y(c: int, key: Tuple[int, str]) -> float:
        return chan_top(c) - 0.9 - lane_idx[key] * LANE_STEP

    # --- Çoxbucaqlıları (polyline) qur ---
    pair_groups: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for e in E:
        if e["kind"] == "side":
            pair_groups[(e["a"], e["b"])].append(e)
    side_y: Dict[int, float] = {}
    for (a, _b), grp in pair_groups.items():
        grp.sort(key=lambda e: e["num"])
        m = len(grp)
        step = min(0.5, 0.7 * sizes[a][1] / m)
        for i, e in enumerate(grp):
            side_y[e["li"]] = ((m - 1) / 2.0 - i) * step

    paths: Dict[int, List[Tuple[float, float]]] = {}
    for e in E:
        a, b, ra, rb, li = e["a"], e["b"], e["ra"], e["rb"], e["li"]
        wa, ha = sizes[a]
        wb, hb = sizes[b]
        if e["kind"] == "side":
            y = row_y[ra] + side_y[li]
            pts = [(xs[a] + wa / 2.0, y), (xs[b] - wb / 2.0, y)]
        elif e["kind"] == "arc":
            ly = lane_y(ra - 1, (li, "A"))
            pts = [(e["xa"], row_y[ra] + ha / 2.0), (e["xa"], ly), (e["xb"], ly), (e["xb"], row_y[rb] + hb / 2.0)]
        else:
            ya, yb = row_y[ra] - ha / 2.0, row_y[rb] + hb / 2.0
            pts = [(e["xa"], ya)]
            if e["kind"] == "adj":
                if abs(e["xa"] - e["xb"]) > 0.01:
                    ly = lane_y(ra, (li, "A"))
                    pts += [(e["xa"], ly), (e["xb"], ly)]
            else:
                xc = e["xc"]
                if abs(e["xa"] - xc) > 0.01:
                    l1 = lane_y(ra, (li, "L1"))
                    pts += [(e["xa"], l1), (xc, l1)]
                if abs(e["xb"] - xc) > 0.01:
                    l2 = lane_y(rb - 1, (li, "L2"))
                    pts += [(xc, l2), (e["xb"], l2)]
            pts.append((e["xb"], yb))
        # təkrar və düz xətt üzərindəki artıq nöqtələri təmizlə
        clean = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
                clean.append(p)
        simp = [clean[0]]
        for i in range(1, len(clean) - 1):
            p0, p1, p2 = simp[-1], clean[i], clean[i + 1]
            if (abs(p0[0] - p1[0]) < 1e-6 and abs(p1[0] - p2[0]) < 1e-6) or \
               (abs(p0[1] - p1[1]) < 1e-6 and abs(p1[1] - p2[1]) < 1e-6):
                continue
            simp.append(p1)
        simp.append(clean[-1])
        paths[li] = simp
    return row_y, row_top, row_bot, paths


def build_layout(nodes, links, sizes):
    """Qaytarır: (pos, bands, paths). pos = qutu mərkəzləri, paths[link_idx] = ortoqonal nöqtələr."""
    xs, row_of, rows, layer_rows = compute_layer_columns(nodes, links, sizes)
    row_y, row_top, row_bot, paths = _route_edges(nodes, links, sizes, xs, row_of, rows)
    pos = {v: (xs[v], row_y[row_of[v]]) for v in xs}
    bands = [{
        "label": lr["label"],
        "top": row_top[lr["rows"][0]],
        "bottom": row_bot[lr["rows"][-1]],
    } for lr in layer_rows]
    return pos, bands, paths


def export_drawio(nodes, links, pos, sizes, paths, bands, out_path: Path) -> None:
    """
    Eyni düzülüşü draw.io (diagrams.net) faylı kimi yazır: qutular, ortoqonal xətlər (waypoint-lərlə),
    nömrələr və təbəqə zolaqları tam REDAKTƏ OLUNAN obyektlərdir.
    """
    from xml.sax.saxutils import escape, quoteattr

    S = 30.0   # 1 koordinat vahidi = 30 piksel
    x_lo = min(pos[n][0] - sizes[n][0] / 2 for n in pos) - 4.0
    x_hi = max(pos[n][0] + sizes[n][0] / 2 for n in pos) + 2.0
    ys = [p[1] for pts in paths.values() for p in pts] + [pos[n][1] + sizes[n][1] / 2 for n in pos]
    y_hi = max(ys) + 2.0

    def X(x: float) -> float:
        return round((x - x_lo) * S, 1)

    def Y(y: float) -> float:
        return round((y_hi - y) * S, 1)

    cells: List[str] = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    for bi, b in enumerate(bands):
        cells.append(
            f'<mxCell id="band{bi}" value={quoteattr(b["label"])} vertex="1" parent="1" '
            f'style="rounded=0;html=1;fillColor=#111B33;strokeColor=#1E293B;fontColor=#64748B;fontStyle=1;'
            f'fontSize=12;align=left;verticalAlign=top;spacingLeft=8;spacingTop=4;">'
            f'<mxGeometry x="{X(x_lo + 0.4)}" y="{Y(b["top"] + 0.8)}" '
            f'width="{round((x_hi - x_lo - 0.8) * S, 1)}" height="{round((b["top"] - b["bottom"] + 1.6) * S, 1)}" '
            f'as="geometry"/></mxCell>'
        )

    cell_id: Dict[str, str] = {}
    for i, (nid, n) in enumerate(nodes.items()):
        if nid not in pos:
            continue
        cell_id[nid] = f"n{i}"
        st = node_style(n)
        w, h = sizes[nid]
        cx, cy = pos[nid]
        html_label = "<br>".join(escape(part) for part in n["label"].split("\n"))
        cells.append(
            f'<mxCell id="n{i}" value={quoteattr(html_label)} vertex="1" parent="1" '
            f'style="rounded=1;arcSize=18;whiteSpace=wrap;html=1;fillColor={st["bg"]};strokeColor={st["border"]};'
            f'strokeWidth={st["border_w"]};fontColor=#F8FAFC;fontStyle=1;fontSize=12;">'
            f'<mxGeometry x="{X(cx - w / 2)}" y="{Y(cy + h / 2)}" width="{round(w * S, 1)}" '
            f'height="{round(h * S, 1)}" as="geometry"/></mxCell>'
        )

    def terminal(pt, a: str, b: str):
        best = None
        for nid in (a, b):
            if nid not in pos:
                continue
            cx, cy = pos[nid]
            w, h = sizes[nid]
            d = math.hypot(max(abs(pt[0] - cx) - w / 2, 0), max(abs(pt[1] - cy) - h / 2, 0))
            if best is None or d < best[0]:
                best = (d, nid)
        nid = best[1]
        cx, cy = pos[nid]
        w, h = sizes[nid]
        rx = min(max((pt[0] - (cx - w / 2)) / w, 0.0), 1.0)
        ry = min(max(((cy + h / 2) - pt[1]) / h, 0.0), 1.0)
        return nid, round(rx, 4), round(ry, 4)

    for li, pts in paths.items():
        link = links[li]
        a, b = link["source"], link["target"]
        s_nid, s_rx, s_ry = terminal(pts[0], a, b)
        t_nid, t_rx, t_ry = terminal(pts[-1], a, b)
        if s_nid == t_nid:
            t_nid = b if s_nid == a else a
        is_isis = link["protocol"] == "IS-IS"
        color = "#FF5722" if is_isis else "#00B0FF"
        badge = "#FF7043" if is_isis else "#38BDF8"
        dashed = "" if link["state"].upper().startswith(("UP", "FULL")) else "dashed=1;"
        way = "".join(f'<mxPoint x="{X(p[0])}" y="{Y(p[1])}"/>' for p in pts[1:-1])
        tip = f'{link["protocol"]} {a} <-> {b} | {link["local_interface"]} | {link["state"]} {link.get("uptime", "")}'
        cells.append(
            f'<mxCell id="e{li}" value="{link["link_num"]}" tooltip={quoteattr(tip)} edge="1" parent="1" '
            f'source="{cell_id[s_nid]}" target="{cell_id[t_nid]}" '
            f'style="edgeStyle=none;rounded=1;html=1;endArrow=none;startArrow=none;strokeColor={color};'
            f'strokeWidth={2.8 if is_isis else 2.3};{dashed}'
            f'exitX={s_rx};exitY={s_ry};exitDx=0;exitDy=0;exitPerimeter=0;'
            f'entryX={t_rx};entryY={t_ry};entryDx=0;entryDy=0;entryPerimeter=0;'
            f'labelBackgroundColor={badge};fontColor=#0B1120;fontStyle=1;fontSize=11;">'
            f'<mxGeometry relative="1" as="geometry"><Array as="points">{way}</Array></mxGeometry></mxCell>'
        )

    lines = []
    for link in links:
        src = link["source"].replace("OSPF_", "")
        dst = link["target"].replace("OSPF_", "")
        lines.append(escape(
            f'{link["link_num"]}. [{link["protocol"]}] {src} <-> {dst} | '
            f'{shorten_interface_name(link["local_interface"])} | {link.get("p2p_ip", "")} | '
            f'{link["state"]} {link.get("uptime", "")}'
        ))
    cells.append(
        f'<mxCell id="linklist" value={quoteattr("<b>BAĞLANTILARIN SİYAHISI</b><br>" + "<br>".join(lines))} '
        f'vertex="1" parent="1" style="text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;'
        f'fontColor=#E2E8F0;fontSize=11;">'
        f'<mxGeometry x="{X(x_hi + 1.0)}" y="{Y(y_hi - 0.5)}" width="900" '
        f'height="{max(200, 20 * (len(lines) + 2))}" as="geometry"/></mxCell>'
    )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile host="app.diagrams.net"><diagram name="Weather Map" id="weathermap">'
        '<mxGraphModel dx="1600" dy="1000" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" '
        'arrows="1" fold="1" page="0" pageScale="1" background="#0B1120" math="0" shadow="0"><root>'
        + "".join(cells) +
        '</root></mxGraphModel></diagram></mxfile>'
    )
    out_path.write_text(xml, encoding="utf-8")
    print(f"[+] draw.io faylı yaradıldı: {out_path}")


def generate_image_weathermap(
    nodes: Dict[str, Dict],
    links: List[Dict],
    png_path: Path,
) -> None:
    """
    Yüksək keyfiyyətli PNG Weather Map. Qatlı (REMOTE CORE -> CORE -> ACCESS L1 -> L2) düzülüş,
    qutular və nömrələr heç vaxt üst-üstə düşmür, şəkil ölçüsü cihaz sayına görə avtomatik böyüyür.
    """
    sizes = widen_for_ports(nodes, links, measure_box_sizes(nodes))
    pos, bands, paths = build_layout(nodes, links, sizes)
    export_drawio(nodes, links, pos, sizes, paths, bands, Path(png_path).with_suffix(".drawio"))

    # ---------------- Ölçülər və Figure ----------------
    x_lo = min(pos[n][0] - sizes[n][0] / 2 for n in pos)
    x_hi = max(pos[n][0] + sizes[n][0] / 2 for n in pos)
    xlim0, xlim1 = x_lo - 4.0, x_hi + 1.6
    min_w = 12.5 / INCH_PER_UNIT
    if xlim1 - xlim0 < min_w:
        extra = (min_w - (xlim1 - xlim0)) / 2.0
        xlim0 -= extra
        xlim1 += extra
    ys_all = [p[1] for pts in paths.values() for p in pts]
    ys_all += [pos[n][1] + sizes[n][1] / 2 for n in pos] + [pos[n][1] - sizes[n][1] / 2 for n in pos]
    ylim1 = max(ys_all) + 2.4
    ylim0 = min(ys_all) - 2.4
    ax_w = (xlim1 - xlim0) * INCH_PER_UNIT
    ax_h = (ylim1 - ylim0) * INCH_PER_UNIT

    num_links = len(links)
    list_cols = 1 if num_links <= 22 else (2 if num_links <= 60 else 3)
    rows_per_col = max(1, math.ceil(num_links / list_cols))
    col_w_in, row_in = 6.8, 0.66
    list_w = list_cols * col_w_in
    total_units = float(rows_per_col) + 1.6
    list_h = total_units * row_in

    margin, gap, top_room, bottom_room = 0.3, 0.5, 1.4, 1.1
    fig_w = margin + ax_w + gap + list_w + margin
    fig_h = max(ax_h, list_h) + top_room + bottom_room
    dpi = max(110, min(300, int((6.0e7 / (fig_w * fig_h)) ** 0.5)))

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi, facecolor="#0B1120")
    ax = fig.add_axes([margin / fig_w, (fig_h - top_room - ax_h) / fig_h, ax_w / fig_w, ax_h / fig_h])
    ax_list = fig.add_axes([
        (margin + ax_w + gap) / fig_w, (fig_h - top_room - list_h) / fig_h, list_w / fig_w, list_h / fig_h
    ])
    ax.set_facecolor("#0B1120")
    ax.set_xlim(xlim0, xlim1)
    ax.set_ylim(ylim0, ylim1)

    # ---------------- Təbəqə zolaqları ----------------
    for bi, b in enumerate(bands):
        ax.add_patch(Rectangle(
            (xlim0 + 0.4, b["bottom"] - 0.8), (xlim1 - xlim0) - 0.8, (b["top"] - b["bottom"]) + 1.6,
            facecolor="#111B33" if bi % 2 == 0 else "#0E1730", edgecolor="#1E293B", linewidth=1.0, zorder=0,
        ))
        ax.text(
            xlim0 + 1.5, (b["top"] + b["bottom"]) / 2.0, b["label"], rotation=90, ha="center", va="center",
            fontsize=9.5, fontweight="bold", color="#64748B", fontfamily="DejaVu Sans", zorder=1,
        )

    # ---------------- Xətləri çək (ortoqonal, yumru künclü) ----------------
    def rounded_path(pts, r=0.5):
        verts = [pts[0]]
        codes = [MplPath.MOVETO]
        for i in range(1, len(pts) - 1):
            p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
            l1 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            l2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            rr = min(r, l1 / 2.0, l2 / 2.0)
            pa = (p1[0] - (p1[0] - p0[0]) / l1 * rr, p1[1] - (p1[1] - p0[1]) / l1 * rr)
            pb = (p1[0] + (p2[0] - p1[0]) / l2 * rr, p1[1] + (p2[1] - p1[1]) / l2 * rr)
            verts += [pa, p1, pb]
            codes += [MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3]
        verts.append(pts[-1])
        codes.append(MplPath.LINETO)
        return MplPath(verts, codes)

    for li, pts in paths.items():
        link = links[li]
        is_isis = link["protocol"] == "IS-IS"
        ax.add_patch(PathPatch(
            rounded_path(pts),
            facecolor="none",
            edgecolor="#FF5722" if is_isis else "#00B0FF",
            linewidth=2.8 if is_isis else 2.3,
            linestyle="solid" if link["state"].upper().startswith(("UP", "FULL")) else "dashed",
            alpha=0.95,
            zorder=2,
            capstyle="round",
            joinstyle="round",
        ))

    # ---------------- Nömrə nişanları (qutulara və bir-birinə toxunmur) ----------------
    placed_badges: List[Tuple[float, float]] = []
    boxes = [(pos[n][0], pos[n][1], sizes[n][0], sizes[n][1]) for n in pos]

    def hits_box(cx: float, cy: float) -> bool:
        return any(abs(cx - bx) < bw / 2 + 0.65 and abs(cy - by) < bh / 2 + 0.65 for bx, by, bw, bh in boxes)

    def hits_badge(cx: float, cy: float) -> bool:
        return any(math.hypot(cx - px, cy - py) < 1.25 for px, py in placed_badges)

    def seg_dist(px, py, p, q) -> float:
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln2 = dx * dx + dy * dy
        t = 0.0 if ln2 == 0 else max(0.0, min(1.0, ((px - p[0]) * dx + (py - p[1]) * dy) / ln2))
        return math.hypot(px - (p[0] + t * dx), py - (p[1] + t * dy))

    all_segs = [(li, p, q) for li, pts in paths.items() for p, q in zip(pts[:-1], pts[1:])]

    for li in sorted(paths, key=lambda i: links[i]["link_num"]):
        pts = paths[li]
        segs = sorted(zip(pts[:-1], pts[1:]), key=lambda s: -math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]))
        cands = []
        for p, q in segs:
            if math.hypot(q[0] - p[0], q[1] - p[1]) < 1.0:
                continue
            for f in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
                cands.append((p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f))
        if not cands:
            cands = [((pts[0][0] + pts[-1][0]) / 2.0, (pts[0][1] + pts[-1][1]) / 2.0)]
        best, best_score = None, None
        for cx, cy in cands:
            if hits_box(cx, cy) or hits_badge(cx, cy):
                continue
            near = sum(1 for oli, p, q in all_segs if oli != li and seg_dist(cx, cy, p, q) < 0.6)
            if best is None or near < best_score:
                best, best_score = (cx, cy), near
                if near == 0:
                    break
        if best is None:
            best = cands[0]
        placed_badges.append(best)
        is_isis = links[li]["protocol"] == "IS-IS"
        ax.text(
            best[0], best[1], str(links[li]["link_num"]),
            fontsize=9.5, fontfamily="DejaVu Sans", fontweight="bold", color="#0B1120",
            ha="center", va="center", zorder=4,
            bbox=dict(boxstyle="circle,pad=0.32", facecolor="#FF7043" if is_isis else "#38BDF8",
                      edgecolor="#FFFFFF", linewidth=1.3),
        )

    # ---------------- Cihaz qutuları (Hostname + IP) ----------------
    for node_id, n in nodes.items():
        if node_id not in pos:
            continue
        st = node_style(n)
        bw, bh = sizes[node_id]
        cx, cy = pos[node_id]
        ax.add_patch(FancyBboxPatch(
            (cx - bw / 2.0, cy - bh / 2.0), bw, bh,
            boxstyle="round,pad=0,rounding_size=0.55",
            facecolor=st["bg"], edgecolor=st["border"], linewidth=st["border_w"], zorder=5,
        ))
        ax.text(
            cx, cy, n["label"],
            fontsize=st["font_sz"], fontfamily="DejaVu Sans", fontweight="bold", color="#F8FAFC",
            ha="center", va="center", zorder=6,
        )

    # ---------------- Başlıq və Legenda ----------------
    isis_count = sum(1 for l in links if l["protocol"] == "IS-IS")
    ospf_count = sum(1 for l in links if l["protocol"] == "OSPF")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ax.set_title(
        "AZTELEKOM L2/L3 ROUTING WEATHER MAP\n"
        f"Cihaz sayı: {len(nodes)}  |  Ümumi Bağlantı (Link): {len(links)} "
        f"(IS-IS: {isis_count}, OSPF: {ospf_count})  |  Tarix: {now_str}",
        fontsize=14, fontweight="bold", color="#F8FAFC", pad=18,
    )
    legend_elements = [
        Line2D([0], [0], color="#FF5722", lw=3.5, label="IS-IS Bağlantısı (Narıncı)"),
        Line2D([0], [0], color="#00B0FF", lw=2.8, label="OSPF Bağlantısı (Mavi)"),
        Line2D([0], [0], marker="s", color="#0B1120", label="Əsas Core NCS (.100.)",
               markerfacecolor="#1E1B4B", markeredgecolor="#FACC15", markersize=11),
        Line2D([0], [0], marker="s", color="#0B1120", label="Mərkəz Core NCS",
               markerfacecolor="#2E1065", markeredgecolor="#C084FC", markersize=11),
        Line2D([0], [0], marker="s", color="#0B1120", label="Access ASR 920 / 901",
               markerfacecolor="#0F172A", markeredgecolor="#00B0FF", markersize=11),
    ]
    ax.legend(
        handles=legend_elements, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=5,
        frameon=True, facecolor="#1E293B", edgecolor="#475569", fontsize=9.2, labelcolor="#F8FAFC",
    )
    ax.axis("off")

    # ---------------- SAĞ PANEL: Nömrələnmiş Bağlantı Siyahısı ----------------
    ax_list.set_xlim(0.0, float(list_cols))
    ax_list.set_ylim(0.0, total_units)
    ax_list.axis("off")

    ax_list.text(
        float(list_cols) / 2.0, total_units - 0.35, "BAĞLANTILARIN SİYAHISI (1, 2, 3...)",
        fontsize=12.5, fontweight="bold", color="#FACC15", ha="center", va="center", fontfamily="DejaVu Sans",
    )
    ax_list.plot([0.03, float(list_cols) - 0.03], [total_units - 0.72, total_units - 0.72],
                 color="#334155", linewidth=1.5)

    for idx, link in enumerate(links):
        col_idx = idx // rows_per_col
        row_idx = idx % rows_per_col
        x_base = float(col_idx)
        y_center = (total_units - 1.25) - float(row_idx) * 1.0

        lnum = link["link_num"]
        is_isis = link["protocol"] == "IS-IS"
        badge_bg = "#FF7043" if is_isis else "#38BDF8"
        proto_color = "#FF8A65" if is_isis else "#7DD3FC"

        src_name = link["source"].replace("OSPF_", "")
        dst_name = link["target"].replace("OSPF_", "")
        intf_str = shorten_interface_name(link["local_interface"])
        p2p_ip = link.get("p2p_ip", "")
        uptime = link.get("uptime", "")

        if is_isis:
            detail_line_1 = f"[{link['protocol']}]  {src_name}  ↔  {dst_name}"
            detail_line_2 = f"Port: {intf_str}   |   State: {link['state']} ({uptime})"
        else:
            ip_part = f"IP: {p2p_ip}   |   " if p2p_ip else ""
            detail_line_1 = f"[{link['protocol']}]  {src_name}  ↔  {dst_name}"
            detail_line_2 = f"Port: {intf_str}   |   {ip_part}Uptime: {uptime}"

        ax_list.text(
            x_base + 0.065, y_center, f"{lnum}", fontsize=9.0, fontweight="bold", color="#0B1120",
            ha="center", va="center", fontfamily="DejaVu Sans",
            bbox=dict(boxstyle="circle,pad=0.28", facecolor=badge_bg, edgecolor="#FFFFFF", linewidth=1.0),
        )
        ax_list.text(x_base + 0.125, y_center + 0.17, detail_line_1, fontsize=8.6, fontweight="bold",
                     color=proto_color, ha="left", va="center", fontfamily="DejaVu Sans")
        ax_list.text(x_base + 0.125, y_center - 0.19, detail_line_2, fontsize=8.0, color="#E2E8F0",
                     ha="left", va="center", fontfamily="DejaVu Sans")

        sep_y = y_center - 0.50
        if row_idx < rows_per_col - 1:
            ax_list.plot([x_base + 0.03, x_base + 0.97], [sep_y, sep_y], color="#1E293B", linewidth=0.8)

    fig.savefig(png_path, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    print(f"[+] PNG Weather Map şəkli yaradıldı: {png_path}")


# =====================================================================
# 5. DEMO REJİMİ
# =====================================================================



# =====================================================================
# 6. ƏSAS İCRA FUNKSİYASI (YALNIZ PNG ÇIXIŞI)
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Aztelekom IS-IS & OSPF L3 Weather Map (PNG)")
    parser.add_argument("--demo", action="store_true", help="Nümunə məlumatlarla test PNG şəkli yarat")
    parser.add_argument("--ncs-ips", nargs="*", help="Mərkəz (Core NCS) IP ünvanları")
    parser.add_argument("--asr-ips", nargs="*", help="Access (ASR 920/901) IP ünvanları")
    parser.add_argument("--workers", type=int, default=10, help="Paralel SSH qoşulma sayı (default: 10)")
    parser.add_argument("--output-dir", type=str, default="", help="Çıxış qovluğunun adı")
    args = parser.parse_args()

    if args.demo:
        out_dir = Path(args.output_dir or ".")
        out_dir.mkdir(parents=True, exist_ok=True)
        run_demo_mode(out_dir)
        return

    print("=== Aztelekom L2/L3 Weather Map Topologiya Kəşfiyyatı (PNG) ===")

    # 1. İstifadəçidən qovluq adını soruş və avtomatik yarat
    folder_name = args.output_dir.strip()
    if not folder_name:
        folder_name = input("1) Nəticələrin saxlanacağı qovluğun adını daxil edin (məs: Region_Topologiya): ").strip()
    if not folder_name:
        folder_name = f"weathermap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    out_dir = Path(folder_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"   [+] Qovluq hazırlandı: {out_dir.resolve()}")

    # 2. Mərkəz (Core NCS) IP-lərini soruş
    ncs_ips = args.ncs_ips or []
    if not ncs_ips:
        raw_ncs = input("2) Mərkəz (Core NCS) cihazların IP ünvanlarını daxil edin (məs: x.x.x.x, x.x.x.y): ").strip()
        ncs_ips = parse_ip_list(raw_ncs)

    if not ncs_ips:
        print("[!] Heç bir NCS IP daxil edilmədi. Çıxılır.")
        sys.exit(1)

    # 3. Access (ASR 920 / 901) IP-lərini soruş
    asr_ips = args.asr_ips or []
    if not asr_ips:
        raw_asr = input("3) Access (ASR 920/901) cihazların IP ünvanlarını daxil edin (vergül və ya boşluqla): ").strip()
        asr_ips = parse_ip_list(raw_asr)

    # 4. SSH Login və Parolu soruş
    username = input("4) SSH Login (Username): ").strip()
    password = getpass.getpass("5) SSH Parol (Password): ")

    discovered_devices, ip_to_host, ncs_hosts = collect_all_devices_parallel(
        ncs_ips=ncs_ips,
        asr_ips=asr_ips,
        username=username,
        password=password,
        max_workers=args.workers,
    )

    if not discovered_devices:
        print("[!] Heç bir cihazdan məlumat toplana bilmədi.")
        sys.exit(1)

    nodes, links = build_unified_topology(discovered_devices, ip_to_host, ncs_hosts)

    png_path = out_dir / "l2_weathermap.png"
    generate_image_weathermap(nodes, links, png_path)
    print(f"\n[✓] PNG Weather Map şəkli hazırlandı: '{png_path.resolve()}'")


if __name__ == "__main__":
    main()