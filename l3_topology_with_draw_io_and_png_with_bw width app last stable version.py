#!/usr/bin/env python3
"""
L3 Topology With Draw Io And Png with BW — Desktop Application (GUI & CLI)
==========================================================================
Xüsusiyyətlər:
  1. **Desktop Application (Tkinter GUI):**
       - Proqramı işə saldıqda müasir qaranlıq mövzulu (Dark Theme) Desktop pəncərəsi açılır.
       - İstəyə görə `--cli` bayrağı ilə terminal rejimində və ya `--demo` ilə test rejimində işləyir.
  2. **Avtomatik Subnet Ping Skaneri (`Subnet -> Ping -> SSH`):**
       - Bir və ya bir neçə Subnet daxil edirsiniz (məs: `10.58.1.0/24, 10.58.100.0/24`).
       - Əvvəlcə daxil edilmiş subnet(lər)in bütün IP ünvanlarını sürətli paralel ping ilə
         yoxlayıb bitirir, yalnız ping keçən (aktiv) IP-ləri seçir.
  3. **Eyni Cihazdakı Təkrar IP-lərin Avtomatik Ötürülməsi (`10.58.1.101` vs `10.58.100.101`):**
       - Əgər `10.58.1.101` və `10.58.100.101` eyni cihazdadırsa, ilk olaraq `10.58.1.101`
         IP-sinə qoşulur; qoşulma uğurlu olduqda (və ya həmin IP cihazın interfeys siyahısında
         göründükdə) `10.58.100.101` IP-sinə ikinci dəfə qoşulmur (vaxt itkisinin qarşısını alır),
         lakin `.100.` IP-sini həmin cihazın məlumatlarına əlavə edir ki, əsas Core NCS
         kimi düzgün tanınsın.
  4. **Xətt Üzərində Bandwidth (`10Gbps`, `30Gbps` və s.):**
       - `Te` interfeysləri üçün `10Gbps`, `Gi` üçün `1Gbps`, `TF` üçün `25Gbps`, `Fo` üçün `40Gbps`, `Hu` üçün `100Gbps`.
       - NCS-də `BE` (`Bundle-Ether`) gördükdə `show bundle bundle-ether[id]` komandası ilə
         üzv portları (`Te0/0/0/12`, `Te0/0/0/13`, `Te0/0/0/22` və s.) tapıb sürətlərini toplayır
         (məs: 3 x 10Gbps = `30Gbps`) və 2 cihaz arasında xəttin üzərində 1 yerdə (`Nömrə | 30Gbps`) yazır.
  5. **Çıxış:** Yüksək keyfiyyətli **PNG (`l2_weathermap.png`)** və **draw.io (`l2_weathermap.drawio`)**.
"""

import argparse
import concurrent.futures
import getpass
import ipaddress
import math
import os
import platform
import queue
import re
import subprocess
import sys
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

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

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


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


def expand_subnets_and_ranges(raw_subnet_input: str) -> List[str]:
    """
    İstifadəçinin daxil etdiyi subnet(lər)i (məs: '10.58.1.0/24', '10.58.100.0/24',
    '10.58.1.0' -> avtomatik /24, və ya '10.58.1.1-10.58.1.50' aralığını) açıb
    bütün host IP ünvanlarının siyahısını qaytarır.
    Daxil edilmə sırasını qoruyur (1-ci subnetin IP-ləri əvvəl gəlir).
    """
    tokens = re.split(r"[,\s;]+", raw_subnet_input.strip())
    all_ips: List[str] = []
    seen: Set[str] = set()

    for tok in tokens:
        t = tok.strip()
        if not t:
            continue

        # 1) IP aralığı formatı: 10.58.1.1-10.58.1.254 və ya 10.58.1.1-254
        if "-" in t and "/" not in t:
            parts = t.split("-", 1)
            start_s, end_s = parts[0].strip(), parts[1].strip()
            try:
                start_ip = ipaddress.IPv4Address(start_s)
                if "." not in end_s and end_s.isdigit():
                    base_octets = start_s.split(".")[:3]
                    end_ip = ipaddress.IPv4Address(".".join(base_octets + [end_s]))
                else:
                    end_ip = ipaddress.IPv4Address(end_s)
                curr = int(start_ip)
                last = int(end_ip)
                if last >= curr and (last - curr) <= 65536:
                    for val in range(curr, last + 1):
                        ip_str = str(ipaddress.IPv4Address(val))
                        if ip_str not in seen:
                            seen.add(ip_str)
                            all_ips.append(ip_str)
                continue
            except ValueError:
                pass

        # 2) CIDR Subnet formatı: 10.58.1.0/24 (əgər '/mask' yazılmayıbsa və '.0' ilə bitirsə -> '/24')
        cidr_str = t
        if "/" not in cidr_str:
            octs = cidr_str.split(".")
            if len(octs) == 4 and octs[3] == "0":
                cidr_str = f"{cidr_str}/24"

        if "/" in cidr_str:
            try:
                net = ipaddress.IPv4Network(cidr_str, strict=False)
                hosts = list(net.hosts()) if net.num_addresses > 2 else list(net)
                for h in hosts:
                    ip_str = str(h)
                    if ip_str not in seen:
                        seen.add(ip_str)
                        all_ips.append(ip_str)
                continue
            except ValueError:
                pass

        # 3) Tək IP ünvanı
        try:
            ipaddress.IPv4Address(t)
            if t not in seen:
                seen.add(t)
                all_ips.append(t)
        except ValueError:
            pass

    return all_ips


def ping_single_ip(ip: str, timeout_ms: int = 750) -> bool:
    """
    Tək bir IP ünvanına 1 ədəd sürətli ICMP echo (ping) göndərir.
    Həm Windows, həm də Linux/macOS üzərində işləyir.
    """
    is_win = platform.system().lower().startswith("win")
    if is_win:
        cmd = ["ping", "-n", "1", "-w", str(max(200, int(timeout_ms))), ip]
    else:
        wait_sec = max(1, int(math.ceil(timeout_ms / 1000.0)))
        cmd = ["ping", "-c", "1", "-W", str(wait_sec), ip]

    creationflags = 0
    if is_win and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(2.0, timeout_ms / 1000.0 + 1.5),
            creationflags=creationflags,
        )
        out_lower = (proc.stdout or "").lower()
        if proc.returncode == 0 and ("ttl=" in out_lower or "bytes from" in out_lower):
            return True
        return False
    except Exception:
        return False


def ping_sweep_ips(
    ip_list: List[str],
    timeout_ms: int = 750,
    max_workers: int = 64,
    log_fn: Optional[Callable[[str], None]] = None,
    progress_fn: Optional[Callable[[int, int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> List[str]:
    """
    Verilmiş siyahıdakı bütün IP-ləri paralel olaraq pingləyib bitirir,
    sonra daxil edilmə sırasını qoruyaraq yalnız ping keçən (alive) IP-ləri qaytarır.
    """
    total = len(ip_list)
    if total == 0:
        return []

    if log_fn:
        log_fn(f"[*] Ping skanı başladı: cəmi {total} IP yoxlanılır (Timeout: {timeout_ms}ms, Paralel: {max_workers})...")

    alive_set: Set[str] = set()
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {
            executor.submit(ping_single_ip, ip, timeout_ms): ip
            for ip in ip_list
        }
        for future in concurrent.futures.as_completed(future_to_ip):
            if stop_event and stop_event.is_set():
                break
            ip = future_to_ip[future]
            completed += 1
            try:
                is_up = future.result()
            except Exception:
                is_up = False
            if is_up:
                alive_set.add(ip)
                if log_fn:
                    log_fn(f"  [PING OK] {ip}")
            if progress_fn:
                progress_fn(completed, total, len(alive_set))

    # Daxil edilmə sırasını qoruyaraq (məs: 1-ci subnetin IP-ləri əvvəl) qaytar
    ordered_alive = [ip for ip in ip_list if ip in alive_set]
    if log_fn:
        log_fn(f"[+] Ping skanı bitdi: {total} IP-dən {len(ordered_alive)} ədədinə ping keçdi.")
    return ordered_alive


def pair_key_1_vs_100(ip_str: str) -> Optional[Tuple[str, str, str]]:
    """
    Yalnız 3-cü okteti '1' və ya '100' olan (`10.58.1.101` və `10.58.100.101` kimi)
    eyni cihaza aid IP cütlərini tanımaq üçün `(oktet1, oktet2, oktet4)` açarını qaytarır.
    Digər fərqli alt-şəbəkələrə (məs: 10.58.2.x) toxunmur ki, bütün cihazlar tam yoxlanılsın.
    """
    parts = ip_str.strip().split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        if parts[2] in ("1", "100"):
            return (parts[0], parts[1], parts[3])
    return None


def clean_hostname(raw_name: str) -> str:
    """
    Cisco (IOS-XR, IOS-XE) və Huawei (VRP `<Hostname>` / `[~Hostname]`) promptlarından
    və ya IS-IS System ID-dən gələn cihaz adını təmizləyir.
    Çoxsətirli banner çıxışlarında yalnız sonuncu prompt sətrini götürür,
    Huawei/IS-IS `.00` / `-00` / `_RE0` sonluqlarını təmizləyir.
    """
    lines = [ln.strip() for ln in raw_name.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    name = lines[-1]
    # Əgər sətirdə `<Hostname>` və ya `[~Hostname]` varsa, birbaşa mötərizənin içini götür
    m_br = re.search(r"[<\[][~*]?([A-Za-z0-9_\-\.]+)[>\]]", name)
    if m_br:
        name = m_br.group(1)
    name = re.sub(r"^(RP/\d+/RP\d+/CPU\d+:)", "", name)
    name = name.lstrip("<[~*").rstrip("#>]\r\n ").strip()
    name = re.sub(r"^\~", "", name)
    # Əgər IP ünvanı deyilsə, yalnız IS-IS .00 pseudonode və _RE0 sonluqlarını sil
    if not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", name):
        name = re.sub(r"(?:\.00|_RE\d+)$", "", name, flags=re.IGNORECASE)
    return name


def classify_device_hw_tier(hostname: str, is_ncs_flag: bool = False) -> Tuple[str, int]:
    """
    Cihazın adından (hostname) onun şəbəkə iyerarxiyasındakı rolunu müəyyən edir:
      - Mərkəz (Core): `NE40` (məs: NE40E), `NCS` (məs: NCS55A2) -> ('core', 1)
      - Paylayıcı (Aggregation): `M14` (məs: 431M14), `M8`, `M4` -> ('m14_agg', 3)
      - Son Qovşaq (Access): `F1` (məs: 431F1A, F1) -> ('f1_access', 4)
      - Digər: ('other', 3)
    """
    h = hostname.upper()
    # Əvvəlcə M14 və F1-i yoxla ki, heç vaxt səhvən Core sayılmasın
    if re.search(r"(?:^|[^A-Z0-9])M(?:14|8|4)(?:[^A-Z0-9]|$)", h) or "M14" in h or "M8" in h:
        return ("m14_agg", 3)
    if re.search(r"(?:^|[^A-Z0-9])F1[A-Z]?(?:[^A-Z0-9]|$)", h) or "F1" in h:
        return ("f1_access", 4)
    if "NE40" in h or "NE8000-X" in h or "NCS" in h or is_ncs_flag:
        return ("core", 1)
    return ("other", 3)


def decode_isis_bcd_sysid_to_ip(sys_id: str) -> Optional[str]:
    """
    Əgər IS-IS System ID 12 rəqəmli BCD formatındadırsa (məs: '0100.5400.1089' -> 010.054.001.089),
    onu uyğun IPv4 ünvanına ('10.54.1.89') çevirir.
    """
    s = sys_id.strip()
    m = re.match(r"^(\d{4})\.(\d{4})\.(\d{4})(?:\.\d{2})?$", s)
    if not m:
        return None
    digits = m.group(1) + m.group(2) + m.group(3)
    octets = [int(digits[i:i + 3]) for i in (0, 3, 6, 9)]
    if all(0 <= o <= 255 for o in octets) and octets[0] > 0:
        return f"{octets[0]}.{octets[1]}.{octets[2]}.{octets[3]}"
    return None


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
    Cisco və Huawei uzun interfeys adlarını vahid qısa formata salır.
    Xüsusilə Huawei-də `display ospf peer brief` 'GigabitEthernet0/1/0',
    `display isis peer` isə 'GE0/1/0' yazdığı üçün hər ikisi eyniləşdirilir.
    """
    replacements = [
        ("Bundle-Ether", "BE"),
        ("Eth-Trunk", "ET"),
        ("100GigabitEthernet", "100GE"),
        ("50GigabitEthernet", "50GE"),
        ("40GigabitEthernet", "40GE"),
        ("25GigabitEthernet", "25GE"),
        ("10GigabitEthernet", "10GE"),
        ("XGigabitEthernet", "10GE"),
        ("XGE", "10GE"),
        ("TenGigabitEthernet", "Te"),
        ("TenGigE", "Te"),
        ("HundredGigE", "Hu"),
        ("GigabitEthernet", "GE"),
        ("FastEthernet", "Fa"),
        ("TwentyFiveGigE", "TF"),
        ("FortyGigE", "Fo"),
        ("Port-channel", "Po"),
    ]
    short = intf.strip()
    for old, new in replacements:
        short = re.sub(rf"^{re.escape(old)}(?=\d)", new, short, flags=re.IGNORECASE)
    return short


def canonical_interface_key(intf: str, strip_sub: bool = False) -> str:
    """
    Eyni port üzərində həm IS-IS, həm də OSPF qonşuluğunu 100% dəqiq tutuşdurmaq üçün
    interfeys adını kanonik açara çevirir:
      - 'GigabitEthernet0/1/0.100', 'GE0/1/0.100', 'Gi0/1/0.100' -> 'ge0/1/0.100'
      - 'XGigabitEthernet0/0/1', '10GE0/0/1', 'Te0/0/1'          -> '10ge0/0/1'
      - 'Eth-Trunk10.431', 'ET10.431'                            -> 'et10.431'
    `strip_sub=True` olduqda subinterfeys hissəsini ('.431') silir.
    """
    s = re.sub(r"\s+", "", intf.strip()).lower()
    if strip_sub:
        s = re.sub(r"\.(?:efp)?\d+$", "", s)
    canon_prefixes = [
        ("100gigabitethernet", "100ge"),
        ("hundredgige", "100ge"),
        ("100ge", "100ge"),
        ("hu", "100ge"),
        ("50gigabitethernet", "50ge"),
        ("fiftygige", "50ge"),
        ("50ge", "50ge"),
        ("fi", "50ge"),
        ("40gigabitethernet", "40ge"),
        ("fortygige", "40ge"),
        ("40ge", "40ge"),
        ("fo", "40ge"),
        ("25gigabitethernet", "25ge"),
        ("twentyfivegige", "25ge"),
        ("25ge", "25ge"),
        ("tf", "25ge"),
        ("10gigabitethernet", "10ge"),
        ("xgigabitethernet", "10ge"),
        ("tengigabitethernet", "10ge"),
        ("tengige", "10ge"),
        ("10ge", "10ge"),
        ("xge", "10ge"),
        ("te", "10ge"),
        ("gigabitethernet", "ge"),
        ("gi", "ge"),
        ("ge", "ge"),
        ("eth-trunk", "et"),
        ("et", "et"),
        ("bundle-ether", "be"),
        ("be", "be"),
        ("port-channel", "po"),
        ("po", "po"),
        ("vlanif", "vl"),
        ("vlan", "vl"),
        ("vl", "vl"),
        ("bdi", "vl"),
        ("bvi", "vl"),
    ]
    for old_p, new_p in canon_prefixes:
        if s.startswith(old_p) and len(s) > len(old_p) and s[len(old_p)].isdigit():
            s = new_p + s[len(old_p):]
            break
    return s


def normalize_base_interface(intf: str) -> str:
    """
    Subinterfeys nömrəsini (məs: '.4040') silərək əsas kanonik portu qaytarır.
    """
    return canonical_interface_key(intf, strip_sub=True)


def single_port_speed_gbps(port_name: str) -> float:
    """
    Tək bir fiziki portun adından (Cisco və ya Huawei) onun sürətini (Gbps ilə) müəyyən edir:
      - 100GE / Hu / HundredGigE                        -> 100 Gbps
      - 50GE / Fi / FiftyGigE                           -> 50 Gbps
      - 40GE / Fo / FortyGigE                           -> 40 Gbps
      - 25GE / TF / TwentyFiveGigE                      -> 25 Gbps
      - Te / TenGigE / 10GE / XGE / XGigabitEthernet    -> 10 Gbps
      - Gi / GE / GigabitEthernet                       -> 1 Gbps
      - Fa / FastEthernet                               -> 0.1 Gbps
    """
    s = re.sub(r"\s+", "", port_name.strip()).lower()
    if s.startswith(("100ge", "100gig", "hu", "hundredgig")):
        return 100.0
    if s.startswith(("50ge", "50gig", "fi", "fiftygig")):
        return 50.0
    if s.startswith(("40ge", "40gig", "fo", "fortygig")):
        return 40.0
    if s.startswith(("25ge", "25gig", "tf", "twentyfivegig")):
        return 25.0
    if s.startswith(("10ge", "10gig", "xge", "xgig", "te", "tengig")):
        return 10.0
    if s.startswith(("gi", "ge", "gigabit")):
        return 1.0
    if s.startswith(("fa", "fastethernet")):
        return 0.1
    return 0.0


def format_gbps(val_gbps: float) -> str:
    """
    Sürət dəyərini oxunaqlı mətnə çevirir (məs: 10.0 -> '10Gbps', 30.0 -> '30Gbps').
    """
    if val_gbps <= 0:
        return ""
    if val_gbps < 1.0:
        return f"{int(round(val_gbps * 1000))}Mbps"
    if abs(val_gbps - round(val_gbps)) < 1e-6:
        return f"{int(round(val_gbps))}Gbps"
    return f"{val_gbps:.1f}Gbps"


def extract_bundle_id(intf_name: str) -> Optional[str]:
    """
    NCS Bundle-Ether adından (məs: 'Bundle-Ether10', 'BE10.4040') bundle ID-ni ('10') çıxarır.
    """
    s = shorten_interface_name(intf_name).strip()
    m = re.match(r"^(?:BE|Bundle-Ether)\s*(\d+)(?:\.\d+)?$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def extract_eth_trunk_id(intf_name: str) -> Optional[str]:
    """
    Huawei Eth-Trunk adından (məs: 'Eth-Trunk10', 'ET10.4040') trunk ID-ni ('10') çıxarır.
    """
    s = shorten_interface_name(intf_name).strip()
    m = re.match(r"^(?:ET|Eth-Trunk)\s*(\d+)(?:\.\d+)?$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def extract_port_channel_id(intf_name: str) -> Optional[str]:
    """
    ASR Port-channel adından (məs: 'Po1', 'Port-channel2.100') ID-ni çıxarır.
    """
    s = shorten_interface_name(intf_name).strip()
    m = re.match(r"^(?:Po|Port-channel)\s*(\d+)(?:\.\d+)?$", s, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def parse_bundle_ether_bandwidth(output: str) -> Tuple[str, List[str]]:
    """
    Cisco NCS `show bundle bundle-ether[id]`, ASR `show etherchannel [id] summary` və ya
    Huawei `display eth-trunk [id]` çıxışını təhlil edir.
    Üzv portları (məs: Te0/0/0/12, 10GE0/0/1, GE0/1/0 və s.) tapır və sürətlərini toplayır.
    """
    member_pattern = re.compile(
        r"\b((?:100GE|100GigabitEthernet|50GE|40GE|40GigabitEthernet|25GE|25GigabitEthernet|"
        r"10GE|10GigabitEthernet|XGigabitEthernet|XGE|Te|TenGigE|TenGigabitEthernet|"
        r"Gi|GE|GigabitEthernet|Hu|HundredGigE|TF|TwentyFiveGigE|Fo|FortyGigE|Fi|FiftyGigE|Fa|FastEthernet)"
        r"\s*\d+(?:/\d+)+)\b",
        flags=re.IGNORECASE,
    )
    members: List[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("Port", "----", "Bundle-Ether", "Status:", "Local ", "ActorPortName")):
            continue
        for m in member_pattern.finditer(line):
            raw_p = re.sub(r"\s+", "", m.group(1))
            norm_p = shorten_interface_name(raw_p)
            if re.match(r"^[A-Za-z0-9]+\d+(?:/\d+)+$", norm_p):
                if norm_p not in members:
                    members.append(norm_p)

    total_gbps = sum(single_port_speed_gbps(p) for p in members)
    if total_gbps > 0:
        return format_gbps(total_gbps), members

    bw_m = re.search(r"Local bandwidth.*?(\d+)\s*\(\d+\)\s*kbps", output, flags=re.IGNORECASE)
    if bw_m:
        kbps = int(bw_m.group(1))
        if kbps > 0:
            return format_gbps(kbps / 1_000_000.0), members

    return "", members


def resolve_interface_bandwidth(
    intf_name: str,
    bundle_bw_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    İnterfeysin adını (məs: 'Te0/0/0/6.4040' -> '10Gbps', 'BE10' -> '30Gbps', 'ET10' -> '20Gbps')
    və ya `bundle_bw_map` cədvəlini əsas götürərək bandwidth mətnini qaytarır.
    """
    s = shorten_interface_name(intf_name).strip()
    bid = extract_bundle_id(s)
    if bid and bundle_bw_map:
        for k in (f"BE{bid}", bid):
            if k in bundle_bw_map:
                return bundle_bw_map[k]

    et_id = extract_eth_trunk_id(s)
    if et_id and bundle_bw_map:
        for k in (f"ET{et_id}", f"Eth-Trunk{et_id}", et_id):
            if k in bundle_bw_map:
                return bundle_bw_map[k]

    pid = extract_port_channel_id(s)
    if pid and bundle_bw_map:
        if f"Po{pid}" in bundle_bw_map:
            return bundle_bw_map[f"Po{pid}"]

    spd = single_port_speed_gbps(s)
    return format_gbps(spd)


def get_subnet_30_key(ip_str: str) -> str:
    """
    P2P OSPF/IS-IS linkinin hər iki tərəfini eyni link kimi tanımaq üçün /30 şəbəkə ünvanını qaytarır.
    """
    if not ip_str:
        return ""
    try:
        net = ipaddress.IPv4Network(f"{ip_str}/30", strict=False)
        return str(net)
    except ValueError:
        return ip_str


def is_clean_hostname(name: str) -> bool:
    """
    IS-IS System ID-nin oxunaqlı cihaz adı (məs: 'Ganja-NCS5508') yoxsa
    rəqəmsal NSAP/MAC formatı (məs: '0100.5400.0089' və ya IP) olduğunu yoxlayır.
    """
    s = name.strip()
    if not s:
        return False
    if re.match(r"^[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}(?:\.\d{2})?$", s):
        return False
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", s):
        return False
    return any(ch.isalpha() for ch in s)


def parse_isis_adjacency(output: str) -> List[Dict[str, str]]:
    """
    Həm Cisco IOS-XR (`show isis adjacency detail` / `show isis adjacency`),
    həm Cisco IOS-XE (`show isis neighbors detail` / `show isis neighbors`),
    həm də Huawei VRP (`display isis peer` / `display isis peer verbose`) çıxışını emal edir.
    """
    adjacencies: List[Dict[str, str]] = []
    current_adj: Optional[Dict[str, str]] = None

    intf_prefix = (
        r"(?:100GE|100GigabitEthernet|50GE|40GE|40GigabitEthernet|25GE|25GigabitEthernet|"
        r"10GE|10GigabitEthernet|XGigabitEthernet|XGE|GE|Te|TenGigE|TenGigabitEthernet|"
        r"Gi|GigabitEthernet|Hu|HundredGigE|BE|Bundle-Ether|Eth-Trunk|ET|"
        r"Fo|FortyGigE|TF|TwentyFiveGigE|Fi|FiftyGigE|Fa|FastEthernet|"
        r"Po|Port-channel|Vlanif|Vl|Vlan|BDI|BVI|LoopBack)[0-9/\.\-:]*"
    )

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith((
            "IS-IS", "System Id", "BFD", "Tag ", "Peer information", "Total Peer",
            "Tue ", "Mon ", "Wed ", "Thu ", "Fri ", "Sat ", "Sun ", "---", "===",
        )):
            continue

        # Detal sətirlərində qonşunun IP ünvanını tut (Cisco & Huawei verbose)
        ip_detail_m = re.search(
            r"(?:Neighbor\s+IPv4\s+Address|IPv4\s+Address|IP\s+Address|Interface\s+address|Peer\s+IP\s+Address)\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})",
            line,
            flags=re.IGNORECASE,
        )
        if ip_detail_m and current_adj is not None:
            if not current_adj.get("neighbor_ip"):
                current_adj["neighbor_ip"] = ip_detail_m.group(1)
            continue

        if current_adj is not None and ("Area Address" in line or "Uptime" in line or "Expires" in line):
            up_m = re.search(r"Uptime\s*:\s*([0-9a-zA-Z:]+)", line, flags=re.IGNORECASE)
            if up_m:
                current_adj["uptime"] = up_m.group(1)
            continue

        # 1) Huawei `display isis peer` formatı:
        # System Id     Interface          Circuit Id         State HoldTime Type     PRI
        # 0100.5400.1001  GE0/1/0            0100.5400.1001.01 Up   24s      L2       --
        match_hw = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            rf"({intf_prefix})\s+"
            r"(\S+)\s+"
            r"(Up|Init|Down|UP|INIT|DOWN)\s+"
            r"(\d+s?)\s+"
            r"(L1/L2|L1L2|L1/2|L1|L2)\b",
            line,
            flags=re.IGNORECASE,
        )
        if match_hw:
            sys_id, intf, _circ_id, state, hold, _isis_type = match_hw.groups()
            cleaned_id = clean_hostname(sys_id)
            decoded_ip = decode_isis_bcd_sysid_to_ip(sys_id) or ""
            current_adj = {
                "protocol": "IS-IS",
                "neighbor_id": cleaned_id,
                "neighbor_ip": decoded_ip,
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": f"Hold:{hold}",
            }
            adjacencies.append(current_adj)
            continue

        # 2) Cisco IOS-XR formatı: System_Id   Interface   SNPA (*PtoP* / xxxx.xxxx.xxxx)   State   Hold   Changed ...
        match_xr = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            rf"({intf_prefix})\s+"
            r"(\*PtoP\*|[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}|\S+)\s+"
            r"(Up|Init|Down|UP|INIT|DOWN)\s+"
            r"(\d+)\s+"
            r"([0-9a-zA-Z:]+)",
            line,
            flags=re.IGNORECASE,
        )
        if match_xr:
            sys_id, intf, _snpa, state, _hold, uptime = match_xr.groups()
            cleaned_id = clean_hostname(sys_id)
            decoded_ip = decode_isis_bcd_sysid_to_ip(sys_id) or ""
            current_adj = {
                "protocol": "IS-IS",
                "neighbor_id": cleaned_id,
                "neighbor_ip": decoded_ip,
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": uptime,
            }
            adjacencies.append(current_adj)
            continue

        # 3) Cisco IOS-XE formatı A: System_Id   Type (L1/L2/L1L2)   Interface   IP_Address   State   Holdtime ...
        match_xe_a = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            r"(L1/L2|L1L2|L1/2|L1|L2)\s+"
            rf"({intf_prefix})\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(UP|Up|INIT|Init|DOWN|Down)\s+"
            r"(\S+)",
            line,
            flags=re.IGNORECASE,
        )
        if match_xe_a:
            sys_id, _isis_type, intf, nbr_ip, state, hold = match_xe_a.groups()
            current_adj = {
                "protocol": "IS-IS",
                "neighbor_id": clean_hostname(sys_id),
                "neighbor_ip": nbr_ip,
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": f"Hold:{hold}s",
            }
            adjacencies.append(current_adj)
            continue

        # 4) Cisco IOS-XE formatı B: System_Id   Interface   ...   State   Hold   Type
        match_xe_b = re.match(
            r"^([A-Za-z0-9_\-\.]+)\s+"
            rf"({intf_prefix})\s+"
            r"(\S+)\s+"
            r"(UP|Up|INIT|Init|DOWN|Down)\s+"
            r"(\S+)\s+"
            r"(L1/L2|L1L2|L1/2|L1|L2)",
            line,
            flags=re.IGNORECASE,
        )
        if match_xe_b:
            sys_id, intf, snpa_or_ip, state, hold, _tp = match_xe_b.groups()
            nbr_ip = snpa_or_ip if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", snpa_or_ip) else (decode_isis_bcd_sysid_to_ip(sys_id) or "")
            current_adj = {
                "protocol": "IS-IS",
                "neighbor_id": clean_hostname(sys_id),
                "neighbor_ip": nbr_ip,
                "local_interface": intf,
                "state": state.capitalize(),
                "uptime": f"Hold:{hold}s",
            }
            adjacencies.append(current_adj)
            continue

    merged: List[Dict[str, str]] = []
    seen_keys: Dict[Tuple[str, str], Dict[str, str]] = {}
    for a in adjacencies:
        k = (a["neighbor_id"], shorten_interface_name(a["local_interface"]))
        if k not in seen_keys:
            seen_keys[k] = a
            merged.append(a)
        else:
            if not seen_keys[k].get("neighbor_ip") and a.get("neighbor_ip"):
                seen_keys[k]["neighbor_ip"] = a["neighbor_ip"]
    return merged


def parse_ospf_neighbors(output: str) -> List[Dict[str, str]]:
    """
    Həm Cisco IOS-XR / IOS-XE (`show ospf neighbor` / `show ip ospf neighbor`),
    həm də Huawei VRP (`display ospf peer brief`) çıxışlarını oxuyur.
    """
    neighbors: List[Dict[str, str]] = []
    current_nbr: Optional[Dict[str, str]] = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1) Cisco formatı: Neighbor_ID   Pri   State   Dead_Time   Address   Interface
        match_cisco = re.match(
            r"^(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(\d+)\s+"
            r"([A-Za-z0-9]+\s*/\s*[A-Za-z0-9\-]+|FULL|Full|2WAY|Init|ExStart|Exchange|Loading)\s+"
            r"([0-9:]+|\d+[wdhms]+)\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(\S+)",
            line,
        )
        if match_cisco:
            nbr_id, pri, state, dead_time, addr, intf = match_cisco.groups()
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
            continue

        # 2) Huawei `display ospf peer brief` formatı:
        # Area Id          Interface                        Neighbor id      State
        # 0.0.0.0          GigabitEthernet0/1/0             10.54.1.89       Full
        match_huawei = re.match(
            r"^(\d+(?:\.\d{1,3}){0,3})\s+"
            r"([A-Za-z0-9/\.\-:]+)\s+"
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+"
            r"(Full|FULL|2-Way|2Way|Init|Attempt|Down|ExStart|Exchange|Loading)\b",
            line,
            flags=re.IGNORECASE,
        )
        if match_huawei:
            _area_id, intf, nbr_id, state = match_huawei.groups()
            current_nbr = {
                "protocol": "OSPF",
                "neighbor_id": nbr_id,
                "priority": "1",
                "state": state,
                "dead_time": "-",
                "neighbor_ip": "",
                "local_interface": intf,
                "uptime": "Up",
            }
            neighbors.append(current_nbr)
            continue

        if current_nbr and "Neighbor is up for" in line:
            up_match = re.search(r"Neighbor is up for\s+(.+)$", line)
            if up_match:
                current_nbr["uptime"] = up_match.group(1).strip()

    return neighbors


def parse_local_ips(output: str) -> Set[str]:
    """
    `show ipv4 interface brief`, `show ip interface brief` və `display ip interface brief`
    çıxışından bütün lokal IP-ləri toplayır (maskaları `/24` və ya `255.255.255.0` avtomatik kənarlaşdırır).
    """
    ips: Set[str] = set()
    for line in output.splitlines():
        for ip_match in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
            if ip_match.startswith(("255.", "0.")):
                continue
            try:
                ipaddress.IPv4Address(ip_match)
                ips.add(ip_match)
            except ValueError:
                pass
    return ips


def extract_router_ids_from_output(output: str) -> Set[str]:
    """
    Huawei `display ospf peer brief` çıxışındakı `OSPF Process 1 with Router ID 10.x.x.x`
    sətrindən cihazın öz OSPF Router ID IP-sini çıxarır.
    """
    rids: Set[str] = set()
    for m in re.finditer(r"Router\s+ID\s*:?\s*(\d{1,3}(?:\.\d{1,3}){3})", output, flags=re.IGNORECASE):
        rids.add(m.group(1))
    return rids


def extract_isis_self_sysids(output: str) -> Set[str]:
    """
    Huawei `display isis brief` və ya Cisco `show isis` çıxışından cihazın öz IS-IS
    `System ID` dəyərini (məs: `0100.5400.0431`) çıxarır.
    """
    sids: Set[str] = set()
    for m in re.finditer(r"System\s+ID\s*:?\s*([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})", output, flags=re.IGNORECASE):
        raw_sid = clean_hostname(m.group(1))
        sids.add(raw_sid)
        sids.add(raw_sid.upper())
        sids.add(raw_sid.lower())
        dec = decode_isis_bcd_sysid_to_ip(raw_sid)
        if dec:
            sids.add(dec)
    return sids


# =====================================================================
# 2. SÜRƏTLİ PARALEL SSH QOŞULMA (YALNIZ VERİLƏN NCS VƏ ASR İP-LƏRİNƏ)
# =====================================================================

def normalize_credentials_list(
    username: str = "",
    password: str = "",
    fallback_credentials: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[str, str]]:
    """
    Əsas (Primary) və 2 ədəd Ehtiyat (Fallback) login/parol cütlərini ardıcıl,
    boş olmayan siyahı halına salır.
    """
    creds: List[Tuple[str, str]] = []
    if username and username.strip():
        creds.append((username.strip(), password))
    for u, p in (fallback_credentials or []):
        u_clean = (u or "").strip()
        if u_clean and (u_clean, p) not in creds:
            creds.append((u_clean, p))
    return creds


def poll_single_device(
    ip: str,
    username: str,
    password: str,
    is_ncs: Optional[bool] = None,
    timeout: int = 10,
    log_fn: Optional[Callable[[str], None]] = None,
    fallback_credentials: Optional[List[Tuple[str, str]]] = None,
) -> Optional[Dict]:
    """
    Verilmiş IP-yə (Cisco NCS, Cisco ASR və ya Huawei) qoşulur:
      - Əvvəlcə Əsas Credential-ı yoxlayır; əgər autentifikasiya xətası verərsə,
        növbə ilə 1-ci və 2-ci Fallback Credential-ları yoxlayır!
      - Huawei cihazı aşkarlandıqda (`<Hostname>` promptu və ya `huawei` tipi):
          * `display ip interface brief`
          * `display ospf peer brief`
          * `display isis peer`
          * `display eth-trunk [id]`
        komandalarını icra edir.
      - Cisco cihazı aşkarlandıqda IOS-XR / IOS-XE komandalarını icra edir.
    """
    if not NETMIKO_AVAILABLE:
        raise RuntimeError("Netmiko kitabxanası tapılmadı. Yükləmək üçün: pip install netmiko")

    creds_to_try = normalize_credentials_list(username, password, fallback_credentials)
    if not creds_to_try:
        return None

    if is_ncs is True:
        device_types = ["cisco_xr", "huawei", "cisco_xe"]
    elif is_ncs is False:
        device_types = ["huawei", "cisco_xe", "cisco_ios", "cisco_xr"]
    else:
        device_types = ["huawei", "cisco_xr", "cisco_xe", "cisco_ios"]

    for cred_idx, (curr_user, curr_pass) in enumerate(creds_to_try, start=1):
        auth_failed_for_this_cred = False

        for dev_type in device_types:
            params = {
                "device_type": dev_type,
                "host": ip,
                "username": curr_user,
                "password": curr_pass,
                "secret": curr_pass,
                "timeout": timeout,
                "conn_timeout": timeout,
                "fast_cli": False,
            }
            try:
                with ConnectHandler(**params) as conn:
                    raw_prompt = conn.find_prompt().strip()
                    is_xr_prompt = raw_prompt.startswith("RP/")
                    is_huawei_prompt = (
                        dev_type == "huawei"
                        or (raw_prompt.startswith("<") and raw_prompt.endswith(">"))
                        or (raw_prompt.startswith("[") and raw_prompt.endswith("]"))
                    )
                    hostname = clean_hostname(raw_prompt)

                    if is_huawei_prompt:
                        # Huawei VRP cihazı: səhifələməni söndür və Huawei komandalarını ver
                        try:
                            conn.send_command("screen-length 0 temporary", expect_string=r"[>#\]]", read_timeout=4)
                        except Exception:
                            pass

                        if not hostname or hostname == ip:
                            sys_out = conn.send_command("display current-configuration | include sysname")
                            s_match = re.search(r"sysname\s+(\S+)", sys_out)
                            if s_match:
                                hostname = clean_hostname(s_match.group(1))

                        ip_brief = conn.send_command("display ip interface brief")
                        ospf_out = conn.send_command("display ospf peer brief")
                        isis_out = conn.send_command("display isis peer")
                        isis_self_out = conn.send_command("display isis brief")

                        local_ips = parse_local_ips(ip_brief)
                        local_ips |= extract_router_ids_from_output(ospf_out)
                        if isis_self_out and "Error" not in isis_self_out:
                            local_ips |= extract_isis_self_sysids(isis_self_out)
                        local_ips.add(ip)
                        isis_neighbors = parse_isis_adjacency(isis_out)
                        ospf_neighbors = parse_ospf_neighbors(ospf_out)

                        # Huawei Eth-Trunk sürətlərinin toplanması
                        bundle_bw_map: Dict[str, str] = {}
                        bundle_members_map: Dict[str, List[str]] = {}
                        all_adj_intfs = [a["local_interface"] for a in isis_neighbors] + [n["local_interface"] for n in ospf_neighbors]
                        seen_et_ids: Set[str] = set()
                        for raw_intf in all_adj_intfs:
                            et_id = extract_eth_trunk_id(raw_intf)
                            if et_id and et_id not in seen_et_ids:
                                seen_et_ids.add(et_id)
                                et_out = conn.send_command(f"display eth-trunk {et_id}")
                                if et_out and "Error" not in et_out:
                                    bw_str, members = parse_bundle_ether_bandwidth(et_out)
                                    if bw_str:
                                        bundle_bw_map[f"ET{et_id}"] = bw_str
                                        bundle_members_map[f"ET{et_id}"] = members

                        # Yalnız NE40 (və ya əl ilə Core kimi verilmiş) cihazlar Core sayılır;
                        # M14 və F1 cihazları heç vaxt Core sayılmır!
                        hw_role, _hw_t = classify_device_hw_tier(hostname, is_ncs_flag=bool(is_ncs is True))
                        is_core_hw = (hw_role == "core")
                        cred_note = f" [Fallback #{cred_idx - 1}: {curr_user}]" if cred_idx > 1 else ""
                        msg = (
                            f"  [OK] {hostname} ({ip}, Huawei){cred_note} | "
                            f"IS-IS qonşu: {len(isis_neighbors)}, OSPF qonşu: {len(ospf_neighbors)}"
                        )
                        if bundle_bw_map:
                            bw_summary = ", ".join(f"{k}={v}" for k, v in bundle_bw_map.items())
                            msg += f" | Eth-Trunk BW: {bw_summary}"
                        print(msg)
                        if log_fn:
                            log_fn(msg)

                        return {
                            "hostname": hostname,
                            "mgmt_ip": ip,
                            "is_ncs": is_core_hw,
                            "is_huawei": True,
                            "device_type": "huawei",
                            "local_ips": sorted(local_ips),
                            "isis_neighbors": isis_neighbors,
                            "ospf_neighbors": ospf_neighbors,
                            "bundle_bw_map": bundle_bw_map,
                            "bundle_members_map": bundle_members_map,
                        }

                    # Cisco (IOS-XR / IOS-XE / IOS) cihazı:
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
                        ip_brief = conn.send_command("show ipv4 vrf all interface brief")
                        if "Invalid" in ip_brief or not ip_brief.strip():
                            ip_brief = conn.send_command("show ipv4 interface brief")
                        isis_out = conn.send_command("show isis adjacency detail")
                        if "Invalid" in isis_out or not isis_out.strip():
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
                        isis_out = conn.send_command("show isis neighbors detail")
                        isis_brief = conn.send_command("show isis neighbors")
                        if "Invalid" not in isis_brief and isis_brief.strip():
                            isis_out = isis_brief + "\n" + isis_out
                        if "Invalid" in isis_out or not isis_out.strip():
                            isis_out = conn.send_command("show isis adjacency detail")

                    local_ips = parse_local_ips(ip_brief)
                    local_ips.add(ip)
                    isis_neighbors = parse_isis_adjacency(isis_out)
                    ospf_neighbors = parse_ospf_neighbors(ospf_out)

                    detected_ncs = bool(is_xr_prompt or (is_ncs is True) or ("NCS" in hostname.upper()))

                    bundle_bw_map = {}
                    bundle_members_map = {}
                    all_adj_intfs = [a["local_interface"] for a in isis_neighbors] + [n["local_interface"] for n in ospf_neighbors]

                    if detected_ncs:
                        seen_bids: Set[str] = set()
                        for raw_intf in all_adj_intfs:
                            bid = extract_bundle_id(raw_intf)
                            if bid and bid not in seen_bids:
                                seen_bids.add(bid)
                                b_out = conn.send_command(f"show bundle bundle-ether{bid}")
                                if "Invalid" in b_out or not b_out.strip():
                                    b_out = conn.send_command(f"show bundle Bundle-Ether {bid}")
                                bw_str, members = parse_bundle_ether_bandwidth(b_out)
                                if bw_str:
                                    bundle_bw_map[f"BE{bid}"] = bw_str
                                    bundle_members_map[f"BE{bid}"] = members
                    else:
                        seen_pids: Set[str] = set()
                        for raw_intf in all_adj_intfs:
                            pid = extract_port_channel_id(raw_intf)
                            if pid and pid not in seen_pids:
                                seen_pids.add(pid)
                                po_out = conn.send_command(f"show etherchannel {pid} summary")
                                if po_out and "Invalid" not in po_out:
                                    bw_str, members = parse_bundle_ether_bandwidth(po_out)
                                    if bw_str:
                                        bundle_bw_map[f"Po{pid}"] = bw_str
                                        bundle_members_map[f"Po{pid}"] = members

                    actual_os = "cisco_xr" if detected_ncs else "cisco_xe"
                    cred_note = f" [Fallback #{cred_idx - 1}: {curr_user}]" if cred_idx > 1 else ""
                    msg = (
                        f"  [OK] {hostname} ({ip}, {'NCS' if detected_ncs else 'ASR'}){cred_note} | "
                        f"IS-IS qonşu: {len(isis_neighbors)}, OSPF qonşu: {len(ospf_neighbors)}"
                    )
                    if bundle_bw_map:
                        bw_summary = ", ".join(f"{k}={v}" for k, v in bundle_bw_map.items())
                        msg += f" | Bundle BW: {bw_summary}"
                    print(msg)
                    if log_fn:
                        log_fn(msg)

                    return {
                        "hostname": hostname,
                        "mgmt_ip": ip,
                        "is_ncs": detected_ncs,
                        "is_huawei": False,
                        "device_type": actual_os,
                        "local_ips": sorted(local_ips),
                        "isis_neighbors": isis_neighbors,
                        "ospf_neighbors": ospf_neighbors,
                        "bundle_bw_map": bundle_bw_map,
                        "bundle_members_map": bundle_members_map,
                    }
            except NetmikoAuthenticationException:
                auth_failed_for_this_cred = True
                break
            except (NetmikoTimeoutException, Exception):
                continue

        if auth_failed_for_this_cred and cred_idx < len(creds_to_try):
            next_u = creds_to_try[cred_idx][0]
            retry_msg = f"  [↻] {ip}: '{curr_user}' ilə autentifikasiya alınmadı, Fallback #{cred_idx} ('{next_u}') yoxlanılır..."
            print(retry_msg)
            if log_fn:
                log_fn(retry_msg)

    fail_msg = f"  [X] Cihaza qoşulmaq mümkün olmadı ({len(creds_to_try)} credential yoxlanıldı): {ip}"
    print(fail_msg)
    if log_fn:
        log_fn(fail_msg)
    return None


def collect_all_devices_parallel(
    ncs_ips: List[str],
    asr_ips: List[str],
    username: str,
    password: str,
    max_workers: int = 10,
    auto_ips: Optional[List[str]] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    progress_fn: Optional[Callable[[int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
    fallback_credentials: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[Dict[str, Dict], Dict[str, str], Set[str]]:
    """
    Ping testinin nəticəsində tapılan (və əl ilə verilən) BÜTÜN IP-lərə (Cisco və Huawei)
    Əsas + 2 Fallback Credential ilə paralel daxil olur.
    Əgər iki fərqli IP-də `hostname` EYNİ çıxarsa, onları tək cihaz kimi birləşdirir.
    """
    discovered_devices: Dict[str, Dict] = {}
    ip_to_host: Dict[str, str] = {}
    ncs_hosts: Set[str] = set()

    tasks: List[Tuple[str, Optional[bool]]] = []
    seen_input_ips: Set[str] = set()

    for ip in (auto_ips or []):
        if ip not in seen_input_ips:
            seen_input_ips.add(ip)
            tasks.append((ip, None))

    for ip in ncs_ips:
        if ip not in seen_input_ips:
            seen_input_ips.add(ip)
            tasks.append((ip, True))
    for ip in asr_ips:
        if ip not in seen_input_ips:
            seen_input_ips.add(ip)
            tasks.append((ip, False))

    creds_list = normalize_credentials_list(username, password, fallback_credentials)
    total_candidates = len(tasks)
    start_msg = (
        f"\n[*] Cəmi {total_candidates} IP-yə paralel SSH qoşulma başladı "
        f"(Aktiv Credential sayı: {len(creds_list)}, Cisco & Huawei dəstəyi)..."
    )
    print(start_msg)
    if log_fn:
        log_fn(start_msg)

    completed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                poll_single_device,
                ip,
                username,
                password,
                is_ncs,
                10,
                log_fn,
                fallback_credentials,
            ): (ip, is_ncs)
            for ip, is_ncs in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            if stop_event and stop_event.is_set():
                break
            ip, _is_ncs = future_map[future]
            completed_count += 1
            try:
                dev_info = future.result()
            except Exception as exc:
                err_m = f"  [!] Xəta ({ip}): {exc}"
                print(err_m)
                if log_fn:
                    log_fn(err_m)
                dev_info = None

            if dev_info:
                raw_h = dev_info["hostname"]
                # Böyük/kiçik hərf fərqinə görə eyni cihazın 2 dəfə düşməsinin qarşısını al ('431m14' == '431M14')
                existing_match = next((k for k in discovered_devices if k.upper() == raw_h.upper()), None)
                hostname = existing_match or raw_h
                dev_info["hostname"] = hostname

                if dev_info["is_ncs"]:
                    ncs_hosts.add(hostname)

                for lip in dev_info["local_ips"]:
                    ip_to_host[lip] = hostname

                if hostname in discovered_devices:
                    existing = discovered_devices[hostname]
                    prev_ip = existing["mgmt_ip"]
                    dup_msg = f"  [EYNİ HOSTNAME BİRLƏŞDİRİLDİ] '{hostname}': {ip} və {prev_ip} eyni cihazdır."
                    print(dup_msg)
                    if log_fn:
                        log_fn(dup_msg)

                    existing["local_ips"] = sorted(set(existing["local_ips"]) | set(dev_info["local_ips"]))
                    if has_100_in_third_octet([ip]) and not has_100_in_third_octet([prev_ip]):
                        existing["mgmt_ip"] = ip

                    existing_isis_keys = {(a["neighbor_id"], shorten_interface_name(a["local_interface"])) for a in existing["isis_neighbors"]}
                    for adj in dev_info["isis_neighbors"]:
                        k = (adj["neighbor_id"], shorten_interface_name(adj["local_interface"]))
                        if k not in existing_isis_keys:
                            existing_isis_keys.add(k)
                            existing["isis_neighbors"].append(adj)

                    existing_ospf_keys = {(n["neighbor_id"], n["neighbor_ip"], shorten_interface_name(n["local_interface"])) for n in existing["ospf_neighbors"]}
                    for nbr in dev_info["ospf_neighbors"]:
                        k = (nbr["neighbor_id"], nbr["neighbor_ip"], shorten_interface_name(nbr["local_interface"]))
                        if k not in existing_ospf_keys:
                            existing_ospf_keys.add(k)
                            existing["ospf_neighbors"].append(nbr)

                    existing.get("bundle_bw_map", {}).update(dev_info.get("bundle_bw_map", {}))
                    existing.get("bundle_members_map", {}).update(dev_info.get("bundle_members_map", {}))
                    if dev_info["is_ncs"]:
                        existing["is_ncs"] = True
                else:
                    discovered_devices[hostname] = dev_info

            if progress_fn:
                progress_fn(completed_count, total_candidates)

    return discovered_devices, ip_to_host, ncs_hosts


def resolve_neighbor_hostname(
    nbr_id: str,
    nbr_ip: str,
    ip_to_host: Dict[str, str],
    current_host: str,
) -> Optional[str]:
    """
    Qonşunun `Neighbor ID` (RID / System ID) və ya `Address` (P2P IP) dəyərinə görə
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
    Toplanmış Cisco və Huawei (`NE40 -> M14 -> F1`) məlumatlarından vahid, təkrarsız topologiya qurur:
      1. Cihazların təkrarlanmasının (`431M14` 2 dəfə çıxmasının) qarşısını alır:
         - Bütün cihaz adları böyük-kiçik hərfə və `.00` sonluqlarına qarşı eyniləşdirilir (`canonical_name`).
         - Eyni interfeys üzərində həm IS-IS (`431M14`), həm də OSPF (`10.x.x.x`) qonşusu görünürsə,
           həmin OSPF Router ID avtomatik olaraq `431M14` cihazına bağlanır!
      2. Şəbəkə İyerarxiyası (`NE40 -> M14 -> F1`):
         - **Mərkəz (Core, Tier 1-2):** `NE40` və `NCS` cihazları mərkəzdə yerləşir.
         - **Paylayıcı Qollar (Aggregation, Tier 3):** `NE40`-dan budaqlanan `M14` (və birbaşa ASR) cihazları.
         - **Son Qovşaqlar (Access, Tier 4):** `M14`-lərin üzərindən qoşulan `F1` cihazları (hər `M14`-ə 3-4 ədəd `F1`).
      3. Təkrarlanan OSPF və IS-IS konfiqurasiyalarının birləşdirilməsi:
         - İki cihaz arasında ən çoxu **1 ədəd xətt** çəkilir (təkrar subinterfeys/proseslər 2 dəfə çəkilmir).
         - Həm OSPF, həm də IS-IS varsa, xəttin yarısı Narıncı, digər yarısı Mavi (`OSPF+IS-IS`) olur.
    """
    nodes: Dict[str, Dict] = {}

    # 0. Kanonik ad və Alias xəritəsi:
    #    İki cihaz arasında HƏM OSPF, HƏM DƏ IS-IS olduqda (məs: IS-IS-də 'ATS431M14' və ya '0100.xxxx.xxxx',
    #    OSPF-də isə '10.x.x.x' çıxdıqda) onların 2 ayrı cihaz kimi göstərilməsinin qarşısını tam alır!
    upper_to_host: Dict[str, str] = {h.upper(): h for h in discovered_devices}
    alnum_to_host: Dict[str, str] = {
        re.sub(r"[^A-Z0-9]", "", h.upper()): h for h in discovered_devices if re.sub(r"[^A-Z0-9]", "", h.upper())
    }
    alias_to_host: Dict[str, str] = {}

    for h, dev in discovered_devices.items():
        alias_to_host[h] = h
        alias_to_host[h.upper()] = h
        for lip in dev.get("local_ips", []):
            ip_to_host[lip] = h
            alias_to_host[lip] = h
            alias_to_host[lip.upper()] = h

    def _register_alias(alias_str: str, canonical_host: str) -> None:
        a = clean_hostname(alias_str)
        if not a:
            return
        alias_to_host[a] = canonical_host
        alias_to_host[a.upper()] = canonical_host
        ip_to_host[a] = canonical_host
        alnum_a = re.sub(r"[^A-Z0-9]", "", a.upper())
        if alnum_a and len(alnum_a) >= 4:
            alnum_to_host[alnum_a] = canonical_host

    def resolve_canonical_host(raw_name: str, nbr_ip: str = "") -> Optional[str]:
        for candidate in (raw_name, nbr_ip):
            if not candidate:
                continue
            c = clean_hostname(candidate)
            if not c:
                continue
            if c in discovered_devices:
                return c
            if c in alias_to_host:
                return alias_to_host[c]
            if c.upper() in alias_to_host:
                return alias_to_host[c.upper()]
            if c.upper() in upper_to_host:
                return upper_to_host[c.upper()]
            if c in ip_to_host:
                return ip_to_host[c]
            dec_ip = decode_isis_bcd_sysid_to_ip(c)
            if dec_ip:
                if dec_ip in ip_to_host:
                    return ip_to_host[dec_ip]
                if dec_ip in alias_to_host:
                    return alias_to_host[dec_ip]
            alnum_c = re.sub(r"[^A-Z0-9]", "", c.upper())
            if alnum_c and len(alnum_c) >= 4 and any(ch.isalpha() for ch in alnum_c):
                if alnum_c in alnum_to_host:
                    return alnum_to_host[alnum_c]
                matches = [
                    h for k_alnum, h in alnum_to_host.items()
                    if len(k_alnum) >= 4 and (k_alnum.endswith(alnum_c) or alnum_c.endswith(k_alnum))
                ]
                if len(set(matches)) == 1:
                    return matches[0]
        return None

    # Eyni interfeys üzərində (əvvəlcə tam subinterfeys `et10.431` / `ge0/1/0.101`, sonra əsas port `et10` / `ge0/1/0`)
    # həm IS-IS, həm də OSPF qonşusu çıxıbsa, onları İKİİSTİQAMƏTLİ (Bidirectional) olaraq eyni cihaza bağla!
    # Bu addım 2 dəfə təkrarlanır ki, zəncirvari (`NE40 -> ATS431M14 -> F1`) bütün qovşaqlar tam eyniləşsin.
    for _pass in range(2):
        for host_a, dev_a in discovered_devices.items():
            for strip_flag in (False, True):
                isis_by_intf: Dict[str, List[Dict]] = defaultdict(list)
                ospf_by_intf: Dict[str, List[Dict]] = defaultdict(list)

                for adj in dev_a["isis_neighbors"]:
                    ik = canonical_interface_key(adj["local_interface"], strip_sub=strip_flag)
                    if ik:
                        isis_by_intf[ik].append(adj)

                for nbr in dev_a["ospf_neighbors"]:
                    ik = canonical_interface_key(nbr["local_interface"], strip_sub=strip_flag)
                    if ik:
                        ospf_by_intf[ik].append(nbr)

                for ik, isis_list in isis_by_intf.items():
                    ospf_list = ospf_by_intf.get(ik, [])
                    if not ospf_list:
                        continue
                    # Bu interfeysdəki unikal IS-IS və OSPF qonşularını tap
                    unique_isis_ids = list(dict.fromkeys(clean_hostname(a["neighbor_id"]) for a in isis_list if clean_hostname(a["neighbor_id"])))
                    unique_ospf_ids = list(dict.fromkeys(n["neighbor_id"].strip() for n in ospf_list if n.get("neighbor_id")))
                    if len(unique_isis_ids) != 1 or len(unique_ospf_ids) != 1:
                        continue

                    i_id = unique_isis_ids[0]
                    i_ip = next((a.get("neighbor_ip", "").strip() for a in isis_list if a.get("neighbor_ip")), "") or (decode_isis_bcd_sysid_to_ip(i_id) or "")
                    o_id = unique_ospf_ids[0]
                    o_ip = next((n.get("neighbor_ip", "").strip() for n in ospf_list if n.get("neighbor_ip")), "")

                    # Hər iki tərəfdən hansısa biri artıq məlum cihaza (`discovered_devices` və ya `alias_to_host`) bağlıdırmı?
                    known_i = resolve_canonical_host(i_id, i_ip)
                    known_o = resolve_canonical_host(o_id, o_ip)

                    # Polled (`discovered_devices`-da olan) ada üstünlük ver, yoxdursa oxunaqlı IS-IS adına üstünlük ver
                    if known_i and known_i in discovered_devices:
                        target_canonical = known_i
                    elif known_o and known_o in discovered_devices:
                        target_canonical = known_o
                    elif known_i:
                        target_canonical = known_i
                    elif known_o:
                        target_canonical = known_o
                    elif is_clean_hostname(i_id):
                        target_canonical = i_id
                    else:
                        target_canonical = o_id or i_ip or i_id

                    # Bütün identifikatorları (`i_id`, `i_ip`, `o_id`, `o_ip`) həmin VAHİD `target_canonical` adına bağla!
                    for tok in (i_id, i_ip, o_id, o_ip, f"OSPF_{o_id}"):
                        if tok:
                            _register_alias(tok, target_canonical)

                    # Əgər IS-IS qonşusunda IP boş idisə, OSPF RID/IP-ni ora yaz ki, qutu etiketində həm ad, həm IP görünsün
                    for a in isis_list:
                        if not a.get("neighbor_ip") and (o_id or o_ip):
                            a["neighbor_ip"] = o_id or o_ip

    # 1. Cihazların İyerarxik Rolunu (`NE40/NCS -> M14 -> F1`) təyin et
    core_candidates: Set[str] = set()
    for hostname, dev in discovered_devices.items():
        hw_role, _ = classify_device_hw_tier(hostname, is_ncs_flag=bool(dev.get("is_ncs")))
        if hw_role == "core":
            core_candidates.add(hostname)

    # Əgər heç bir cihazın adında NE40/NCS yoxdursa, `ncs_hosts`-a bax (M14 və F1-ləri çıxmaq şərtilə)
    if not core_candidates:
        for h in ncs_hosts:
            hw_role, _ = classify_device_hw_tier(h)
            if hw_role not in ("m14_agg", "f1_access"):
                core_candidates.add(h)

    ncs_hosts = set(core_candidates)
    total_core = len(ncs_hosts)

    for hostname, dev in discovered_devices.items():
        all_ips = [dev["mgmt_ip"]] + list(dev["local_ips"])
        hw_role, hw_tier = classify_device_hw_tier(hostname, is_ncs_flag=(hostname in ncs_hosts))

        if hostname in ncs_hosts or hw_role == "core":
            ncs_hosts.add(hostname)
            if has_100_in_third_octet(all_ips) or total_core <= 1:
                role, tier = "deg1_core", 1
            else:
                role, tier = "deg2_core", 2
        elif hw_role == "m14_agg":
            role, tier = "deg3_access", 3
        elif hw_role == "f1_access":
            role, tier = "deg3_access", 4
        else:
            role, tier = "deg3_access", 3

        nodes[hostname] = {
            "id": hostname,
            "label": f"{hostname}\n{dev['mgmt_ip']}",
            "mgmt_ip": dev["mgmt_ip"],
            "role": role,
            "tier": tier,
            "hw_role": hw_role,
            "device_type": dev["device_type"],
            "is_huawei": dev.get("is_huawei", False),
            "local_ips": dev["local_ips"],
        }

    if ncs_hosts and not any(n["role"] == "deg1_core" for n in nodes.values()):
        first_core = sorted(ncs_hosts)[0]
        nodes[first_core]["role"] = "deg1_core"
        nodes[first_core]["tier"] = 1

    def _ensure_unpolled_node(nbr_key: str, display_name: str, nbr_ip_str: str, from_host: str, is_isis_nbr: bool) -> str:
        # Əgər bu IP və ya ad artıq mövcud qovşaqlardan birinə aiddirsə, heç vaxt 2-ci təkrar qutu yaratma!
        for check_ip in (nbr_ip_str, display_name, nbr_key):
            if check_ip and check_ip in ip_to_host and ip_to_host[check_ip] in nodes:
                return ip_to_host[check_ip]
        for existing_k, existing_n in nodes.items():
            if nbr_ip_str and (nbr_ip_str == existing_n.get("mgmt_ip") or nbr_ip_str in existing_n.get("local_ips", [])):
                return existing_k
            if display_name and (display_name == existing_n.get("mgmt_ip") or display_name in existing_n.get("local_ips", [])):
                return existing_k
        # Əvvəlcə artıq mövcud olan qovşaqlarla böyük/kiçik hərf uyğunluğunu yoxla
        for existing_k in nodes:
            if existing_k.upper() == nbr_key.upper() or (display_name and existing_k.upper() == display_name.upper()):
                if nbr_ip_str and nbr_ip_str not in nodes[existing_k]["local_ips"]:
                    nodes[existing_k]["local_ips"].append(nbr_ip_str)
                    if nodes[existing_k]["mgmt_ip"] == existing_k:
                        nodes[existing_k]["mgmt_ip"] = nbr_ip_str
                        nodes[existing_k]["label"] = f"{existing_k}\n{nbr_ip_str}"
                return existing_k

        hw_r, hw_t = classify_device_hw_tier(display_name)
        from_tier = nodes.get(from_host, {}).get("tier", 3)
        from_hw = nodes.get(from_host, {}).get("hw_role", "other")

        if hw_r == "m14_agg":
            role, tier = "deg3_access", 3
        elif hw_r == "f1_access":
            role, tier = "deg3_access", 4
        elif hw_r == "core":
            role, tier = "remote_isis_core", 0
        elif from_hw == "m14_agg" or from_tier == 3:
            role, tier = "deg3_access", 4
        elif is_isis_nbr and from_host in ncs_hosts:
            role, tier = "remote_isis_core", 0
        else:
            role, tier = "deg3_access", (3 if from_host in ncs_hosts else 4)

        if is_clean_hostname(display_name) and nbr_ip_str and display_name != nbr_ip_str:
            lbl_text = f"{display_name}\n{nbr_ip_str}"
        elif nbr_ip_str:
            lbl_text = nbr_ip_str
        else:
            lbl_text = display_name

        nodes[nbr_key] = {
            "id": nbr_key,
            "label": lbl_text.strip(),
            "mgmt_ip": nbr_ip_str or display_name,
            "role": role,
            "tier": tier,
            "hw_role": hw_r,
            "device_type": "cisco_xr" if role == "remote_isis_core" else "cisco_xe",
            "local_ips": [nbr_ip_str] if nbr_ip_str else [],
        }
        _register_alias(nbr_key, nbr_key)
        if display_name:
            _register_alias(display_name, nbr_key)
        if nbr_ip_str:
            _register_alias(nbr_ip_str, nbr_key)
        return nbr_key

    # ---------------- 2. İki Cihaz Arasında TƏK (1 ƏDƏD) Bağlantının Qurulması ----------------
    # İstifadəçinin tələbi: "tekrarlanan ospf ve isis configlerini 2 dene nezere alma 1 dene goster"
    # "bir cihazin basqa bir cihaz ile hem ospf hem isis qonslugu oldugu hallar olacaq bu halda
    #  xettin yarisi mavi diger yarisi narinci rengde olsun"
    pair_records: Dict[Tuple[str, str], Dict] = {}
    directly_connected_to_core: Set[str] = set()

    def _get_or_create_pair_rec(h1: str, h2: str) -> Dict:
        pair = tuple(sorted([h1, h2]))
        if pair not in pair_records:
            pair_records[pair] = {
                "source": pair[0],
                "target": pair[1],
                "has_isis": False,
                "has_ospf": False,
                "intfs_by_host": defaultdict(list),
                "bandwidth": "",
                "p2p_ip": "",
                "state": "Up",
                "uptime": "",
            }
        return pair_records[pair]

    # 2a. Bütün IS-IS qonşuluqlarını emal et
    for host_a, dev_a in discovered_devices.items():
        bw_map_a = dev_a.get("bundle_bw_map", {})
        for adj in dev_a["isis_neighbors"]:
            raw_id = clean_hostname(adj["neighbor_id"])
            nbr_ip = adj.get("neighbor_ip", "").strip() or (decode_isis_bcd_sysid_to_ip(raw_id) or "")
            resolved = resolve_canonical_host(raw_id, nbr_ip)
            if not resolved:
                cand_key = raw_id if is_clean_hostname(raw_id) else (nbr_ip or raw_id)
                resolved = _ensure_unpolled_node(cand_key, raw_id, nbr_ip, host_a, is_isis_nbr=True)
            elif resolved not in nodes:
                disp = resolved if is_clean_hostname(resolved) else raw_id
                resolved = _ensure_unpolled_node(resolved, disp, nbr_ip, host_a, is_isis_nbr=True)

            if resolved == host_a:
                continue

            if host_a in ncs_hosts:
                directly_connected_to_core.add(resolved)
            elif resolved in ncs_hosts:
                directly_connected_to_core.add(host_a)

            rec = _get_or_create_pair_rec(host_a, resolved)
            rec["has_isis"] = True
            intf_a = shorten_interface_name(adj["local_interface"])
            if intf_a and intf_a not in rec["intfs_by_host"][host_a]:
                rec["intfs_by_host"][host_a].append(intf_a)

            bw_a = resolve_interface_bandwidth(intf_a, bw_map_a)
            if bw_a and (not rec["bandwidth"] or intf_a.upper().startswith(("BE", "ET"))):
                rec["bandwidth"] = bw_a
            if nbr_ip and not rec["p2p_ip"]:
                rec["p2p_ip"] = nbr_ip
            if adj.get("uptime") and not rec["uptime"]:
                rec["uptime"] = adj["uptime"]
            if adj.get("state"):
                rec["state"] = adj["state"]

    # 2b. Bütün OSPF qonşuluqlarını emal et
    for host_a, dev_a in discovered_devices.items():
        bw_map_a = dev_a.get("bundle_bw_map", {})
        for nbr in dev_a["ospf_neighbors"]:
            nbr_id = nbr["neighbor_id"].strip()
            nbr_ip = nbr.get("neighbor_ip", "").strip()
            resolved = resolve_canonical_host(nbr_id, nbr_ip)
            if not resolved:
                cand_key = f"OSPF_{nbr_id}"
                resolved = _ensure_unpolled_node(cand_key, nbr_id, nbr_ip or nbr_id, host_a, is_isis_nbr=False)
            elif resolved not in nodes:
                disp = resolved if is_clean_hostname(resolved) else nbr_id
                resolved = _ensure_unpolled_node(resolved, disp, nbr_ip or nbr_id, host_a, is_isis_nbr=False)
            else:
                ip_val = nbr_ip or nbr_id
                if ip_val and nodes[resolved]["mgmt_ip"] == resolved and ip_val != resolved:
                    nodes[resolved]["mgmt_ip"] = ip_val
                    nodes[resolved]["label"] = f"{resolved}\n{ip_val}"
                    if ip_val not in nodes[resolved]["local_ips"]:
                        nodes[resolved]["local_ips"].append(ip_val)

            if resolved == host_a:
                continue

            if host_a in ncs_hosts:
                directly_connected_to_core.add(resolved)
            elif resolved in ncs_hosts:
                directly_connected_to_core.add(host_a)

            rec = _get_or_create_pair_rec(host_a, resolved)
            rec["has_ospf"] = True
            intf_a = shorten_interface_name(nbr["local_interface"])
            if intf_a and intf_a not in rec["intfs_by_host"][host_a]:
                rec["intfs_by_host"][host_a].append(intf_a)

            bw_a = resolve_interface_bandwidth(intf_a, bw_map_a)
            if bw_a and (not rec["bandwidth"] or intf_a.upper().startswith(("BE", "ET"))):
                rec["bandwidth"] = bw_a
            if (nbr_ip or nbr_id) and not rec["p2p_ip"]:
                rec["p2p_ip"] = nbr_ip or nbr_id
            if nbr.get("uptime") and not rec["uptime"]:
                rec["uptime"] = nbr["uptime"]
            if nbr.get("state") and not rec["has_isis"]:
                rec["state"] = nbr["state"]

    # 2c. Hər cihaz cütü üçün TƏK bir link obyekti yarat
    links: List[Dict] = []
    for (h1, h2), rec in pair_records.items():
        if rec["has_isis"] and rec["has_ospf"]:
            proto = "OSPF+IS-IS"
        elif rec["has_isis"]:
            proto = "IS-IS"
        else:
            proto = "OSPF"

        i1_list = rec["intfs_by_host"].get(h1, [])
        i2_list = rec["intfs_by_host"].get(h2, [])
        i1_str = i1_list[0] if i1_list else ""
        i2_str = i2_list[0] if i2_list else ""

        if i1_str and i2_str and i1_str != i2_str:
            intf_label = f"{i1_str} ↔ {i2_str}"
        else:
            intf_label = i1_str or i2_str or "P2P"

        # Yuxarı səviyyəli qovşağı `source`, aşağı səviyyəlini `target` et
        if nodes.get(h1, {}).get("tier", 3) <= nodes.get(h2, {}).get("tier", 3):
            src_h, dst_h = h1, h2
        else:
            src_h, dst_h = h2, h1

        links.append({
            "source": src_h,
            "target": dst_h,
            "protocol": proto,
            "local_interface": intf_label,
            "bandwidth": rec["bandwidth"],
            "neighbor_ip": rec["p2p_ip"] or "P2P",
            "p2p_ip": rec["p2p_ip"],
            "state": rec["state"],
            "uptime": rec["uptime"] or "Up",
        })

    # ---------------- 3. NE40 -> M14 -> F1 Mərtəbə (Tier) Dəqiqləşdirməsi ----------------
    # - `M14` cihazları həmişə Tier 3 (ACCESS L1 / AGG M14) qatında qalır.
    # - `F1` cihazları (və ya `NE40`-a birbaşa qoşulmayıb `M14` üzərindən qoşulan cihazlar)
    #   həmişə Tier 4 (ACCESS L2 / F1) qatında yerləşir!
    for nid, n in nodes.items():
        hw_r = n.get("hw_role", "other")
        if hw_r == "m14_agg":
            n["tier"] = 3
        elif hw_r == "f1_access":
            n["tier"] = 4
        elif n["role"] == "deg3_access" and n["tier"] == 3:
            if nid not in directly_connected_to_core:
                connected_to_tier3 = any(
                    (l["source"] == nid and l["target"] not in ncs_hosts)
                    or (l["target"] == nid and l["source"] not in ncs_hosts)
                    for l in links
                )
                if connected_to_tier3:
                    n["tier"] = 4

    def _proto_order(p_str: str) -> int:
        if p_str == "IS-IS":
            return 0
        if p_str == "OSPF+IS-IS":
            return 1
        return 2

    links.sort(key=lambda x: (_proto_order(x["protocol"]), x["source"], x["target"], x["local_interface"]))
    for idx, link in enumerate(links, start=1):
        link["link_num"] = idx

    return nodes, links


# =====================================================================
# 4. GENİŞ QATLI (LAYERED) DÜZÜLÜŞ VƏ KƏSİŞMƏSİZ ORTOQONAL MARŞRUTLAMA
# =====================================================================

INCH_PER_UNIT = 0.30   # 1 koordinat vahidi = 0.30 düym
H_GAP = 5.6            # eyni sətirdə qutular arası GENİŞ üfüqi boşluq (xətlərin rahat keçməsi üçün)
V_GAP = 4.6            # eyni təbəqənin alt-sətirləri arası şaquli boşluq
LAYER_GAP = 6.2        # təbəqələr arası GENİŞ şaquli boşluq (ortoqonal xətlər və BW nişanları üçün)
LANE_STEP = 0.82       # paralel üfüqi xətt zolaqları (lane) arası geniş məsafə
PORT_SP = 0.68         # şaquli xətlər və portlar arası minimum məsafə (üst-üstə düşmə = 0)
MAX_COLS = 100          # bir sətirdə maksimum cihaz (geniş topologiya üçün 16 sütun)


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
    lines = label.splitlines() or [label]
    max_chars = max(len(line) for line in lines)
    return max(5.4, max_chars * 0.32 + 1.5), max(2.0, len(lines) * 0.84 + 0.75)


def measure_box_sizes(nodes: Dict[str, Dict]) -> Dict[str, Tuple[float, float]]:
    sizes: Dict[str, Tuple[float, float]] = {}
    try:
        fig = plt.figure(figsize=(4, 4), dpi=100)
        renderer = fig.canvas.get_renderer()
        for nid, n in nodes.items():
            st = node_style(n)
            fs = st["font_sz"]
            t = fig.text(0, 0, n["label"], fontsize=fs, fontweight="bold", fontfamily="DejaVu Sans")
            bb = t.get_window_extent(renderer)
            pad_in = 0.58 * fs / 72.0
            border_in = st["border_w"] / 72.0
            w_in = bb.width / fig.dpi + 2 * pad_in + border_in + 0.08
            h_in = bb.height / fig.dpi + 2 * pad_in + border_in + 0.08
            sizes[nid] = (w_in / INCH_PER_UNIT, h_in / INCH_PER_UNIT)
        plt.close(fig)
    except Exception:
        sizes = {nid: estimate_box_size(n["label"]) for nid, n in nodes.items()}
    return sizes


def widen_for_ports(nodes, links, sizes):
    """Linki çox olan cihazın qutusunu enləndirir ki, hər port arasında ən azı 0.75 vahid məsafə olsun."""
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
        need = 0.78 * max(up[nid], down[nid]) / 0.88
        out[nid] = (max(w, need), h)
    return out


def _isotonic_spaced(desired: List[float], spacing: float) -> List[float]:
    n = len(desired)
    if n == 0:
        return []
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
    """
    Təbəqəni sətirlərə düzür. Əgər `rows > 1` olarsa, tək və cüt alt-sətirləri
    üfüqi olaraq `0.5 * cell` qədər sürüşdürür (staggered) ki, alt sətirdəki qutu
    üst sətirdəki qutu ilə heç vaxt eyni X şaquli xəttinə düşməsin!
    """
    if rows <= 1:
        xs_list = _isotonic_spaced([desired[v] for v in order], cell)
        return {v: xs_list[i] for i, v in enumerate(order)}, {v: 0 for v in order}

    xs: Dict[str, float] = {}
    sub: Dict[str, int] = {}
    for r in range(rows):
        row_members = [order[i] for i in range(r, len(order), rows)]
        if not row_members:
            continue
        row_des = [desired[v] for v in row_members]
        row_x = _isotonic_spaced(row_des, cell)
        stagger_offset = (r % 2) * (0.48 * cell)
        for idx, v in enumerate(row_members):
            xs[v] = row_x[idx] + stagger_offset
            sub[v] = r
    return xs, sub


def _ip_group(n: Dict, nid: str) -> str:
    parts = (n.get("mgmt_ip") or "").strip().split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:2])
    return "?" + nid


def _layer_value(n: Dict) -> int:
    t = int(n.get("tier", 3))
    if t == 0:
        return 0
    if t in (1, 2):
        return 1
    return t - 1          # tier3 -> 2 (ACCESS L1), tier4 -> 3 (ACCESS L2), tier5 -> 4 (ACCESS L3)...


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
    if sizes is None:
        sizes = {nid: estimate_box_size(n["label"]) for nid, n in nodes.items()}

    layer_val = {nid: _layer_value(n) for nid, n in nodes.items()}
    keys = sorted(set(layer_val.values()))
    layers: List[List[str]] = [
        sorted([nid for nid in nodes if layer_val[nid] == k], key=lambda i: (nodes[i].get("tier", 3), i))
        for k in keys
    ]
    layer_idx = {nid: i for i, lay in enumerate(layers) for nid in lay}
    fixed = {i for i, k in enumerate(keys) if k == 1}

    nbrs: Dict[str, Set[str]] = defaultdict(set)
    for l in links:
        s, t = l["source"], l["target"]
        if s != t and s in nodes and t in nodes:
            nbrs[s].add(t)
            nbrs[t].add(s)

    total = len(nodes)
    max_cols = max(MAX_COLS, total)
    rows = [max(1, math.ceil(len(lay) / max_cols)) for lay in layers]
    cells = [max(sizes[v][0] for v in lay) + H_GAP for lay in layers]

    grp = {nid: _ip_group(n, nid) for nid, n in nodes.items()}

    def order_layer(lay: List[str], des: Dict[str, float]) -> List[str]:
        # Hər bir F1 / alt cihazı birbaşa öz valideyn M14 / Core cihazının X koordinatına (`des[v]`)
        # görə sırala ki, hər M14-ün 3-4 F1 qolu tam öz altında yan-yana düzülsün!
        return sorted(lay, key=lambda v: (round(des[v], 2), grp[v], xs.get(v, 0.0), v))

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

    for i in range(len(layers)):
        if i in fixed:
            continue
        des = {v: desired_for(v, "down") for v in layers[i]}
        order = order_layer(layers[i], des)
        arrange(i, order, des)

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
    Kəsişməsiz və üst-üstə düşməsiz Ortoqonal Marşrutlama:
      - Hər qutunun alt və üst kənarındakı çıxış/giriş portları (`xa`, `xb`) qonşuların
        üfüqi mövqeyinə görə ciddi sıralanır və heç vaxt pozulmur!
      - Şaquli xətlər arasında ən azı `PORT_SP = 0.68` məsafə qorunur.
      - Kanal daxilində üfüqi xətlər (`lanes`) bir-birindən `LANE_STEP = 0.82` məsafə ilə
        ayrı zolaqlara düşür -> iki xətt heç vaxt üst-üstə düşmür!
    """
    nrows = len(rows)
    used: Dict[int, List[float]] = defaultdict(list)
    row_boxes = [[(xs[v], sizes[v][0]) for v in r["nodes"]] for r in rows]

    def alloc(chs, desired, lo, hi, sp=PORT_SP, avoid_rows=(), span=120.0):
        def ok(x):
            if x < lo or x > hi:
                return False
            for c in chs:
                for u in used[c]:
                    if abs(u - x) < sp:
                        return False
            for r in avoid_rows:
                for bx, bw in row_boxes[r]:
                    if abs(x - bx) < bw / 2.0 + 0.55:
                        return False
            return True
        steps = int(min(span, max(hi - lo, 1.0)) / 0.12) + 2
        for k in range(steps):
            for sgn in ((1, -1) if k else (1,)):
                x = desired + sgn * k * 0.12
                if ok(x):
                    for c in chs:
                        used[c].append(x)
                    return x
        return None

    def alloc_safe(chs, desired, lo, hi, **kw):
        for sp in (PORT_SP, 0.45, 0.30, 0.18):
            x = alloc(chs, desired, lo, hi, sp=sp, **kw)
            if x is not None:
                return x
        # Əgər yenə tapılmasa, mövcud `used` nöqtələrindən ən uzaq nöqtəni seç
        best_x = min(max(desired, lo), hi)
        for c in chs:
            used[c].append(best_x)
        return best_x

    # --- 1. Kənarların təsnifatı ---
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

    # --- 2. Qutu kənarında port mövqeləri (qonşunun X-inə görə sıralı və ayrı!) ---
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
        step = min(1.25, max(0.70, 0.88 * sizes[nid][0] / max(1, n_it)))
        if step * (n_it - 1) > 0.90 * sizes[nid][0] and n_it > 1:
            step = (0.90 * sizes[nid][0]) / (n_it - 1)
        for i, (li, end, _o) in enumerate(items):
            desired[(li, end)] = xs[nid] + (i - (n_it - 1) / 2.0) * step

    # --- 3. Port və şaquli koridor ayrılması ---
    for e in sorted(E, key=lambda e: (e["rb"] - e["ra"], abs(xs[e["a"]] - xs[e["b"]]), e["num"])):
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
            # Yalnız 1-ə-1 tam şaquli uyğun gəldikdə və digər portlarla toqquşmadıqda düz xətt çək
            if lo <= hi and abs(da - db) <= 0.45:
                xm = alloc([ch], min(max((da + db) / 2.0, lo), hi), lo, hi, sp=PORT_SP)
            if xm is not None:
                e["xa"] = e["xb"] = xm
            else:
                e["xa"] = alloc_safe([ch], da, a_lo, a_hi)
                e["xb"] = alloc_safe([ch], db, b_lo, b_hi)
                e["lanes"].append((ch, e["xa"], e["xb"], "A"))
        else:  # long (aradakı sətirlərin yanından keçən xətt)
            chs = list(range(ra, rb))
            mid_rows = list(range(ra + 1, rb))
            preferred_xc = (da + db) / 2.0
            xc = alloc(chs, preferred_xc, -1e9, 1e9, sp=PORT_SP, avoid_rows=mid_rows, span=500.0)
            if xc is None:
                xc = alloc_safe(chs, db, -1e9, 1e9, avoid_rows=mid_rows, span=500.0)
            e["xc"] = xc
            # DİQQƏT: `xa` və `xb` HƏMİŞƏ qutunun öz sıralanmış `da`/`db` portundan çıxır!
            e["xa"] = alloc_safe([ra], da, a_lo, a_hi)
            if abs(e["xa"] - xc) > 0.02:
                e["lanes"].append((ra, e["xa"], xc, "L1"))
            e["xb"] = alloc_safe([rb - 1], db, b_lo, b_hi)
            if abs(e["xb"] - xc) > 0.02:
                e["lanes"].append((rb - 1, xc, e["xb"], "L2"))

    # --- 4. Üfüqi zolaqların (lane) paylanması: hər kəsişən/yaxın xətt FƏRQLİ Y zolağına düşür ---
    chan_items: Dict[int, List[Tuple[float, float, Tuple[int, str]]]] = defaultdict(list)
    for e in E:
        for ch, x1, x2, tag in e["lanes"]:
            if abs(x1 - x2) > 0.01:
                chan_items[ch].append((min(x1, x2), max(x1, x2), (e["li"], tag)))

    lane_idx: Dict[Tuple[int, str], int] = {}
    nlanes: Dict[int, int] = {}
    for ch, items in chan_items.items():
        # Uzun üfüqi seqmentləri əvvəl yerləşdir ki, qısa seqmentlərlə kəsişmə az olsun
        items.sort(key=lambda it: (it[0], -(it[1] - it[0])))
        ends: List[float] = []
        for x1, x2, key in items:
            for j, en in enumerate(ends):
                # 1.6 vahid üfüqi təhlükəsizlik буferi (heç vaxt yaxın üfüqi xətlər eyni zolağı paylaşmır)
                if en + 1.6 <= x1:
                    ends[j] = x2
                    lane_idx[key] = j
                    break
            else:
                ends.append(x2)
                lane_idx[key] = len(ends) - 1
        nlanes[ch] = len(ends)

    # --- 5. Şaquli yerləşdirmə (kanal hündürlüyü zolaq sayına görə avtomatik böyüyür) ---
    chan_h: Dict[int, float] = {}
    for c in range(-1, nrows - 1):
        nl = nlanes.get(c, 0)
        base = 0.0 if c == -1 else (LAYER_GAP if rows[c]["layer"] != rows[c + 1]["layer"] else V_GAP)
        chan_h[c] = max(base, (2.4 + (nl - 1) * LANE_STEP) if nl else 0.0)

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
        nl = max(1, nlanes.get(c, 1))
        ch_height = chan_h[c]
        # Zolaqları kanalın mərkəzində bərabər payla
        total_span = (nl - 1) * LANE_STEP
        top_start = chan_top(c) - (ch_height - total_span) / 2.0
        return top_start - lane_idx[key] * LANE_STEP

    # --- 6. Çoxbucaqlıları (polyline) qur ---
    pair_groups: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for e in E:
        if e["kind"] == "side":
            pair_groups[(e["a"], e["b"])].append(e)
    side_y: Dict[int, float] = {}
    for (a, _b), grp in pair_groups.items():
        grp.sort(key=lambda e: e["num"])
        m = len(grp)
        step = min(0.65, 0.78 * sizes[a][1] / max(1, m))
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
                if abs(e["xa"] - xc) > 0.02:
                    l1 = lane_y(ra, (li, "L1"))
                    pts += [(e["xa"], l1), (xc, l1)]
                if abs(e["xb"] - xc) > 0.02:
                    l2 = lane_y(rb - 1, (li, "L2"))
                    pts += [(xc, l2), (e["xb"], l2)]
            pts.append((e["xb"], yb))

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
        bw_val = link.get("bandwidth", "")
        edge_label = f'{link["link_num"]} | {bw_val}' if bw_val else str(link["link_num"])
        tip = f'{link["protocol"]} {a} <-> {b} | {link["local_interface"]} | {bw_val} | {link["state"]} {link.get("uptime", "")}'
        cells.append(
            f'<mxCell id="e{li}" value={quoteattr(edge_label)} tooltip={quoteattr(tip)} edge="1" parent="1" '
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
        bw_part = f' | BW: {link["bandwidth"]}' if link.get("bandwidth") else ""
        lines.append(escape(
            f'{link["link_num"]}. [{link["protocol"]}] {src} <-> {dst} | '
            f'{shorten_interface_name(link["local_interface"])}{bw_part} | {link.get("p2p_ip", "")} | '
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
    dpi = max(90, min(280, int(min(28000.0 / max(1.0, fig_w), 28000.0 / max(1.0, fig_h), (6.5e7 / max(1.0, fig_w * fig_h)) ** 0.5))))

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
    def rounded_path(pts, r=0.28):
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

    def split_polyline_in_half(pts_list: List[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """
        Ortoqonal xətti (polyline) həndəsi uzunluğuna görə tam ortadan (50% / 50%) iki hissəyə bölür:
        1-ci yarısı Narıncı (IS-IS), 2-ci yarısı Mavi (OSPF) rəngdə çəkilməsi üçün.
        """
        if len(pts_list) < 2:
            return pts_list, pts_list
        seg_lens = [
            math.hypot(pts_list[i + 1][0] - pts_list[i][0], pts_list[i + 1][1] - pts_list[i][1])
            for i in range(len(pts_list) - 1)
        ]
        total_len = sum(seg_lens)
        if total_len <= 1e-6:
            return pts_list, pts_list
        half_len = total_len / 2.0
        acc = 0.0
        for i, slen in enumerate(seg_lens):
            if acc + slen >= half_len:
                rem = half_len - acc
                t = 0.5 if slen <= 1e-9 else (rem / slen)
                mx = pts_list[i][0] + t * (pts_list[i + 1][0] - pts_list[i][0])
                my = pts_list[i][1] + t * (pts_list[i + 1][1] - pts_list[i][1])
                mid_pt = (mx, my)
                first_half = pts_list[:i + 1] + [mid_pt]
                second_half = [mid_pt] + pts_list[i + 1:]
                return first_half, second_half
            acc += slen
        return pts_list, pts_list

    for li, pts in paths.items():
        link = links[li]
        proto = link["protocol"]
        lstyle = "solid" if link["state"].upper().startswith(("UP", "FULL")) else "dashed"

        if proto == "OSPF+IS-IS":
            # Xəttin yarısı Narıncı (IS-IS), digər yarısı Mavi (OSPF)!
            half1, half2 = split_polyline_in_half(pts)
            ax.add_patch(PathPatch(
                rounded_path(half1),
                facecolor="none",
                edgecolor="#FF5722",
                linewidth=2.8,
                linestyle=lstyle,
                alpha=0.96,
                zorder=2,
                capstyle="butt",
                joinstyle="round",
            ))
            ax.add_patch(PathPatch(
                rounded_path(half2),
                facecolor="none",
                edgecolor="#00B0FF",
                linewidth=2.8,
                linestyle=lstyle,
                alpha=0.96,
                zorder=2,
                capstyle="butt",
                joinstyle="round",
            ))
        else:
            is_isis = (proto == "IS-IS")
            ax.add_patch(PathPatch(
                rounded_path(pts),
                facecolor="none",
                edgecolor="#FF5722" if is_isis else "#00B0FF",
                linewidth=2.8 if is_isis else 2.3,
                linestyle=lstyle,
                alpha=0.95,
                zorder=2,
                capstyle="round",
                joinstyle="round",
            ))

    # ---------------- Xətt üzərində Nömrə + Bandwidth nişanları (2 cihaz arasında 1 yerdə) ----------------
    placed_badges: List[Tuple[float, float]] = []
    boxes = [(pos[n][0], pos[n][1], sizes[n][0], sizes[n][1]) for n in pos]

    def hits_box(cx: float, cy: float) -> bool:
        return any(abs(cx - bx) < bw / 2 + 1.05 and abs(cy - by) < bh / 2 + 0.65 for bx, by, bw, bh in boxes)

    def hits_badge(cx: float, cy: float) -> bool:
        return any(abs(cx - px) < 2.25 and abs(cy - py) < 0.95 for px, py in placed_badges)

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
            if math.hypot(q[0] - p[0], q[1] - p[1]) < 0.9:
                continue
            for f in (0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78):
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
            for cx, cy in cands:
                if not hits_box(cx, cy):
                    best = (cx, cy)
                    break
        if best is None:
            best = cands[0]
        placed_badges.append(best)
        proto = links[li]["protocol"]
        is_isis = (proto == "IS-IS")
        is_dual = (proto == "OSPF+IS-IS")
        lnum_str = str(links[li]["link_num"])
        bw_str = links[li].get("bandwidth", "")
        badge_text = f"{lnum_str} | {bw_str}" if bw_str else lnum_str
        b_style = "round,pad=0.26,rounding_size=0.45" if bw_str else "circle,pad=0.32"
        b_face = "#38BDF8" if (is_dual or not is_isis) else "#FF7043"
        b_edge = "#FF5722" if is_dual else "#FFFFFF"
        b_lw = 2.2 if is_dual else 1.25
        ax.text(
            best[0], best[1], badge_text,
            fontsize=8.6 if bw_str else 9.5, fontfamily="DejaVu Sans", fontweight="bold", color="#0B1120",
            ha="center", va="center", zorder=4,
            bbox=dict(boxstyle=b_style, facecolor=b_face, edgecolor=b_edge, linewidth=b_lw),
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
    dual_count = sum(1 for l in links if l["protocol"] == "OSPF+IS-IS")
    dual_part = f", OSPF+IS-IS: {dual_count}" if dual_count else ""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    ax.set_title(
        "L2/L3 ROUTING WEATHER MAP (CISCO & HUAWEI)\n"
        f"Cihaz sayı: {len(nodes)}  |  Ümumi Bağlantı (Link): {len(links)} "
        f"(IS-IS: {isis_count}, OSPF: {ospf_count}{dual_part})  |  Tarix: {now_str}",
        fontsize=14, fontweight="bold", color="#F8FAFC", pad=18,
    )
    legend_elements = [
        Line2D([0], [0], color="#FF5722", lw=3.5, label="IS-IS Bağlantısı (Narıncı)"),
        Line2D([0], [0], color="#00B0FF", lw=2.8, label="OSPF Bağlantısı (Mavi)"),
        Line2D([0], [0], color="#00B0FF", marker="o", markerfacecolor="#FF5722", markeredgecolor="#FFFFFF",
               markersize=8, lw=3.0, label="OSPF + IS-IS (Yarı Narıncı / Yarı Mavi)"),
        Line2D([0], [0], marker="s", color="#0B1120", label="Əsas Core (.100.)",
               markerfacecolor="#1E1B4B", markeredgecolor="#FACC15", markersize=11),
        Line2D([0], [0], marker="s", color="#0B1120", label="Mərkəz Core",
               markerfacecolor="#2E1065", markeredgecolor="#C084FC", markersize=11),
        Line2D([0], [0], marker="s", color="#0B1120", label="Access (Cisco ASR / Huawei)",
               markerfacecolor="#0F172A", markeredgecolor="#00B0FF", markersize=11),
    ]
    ax.legend(
        handles=legend_elements, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=6,
        frameon=True, facecolor="#1E293B", edgecolor="#475569", fontsize=9.0, labelcolor="#F8FAFC",
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
        proto = link["protocol"]
        is_isis = (proto == "IS-IS")
        is_dual = (proto == "OSPF+IS-IS")
        badge_bg = "#FF7043" if is_isis else "#38BDF8"
        badge_edge = "#FF5722" if is_dual else "#FFFFFF"
        proto_color = "#FACC15" if is_dual else ("#FF8A65" if is_isis else "#7DD3FC")

        src_name = link["source"].replace("OSPF_", "")
        dst_name = link["target"].replace("OSPF_", "")
        intf_str = shorten_interface_name(link["local_interface"])
        p2p_ip = link.get("p2p_ip", "")
        uptime = link.get("uptime", "")

        bw_str = link.get("bandwidth", "")
        bw_part = f"   |   BW: {bw_str}" if bw_str else ""
        if is_isis:
            detail_line_1 = f"[{link['protocol']}]  {src_name}  ↔  {dst_name}"
            detail_line_2 = f"Port: {intf_str}{bw_part}   |   State: {link['state']} ({uptime})"
        else:
            ip_part = f"IP: {p2p_ip}   |   " if p2p_ip else ""
            detail_line_1 = f"[{link['protocol']}]  {src_name}  ↔  {dst_name}"
            detail_line_2 = f"Port: {intf_str}{bw_part}   |   {ip_part}Uptime: {uptime}"

        ax_list.text(
            x_base + 0.065, y_center, f"{lnum}", fontsize=9.0, fontweight="bold", color="#0B1120",
            ha="center", va="center", fontfamily="DejaVu Sans",
            bbox=dict(boxstyle="circle,pad=0.28", facecolor=badge_bg, edgecolor=badge_edge, linewidth=1.8 if is_dual else 1.0),
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
# 5. DEMO REJİMİ (TE = 10Gbps VƏ BUNDLE-ETHER = 30Gbps / 20Gbps NÜMUNƏSİ)
# =====================================================================

def run_demo_mode(output_dir: Path) -> None:
    """
    Test məqsədilə Huawei & Cisco `NE40 -> M14 -> F1` arxitekturasını simulyasiya edir:
      - Mərkəzdə 2 əsas `NE40` cihazı (`Baku-Core-NE40-1`, `Baku-Core-NE40-2`)
      - Onlardan budaqlanan `431M14` və `432M14` paylayıcı cihazları (həm OSPF, həm IS-IS olduqda
        təkrarlanmayan tək yarı-mavi / yarı-narıncı xətt!)
      - Hər `M14` üzərindən qoşulan 3 ədəd `F1` cihazı (`431F1-1..3`, `432F1-1..3`)
    """
    discovered_devices = {
        "Baku-Core-NE40-1": {
            "hostname": "Baku-Core-NE40-1",
            "mgmt_ip": "10.54.100.1",
            "is_ncs": True,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.100.1", "10.54.0.1", "10.54.1.1"],
            "bundle_bw_map": {"ET10": "30Gbps", "ET20": "20Gbps"},
            "isis_neighbors": [
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "Baku-Core-NE40-2",
                    "neighbor_ip": "10.54.0.2",
                    "local_interface": "Eth-Trunk10",
                    "state": "Up",
                    "uptime": "24w1d",
                },
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "431M14",
                    "neighbor_ip": "10.54.1.14",
                    "local_interface": "Eth-Trunk20.4040",
                    "state": "Up",
                    "uptime": "12w3d",
                },
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "432M14",
                    "neighbor_ip": "10.54.1.15",
                    "local_interface": "10GE0/1/0",
                    "state": "Up",
                    "uptime": "11w5d",
                },
            ],
            "ospf_neighbors": [
                {
                    # 431M14 ilə həm IS-IS, həm OSPF var -> 2 xətt deyil, 1 yarı-narıncı/yarı-mavi xətt olacaq!
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.14",
                    "neighbor_ip": "",
                    "local_interface": "Eth-Trunk20.4040",
                    "state": "Full",
                    "uptime": "12w3d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.15",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/1/0",
                    "state": "Full",
                    "uptime": "11w5d",
                },
            ],
        },
        "Baku-Core-NE40-2": {
            "hostname": "Baku-Core-NE40-2",
            "mgmt_ip": "10.54.200.2",
            "is_ncs": True,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.200.2", "10.54.0.2"],
            "bundle_bw_map": {"ET10": "30Gbps"},
            "isis_neighbors": [
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "Baku-Core-NE40-1",
                    "neighbor_ip": "10.54.0.1",
                    "local_interface": "Eth-Trunk10",
                    "state": "Up",
                    "uptime": "24w1d",
                },
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "432M14",
                    "neighbor_ip": "10.54.1.15",
                    "local_interface": "10GE0/1/1",
                    "state": "Up",
                    "uptime": "10w2d",
                },
            ],
            "ospf_neighbors": [],
        },
        "431M14": {
            "hostname": "431M14",
            "mgmt_ip": "10.54.1.14",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.14", "10.54.100.14"],
            "bundle_bw_map": {"ET20": "20Gbps"},
            "isis_neighbors": [
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "Baku-Core-NE40-1",
                    "neighbor_ip": "10.54.100.1",
                    "local_interface": "Eth-Trunk20.4040",
                    "state": "Up",
                    "uptime": "12w3d",
                },
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "431F1-1",
                    "neighbor_ip": "10.54.1.101",
                    "local_interface": "10GE0/0/1",
                    "state": "Up",
                    "uptime": "6w1d",
                },
            ],
            "ospf_neighbors": [
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.100.1",
                    "neighbor_ip": "",
                    "local_interface": "Eth-Trunk20.4040",
                    "state": "Full",
                    "uptime": "12w3d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.101",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/0/1",
                    "state": "Full",
                    "uptime": "6w1d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.102",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/0/2",
                    "state": "Full",
                    "uptime": "5w4d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.103",
                    "neighbor_ip": "",
                    "local_interface": "GE0/0/3",
                    "state": "Full",
                    "uptime": "4w2d",
                },
            ],
        },
        "432M14": {
            "hostname": "432M14",
            "mgmt_ip": "10.54.1.15",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.15", "10.54.100.15"],
            "bundle_bw_map": {},
            "isis_neighbors": [
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "Baku-Core-NE40-1",
                    "neighbor_ip": "10.54.100.1",
                    "local_interface": "10GE0/0/1",
                    "state": "Up",
                    "uptime": "11w5d",
                },
                {
                    "protocol": "IS-IS",
                    "neighbor_id": "Baku-Core-NE40-2",
                    "neighbor_ip": "10.54.200.2",
                    "local_interface": "10GE0/0/2",
                    "state": "Up",
                    "uptime": "10w2d",
                },
            ],
            "ospf_neighbors": [
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.100.1",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/0/1",
                    "state": "Full",
                    "uptime": "11w5d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.201",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/0/5",
                    "state": "Full",
                    "uptime": "7w1d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.202",
                    "neighbor_ip": "",
                    "local_interface": "10GE0/0/6",
                    "state": "Full",
                    "uptime": "7w1d",
                },
                {
                    "protocol": "OSPF",
                    "neighbor_id": "10.54.1.203",
                    "neighbor_ip": "",
                    "local_interface": "GE0/0/7",
                    "state": "Full",
                    "uptime": "3w5d",
                },
            ],
        },
        "431F1-1": {
            "hostname": "431F1-1",
            "mgmt_ip": "10.54.1.101",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.101"],
            "bundle_bw_map": {},
            "isis_neighbors": [
                {"protocol": "IS-IS", "neighbor_id": "431M14", "neighbor_ip": "10.54.1.14", "local_interface": "10GE0/0/1", "state": "Up", "uptime": "6w1d"}
            ],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.14", "neighbor_ip": "", "local_interface": "10GE0/0/1", "state": "Full", "uptime": "6w1d"}
            ],
        },
        "431F1-2": {
            "hostname": "431F1-2",
            "mgmt_ip": "10.54.1.102",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.102"],
            "bundle_bw_map": {},
            "isis_neighbors": [],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.14", "neighbor_ip": "", "local_interface": "10GE0/0/1", "state": "Full", "uptime": "5w4d"}
            ],
        },
        "431F1-3": {
            "hostname": "431F1-3",
            "mgmt_ip": "10.54.1.103",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.103"],
            "bundle_bw_map": {},
            "isis_neighbors": [],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.14", "neighbor_ip": "", "local_interface": "GE0/0/1", "state": "Full", "uptime": "4w2d"}
            ],
        },
        "432F1-1": {
            "hostname": "432F1-1",
            "mgmt_ip": "10.54.1.201",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.201"],
            "bundle_bw_map": {},
            "isis_neighbors": [],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.15", "neighbor_ip": "", "local_interface": "10GE0/0/1", "state": "Full", "uptime": "7w1d"}
            ],
        },
        "432F1-2": {
            "hostname": "432F1-2",
            "mgmt_ip": "10.54.1.202",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.202"],
            "bundle_bw_map": {},
            "isis_neighbors": [],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.15", "neighbor_ip": "", "local_interface": "10GE0/0/1", "state": "Full", "uptime": "7w1d"}
            ],
        },
        "432F1-3": {
            "hostname": "432F1-3",
            "mgmt_ip": "10.54.1.203",
            "is_ncs": False,
            "is_huawei": True,
            "device_type": "huawei",
            "local_ips": ["10.54.1.203"],
            "bundle_bw_map": {},
            "isis_neighbors": [],
            "ospf_neighbors": [
                {"protocol": "OSPF", "neighbor_id": "10.54.1.15", "neighbor_ip": "", "local_interface": "GE0/0/1", "state": "Full", "uptime": "3w5d"}
            ],
        },
    }

    ip_to_host = {}
    for h, d in discovered_devices.items():
        for lip in d["local_ips"]:
            ip_to_host[lip] = h
    ncs_hosts = {"Baku-Core-NE40-1", "Baku-Core-NE40-2"}

    nodes, links = build_unified_topology(discovered_devices, ip_to_host, ncs_hosts)
    generate_image_weathermap(nodes, links, output_dir / "l2_weathermap.png")


# =====================================================================
# 6. DESKTOP APPLICATION (TKINTER GUI) VƏ TERMINAL (CLI) REJİMİ
# =====================================================================

class L3TopologyDesktopApp:
    """
    Aztelekom L2/L3 ISIS & OSPF Weather Map + Bandwidth Desktop Tətbiqi.
    - Subnet daxiletməsi -> Bütün subnetin sürətli Ping skanı
    - Eyni cihazdakı `1.101` və `100.101` IP-lərinin avtomatik təkrarsızlaşdırılması
    - NCS `Bundle-Ether` (`show bundle bundle-ether[id]`) və `Te` (`10Gbps`) sürətlərinin xətt üzərində yazılması
    - Canlı loq, proqres bar və proqram daxilində PNG topologiya önizləməsi
    """

    def __init__(self, root: "tk.Tk") -> None:
        self.root = root
        self.root.title("L3 Topology With Draw.io And PNG with BW — Aztelekom Desktop App")
        self.root.geometry("1360x840")
        self.root.minsize(1100, 700)
        self.root.configure(bg="#0B1120")

        self.log_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.last_png_path: Optional[Path] = None
        self.last_out_dir: Optional[Path] = None
        self._preview_photo = None

        self._build_styles()
        self._build_ui()
        self.root.after(100, self._poll_queue)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background="#0B1120")
        style.configure("Card.TFrame", background="#111B33")
        style.configure(
            "TLabel",
            background="#111B33",
            foreground="#E2E8F0",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Header.TLabel",
            background="#0B1120",
            foreground="#F8FAFC",
            font=("Segoe UI", 14, "bold"),
        )
        style.configure(
            "SubHeader.TLabel",
            background="#0B1120",
            foreground="#94A3B8",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Section.TLabel",
            background="#111B33",
            foreground="#FACC15",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TNotebook",
            background="#0B1120",
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background="#1E293B",
            foreground="#CBD5E1",
            padding=(14, 7),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#0284C7")],
            foreground=[("selected", "#FFFFFF")],
        )
        style.configure(
            "Green.Horizontal.TProgressbar",
            troughcolor="#1E293B",
            background="#10B981",
            bordercolor="#1E293B",
            lightcolor="#10B981",
            darkcolor="#10B981",
        )

    def _build_ui(self) -> None:
        # Üst Başlıq Paneli
        top_bar = ttk.Frame(self.root)
        top_bar.pack(fill=tk.X, padx=16, pady=(12, 6))

        title_lbl = ttk.Label(
            top_bar,
            text="AZTELEKOM L3 TOPOLOGY WITH DRAW.IO & PNG (WITH BANDWIDTH)",
            style="Header.TLabel",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ttk.Label(
            top_bar,
            text="Subnet Ping Skaneri  •  1.x / 100.x Eyni Cihaz Filtrləməsi  •  IS-IS & OSPF  •  Bundle-Ether / Te Sürət Hesablayıcısı",
            style="SubHeader.TLabel",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Əsas Gövdə (Sol: Parametrlər, Sağ: Loq & Şəkil Önizləməsi)
        main_pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg="#1E293B", sashwidth=5, bd=0)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        left_frame = ttk.Frame(main_pane, style="Card.TFrame")
        right_frame = ttk.Frame(main_pane)
        main_pane.add(left_frame, minsize=430, width=460)
        main_pane.add(right_frame, minsize=600)

        self._build_left_controls(left_frame)
        self._build_right_panel(right_frame)

    def _build_left_controls(self, parent: "ttk.Frame") -> None:
        pad_frame = ttk.Frame(parent, style="Card.TFrame")
        pad_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # 1. Çıxış Qovluğu
        ttk.Label(pad_frame, text="1) Çıxış Qovluğu (Nəticələrin saxlanacağı yer):", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        folder_row = ttk.Frame(pad_frame, style="Card.TFrame")
        folder_row.pack(fill=tk.X, pady=(0, 10))

        default_folder = f"Topology_{datetime.now().strftime('%Y%m%d_%H%M')}"
        self.folder_var = tk.StringVar(value=default_folder)
        folder_entry = tk.Entry(
            folder_row,
            textvariable=self.folder_var,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
        )
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 6))

        browse_btn = tk.Button(
            folder_row,
            text="Qovluq Seç...",
            command=self._choose_folder,
            bg="#334155",
            fg="#F8FAFC",
            activebackground="#475569",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        browse_btn.pack(side=tk.RIGHT)

        # 2. Subnet Daxil Edin (Avtomatik Ping üçün)
        ttk.Label(
            pad_frame,
            text="2) Subnet(lər) Daxil Edin (Əvvəlcə bütün IP-ləri Pingləyir):",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(2, 2))
        ttk.Label(
            pad_frame,
            text="Məs: 10.58.1.0/24, 10.58.100.0/24 (1.101 qoşulubsa 100.101 avtomatik ötürülür)",
            foreground="#94A3B8",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 4))

        self.subnet_text = tk.Text(
            pad_frame,
            height=3,
            bg="#0F172A",
            fg="#38BDF8",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.subnet_text.pack(fill=tk.X, pady=(0, 8))

        # 3. Əlavə (Əl ilə) Core NCS və Access ASR IP-ləri
        ttk.Label(
            pad_frame,
            text="3) Əlavə Mərkəz (Core NCS) IP-ləri (İstəyə bağlı):",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(2, 3))
        self.ncs_text = tk.Text(
            pad_frame,
            height=2,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.ncs_text.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            pad_frame,
            text="4) Əlavə Access (ASR 920 / 901) IP-ləri (İstəyə bağlı):",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(2, 3))
        self.asr_text = tk.Text(
            pad_frame,
            height=3,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.asr_text.pack(fill=tk.X, pady=(0, 10))

        # 4. SSH Məlumatları (Əsas + 2 Fallback Credential)
        cred_header_row = ttk.Frame(pad_frame, style="Card.TFrame")
        cred_header_row.pack(fill=tk.X, pady=(2, 3))
        ttk.Label(
            cred_header_row,
            text="5) SSH Credentials (Əsas + 2 Fallback — Cisco & Huawei):",
            style="Section.TLabel",
        ).pack(side=tk.LEFT)

        self.show_pass_var = tk.BooleanVar(value=False)
        show_cb = tk.Checkbutton(
            cred_header_row,
            text="Parolları Göstər",
            variable=self.show_pass_var,
            command=self._toggle_password,
            bg="#111B33",
            fg="#CBD5E1",
            selectcolor="#0F172A",
            activebackground="#111B33",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 8),
        )
        show_cb.pack(side=tk.RIGHT)

        cred_grid = ttk.Frame(pad_frame, style="Card.TFrame")
        cred_grid.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(cred_grid, text="Roll", foreground="#94A3B8", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(cred_grid, text="Username", foreground="#94A3B8", font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ttk.Label(cred_grid, text="Password", foreground="#94A3B8", font=("Segoe UI", 8, "bold")).grid(row=0, column=2, sticky="w")

        # 1) Əsas Credential
        ttk.Label(cred_grid, text="1) Əsas:").grid(row=1, column=0, sticky="w", pady=3, padx=(0, 6))
        self.user_var = tk.StringVar()
        tk.Entry(
            cred_grid, textvariable=self.user_var, bg="#0F172A", fg="#F8FAFC",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        ).grid(row=1, column=1, sticky="ew", ipady=3, pady=3, padx=(0, 6))
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(
            cred_grid, textvariable=self.pass_var, show="*", bg="#0F172A", fg="#F8FAFC",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        )
        self.pass_entry.grid(row=1, column=2, sticky="ew", ipady=3, pady=3)

        # 2) Fallback 1 Credential
        ttk.Label(cred_grid, text="2) Fallback 1:").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 6))
        self.fb1_user_var = tk.StringVar()
        tk.Entry(
            cred_grid, textvariable=self.fb1_user_var, bg="#0F172A", fg="#38BDF8",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        ).grid(row=2, column=1, sticky="ew", ipady=3, pady=3, padx=(0, 6))
        self.fb1_pass_var = tk.StringVar()
        self.fb1_pass_entry = tk.Entry(
            cred_grid, textvariable=self.fb1_pass_var, show="*", bg="#0F172A", fg="#38BDF8",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        )
        self.fb1_pass_entry.grid(row=2, column=2, sticky="ew", ipady=3, pady=3)

        # 3) Fallback 2 Credential
        ttk.Label(cred_grid, text="3) Fallback 2:").grid(row=3, column=0, sticky="w", pady=3, padx=(0, 6))
        self.fb2_user_var = tk.StringVar()
        tk.Entry(
            cred_grid, textvariable=self.fb2_user_var, bg="#0F172A", fg="#C084FC",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        ).grid(row=3, column=1, sticky="ew", ipady=3, pady=3, padx=(0, 6))
        self.fb2_pass_var = tk.StringVar()
        self.fb2_pass_entry = tk.Entry(
            cred_grid, textvariable=self.fb2_pass_var, show="*", bg="#0F172A", fg="#C084FC",
            insertbackground="#F8FAFC", relief=tk.FLAT, font=("Consolas", 9), width=14,
        )
        self.fb2_pass_entry.grid(row=3, column=2, sticky="ew", ipady=3, pady=3)

        cred_grid.columnconfigure(1, weight=1)
        cred_grid.columnconfigure(2, weight=1)

        # 5. Skan Parametrləri (Ping Timeout & Workers)
        opt_grid = ttk.Frame(pad_frame, style="Card.TFrame")
        opt_grid.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(opt_grid, text="Ping Timeout (ms):").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.ping_timeout_var = tk.StringVar(value="700")
        tk.Entry(
            opt_grid,
            textvariable=self.ping_timeout_var,
            width=8,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
        ).grid(row=0, column=1, sticky="w", ipady=3, padx=(0, 16))

        ttk.Label(opt_grid, text="SSH Paralel Sayı:").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.workers_var = tk.StringVar(value="12")
        tk.Entry(
            opt_grid,
            textvariable=self.workers_var,
            width=6,
            bg="#0F172A",
            fg="#F8FAFC",
            insertbackground="#F8FAFC",
            relief=tk.FLAT,
            font=("Consolas", 10),
        ).grid(row=0, column=3, sticky="w", ipady=3)

        # 6. İdarəetmə Düymələri
        btn_row1 = ttk.Frame(pad_frame, style="Card.TFrame")
        btn_row1.pack(fill=tk.X, pady=(4, 6))

        self.start_btn = tk.Button(
            btn_row1,
            text="▶  BAŞLAT (Ping + SSH + Topologiya)",
            command=self._on_start_discovery,
            bg="#10B981",
            fg="#0B1120",
            activebackground="#059669",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            pady=8,
            cursor="hand2",
        )
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.stop_btn = tk.Button(
            btn_row1,
            text="■ Dayandır",
            command=self._on_stop,
            state=tk.DISABLED,
            bg="#EF4444",
            fg="#FFFFFF",
            activebackground="#DC2626",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=8,
            cursor="hand2",
        )
        self.stop_btn.pack(side=tk.RIGHT)

        btn_row2 = ttk.Frame(pad_frame, style="Card.TFrame")
        btn_row2.pack(fill=tk.X, pady=(2, 4))

        self.ping_only_btn = tk.Button(
            btn_row2,
            text="🔍 Yalnız Subnet Ping Yoxla",
            command=self._on_ping_only,
            bg="#0284C7",
            fg="#FFFFFF",
            activebackground="#0369A1",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            pady=6,
            cursor="hand2",
        )
        self.ping_only_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.demo_btn = tk.Button(
            btn_row2,
            text="★ Demo Topologiya Çək",
            command=self._on_run_demo,
            bg="#6366F1",
            fg="#FFFFFF",
            activebackground="#4F46E5",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            pady=6,
            cursor="hand2",
        )
        self.demo_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        btn_row3 = ttk.Frame(pad_frame, style="Card.TFrame")
        btn_row3.pack(fill=tk.X, pady=(4, 0))

        tk.Button(
            btn_row3,
            text="📂 Çıxış Qovluğunu Aç",
            command=self._open_output_folder,
            bg="#1E293B",
            fg="#E2E8F0",
            activebackground="#334155",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            pady=5,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        tk.Button(
            btn_row3,
            text="🖼 Şəkli Tam Ekran Aç",
            command=self._open_image_external,
            bg="#1E293B",
            fg="#E2E8F0",
            activebackground="#334155",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            pady=5,
            cursor="hand2",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

    def _build_right_panel(self, parent: "ttk.Frame") -> None:
        # Proqres və Status sətri
        status_frame = ttk.Frame(parent, style="Card.TFrame")
        status_frame.pack(fill=tk.X, pady=(0, 8))

        self.status_var = tk.StringVar(value="Hazırdır. Subnet və ya IP-ləri daxil edib 'BAŞLAT' düyməsini sıxın.")
        status_lbl = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 10, "bold"),
            foreground="#38BDF8",
        )
        status_lbl.pack(anchor="w", padx=10, pady=(8, 4))

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(
            status_frame,
            variable=self.progress_var,
            maximum=100.0,
            style="Green.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Notebook (Tab 1: Canlı Loq, Tab 2: Topologiya Şəkli)
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        log_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        preview_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(log_tab, text="  📋 Canlı Sistem Loqu (Ping & SSH)  ")
        self.notebook.add(preview_tab, text="  🗺 Topologiya Şəkli (Önizləmə)  ")

        self.log_box = scrolledtext.ScrolledText(
            log_tab,
            bg="#090E1A",
            fg="#E2E8F0",
            insertbackground="#F8FAFC",
            font=("Consolas", 10),
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.log_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._append_log("=== Aztelekom L3 Topology With Draw.io & PNG (with BW) Desktop App ===")
        self._append_log("• Subnet daxil etdikdə əvvəlcə bütün IP-lər pinglənir, yalnız cavab verənlərə SSH qoşulur.")
        self._append_log("• Eyni cihazda həm 10.x.1.101, həm də 10.x.100.101 varsa, 1.101-ə qoşulduqdan sonra 100.101 avtomatik ötürülür.\n")

        self.preview_canvas = tk.Canvas(preview_tab, bg="#0B1120", highlightthickness=0)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.preview_canvas.bind("<Configure>", lambda _e: self._redraw_preview())

    def _toggle_password(self) -> None:
        ch = "" if self.show_pass_var.get() else "*"
        self.pass_entry.configure(show=ch)
        self.fb1_pass_entry.configure(show=ch)
        self.fb2_pass_entry.configure(show=ch)

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Çıxış Qovluğunu Seçin")
        if chosen:
            self.folder_var.set(chosen)

    def _append_log(self, text: str) -> None:
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def _emit_log(self, text: str) -> None:
        self.log_queue.put(("log", text))

    def _emit_progress(self, pct: float, status_text: str) -> None:
        self.log_queue.put(("progress", (pct, status_text)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    pct, st = payload
                    self.progress_var.set(float(pct))
                    self.status_var.set(str(st))
                elif kind == "done":
                    png_p = payload
                    self.start_btn.configure(state=tk.NORMAL)
                    self.ping_only_btn.configure(state=tk.NORMAL)
                    self.demo_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    if png_p and Path(png_p).exists():
                        self.last_png_path = Path(png_p)
                        self._load_preview_image(self.last_png_path)
                        self.notebook.select(1)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _load_preview_image(self, png_path: Path) -> None:
        self.last_png_path = png_path
        self._redraw_preview()

    def _redraw_preview(self) -> None:
        if not self.last_png_path or not self.last_png_path.exists():
            return
        self.preview_canvas.delete("all")
        cw = max(200, self.preview_canvas.winfo_width())
        ch = max(200, self.preview_canvas.winfo_height())
        if PIL_AVAILABLE:
            try:
                img = Image.open(self.last_png_path)
                iw, ih = img.size
                scale = min(cw / iw, ch / ih) * 0.96
                nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
                resized = img.resize((nw, nh), Image.LANCZOS)
                self._preview_photo = ImageTk.PhotoImage(resized)
                self.preview_canvas.create_image(cw // 2, ch // 2, image=self._preview_photo, anchor=tk.CENTER)
                return
            except Exception:
                pass
        try:
            self._preview_photo = tk.PhotoImage(file=str(self.last_png_path))
            self.preview_canvas.create_image(cw // 2, ch // 2, image=self._preview_photo, anchor=tk.CENTER)
        except Exception as exc:
            self.preview_canvas.create_text(
                cw // 2,
                ch // 2,
                text=f"Şəkil hazırdır: {self.last_png_path}\n({exc})",
                fill="#F8FAFC",
                font=("Segoe UI", 11),
            )

    def _set_running_state(self) -> None:
        self.stop_event.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.ping_only_btn.configure(state=tk.DISABLED)
        self.demo_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress_var.set(0.0)

    def _on_stop(self) -> None:
        self.stop_event.set()
        self._emit_log("[!] İstifadəçi tərəfindən dayandırma sorğusu göndərildi...")
        self.status_var.set("Dayandırılır...")

    def _on_ping_only(self) -> None:
        raw_sub = self.subnet_text.get("1.0", tk.END).strip()
        if not raw_sub:
            messagebox.showwarning("Xəbərdarlıq", "Zəhmət olmasa yoxlamaq üçün ən azı 1 Subnet daxil edin (məs: 10.58.1.0/24).")
            return
        self._set_running_state()
        self.notebook.select(0)

        def _worker() -> None:
            try:
                ips = expand_subnets_and_ranges(raw_sub)
                if not ips:
                    self._emit_log("[!] Düzgün Subnet və ya IP formatı tapılmadı.")
                    self.log_queue.put(("done", None))
                    return
                t_ms = int(self.ping_timeout_var.get().strip() or "700")
                alive = ping_sweep_ips(
                    ips,
                    timeout_ms=t_ms,
                    max_workers=64,
                    log_fn=self._emit_log,
                    progress_fn=lambda done, tot, up: self._emit_progress(
                        (done / max(1, tot)) * 100.0,
                        f"Ping yoxlanılır: {done}/{tot} (Aktiv: {up})",
                    ),
                    stop_event=self.stop_event,
                )
                self._emit_log(f"\n[✓] Ping keçən aktiv IP-lər ({len(alive)} ədəd): {', '.join(alive)}")
                self._emit_progress(100.0, f"Ping tamamlandı: {len(alive)} aktiv IP tapıldı.")
            except Exception as exc:
                self._emit_log(f"[!] Xəta: {exc}")
            finally:
                self.log_queue.put(("done", None))

        self.worker_thread = threading.Thread(target=_worker, daemon=True)
        self.worker_thread.start()

    def _on_run_demo(self) -> None:
        folder = self.folder_var.get().strip() or f"weathermap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir = Path(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.last_out_dir = out_dir
        self._set_running_state()

        def _worker() -> None:
            try:
                self._emit_log(f"\n[*] Demo topologiya hazırlanır -> Qovluq: {out_dir.resolve()}")
                self._emit_progress(35.0, "Demo topologiya qurulur...")
                run_demo_mode(out_dir)
                png_p = out_dir / "l2_weathermap.png"
                self._emit_log(f"[✓] Demo PNG və draw.io faylları hazırlandı: {png_p.resolve()}")
                self._emit_progress(100.0, f"Demo tamamlandı: {png_p.name}")
                self.log_queue.put(("done", str(png_p)))
            except Exception as exc:
                self._emit_log(f"[!] Demo xətası: {exc}")
                self.log_queue.put(("done", None))

        self.worker_thread = threading.Thread(target=_worker, daemon=True)
        self.worker_thread.start()

    def _on_start_discovery(self) -> None:
        raw_sub = self.subnet_text.get("1.0", tk.END).strip()
        raw_ncs = self.ncs_text.get("1.0", tk.END).strip()
        raw_asr = self.asr_text.get("1.0", tk.END).strip()
        username = self.user_var.get().strip()
        password = self.pass_var.get()

        if not raw_sub and not raw_ncs and not raw_asr:
            messagebox.showwarning(
                "Məlumat çatışmır",
                "Zəhmət olmasa ya Subnet (məs: 10.58.1.0/24) ya da Cihaz IP-lərini daxil edin.",
            )
            return
        if not username:
            messagebox.showwarning("Məlumat çatışmır", "Zəhmət olmasa SSH Username daxil edin.")
            return

        folder = self.folder_var.get().strip() or f"weathermap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        out_dir = Path(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.last_out_dir = out_dir

        try:
            t_ms = int(self.ping_timeout_var.get().strip() or "700")
        except ValueError:
            t_ms = 700
        try:
            workers = int(self.workers_var.get().strip() or "10")
        except ValueError:
            workers = 10

        self._set_running_state()
        self.notebook.select(0)

        def _worker() -> None:
            try:
                self._emit_log(f"\n================ YENİ TOPOLOGİYA KƏŞFİYYATI ================")
                self._emit_log(f"[+] Çıxış qovluğu: {out_dir.resolve()}")

                alive_subnet_ips: List[str] = []
                if raw_sub:
                    subnet_ips = expand_subnets_and_ranges(raw_sub)
                    if subnet_ips:
                        alive_subnet_ips = ping_sweep_ips(
                            subnet_ips,
                            timeout_ms=t_ms,
                            max_workers=64,
                            log_fn=self._emit_log,
                            progress_fn=lambda done, tot, up: self._emit_progress(
                                (done / max(1, tot)) * 45.0,
                                f"Addım 1/3 — Ping yoxlanılır: {done}/{tot} (Aktiv: {up})",
                            ),
                            stop_event=self.stop_event,
                        )
                    else:
                        self._emit_log("[!] Subnet formatı oxuna bilmədi.")

                if self.stop_event.is_set():
                    self.log_queue.put(("done", None))
                    return

                ncs_ips = parse_ip_list(raw_ncs) if raw_ncs else []
                asr_ips = parse_ip_list(raw_asr) if raw_asr else []

                if not alive_subnet_ips and not ncs_ips and not asr_ips:
                    self._emit_log("[!] Ping keçən və ya əl ilə verilmiş heç bir aktiv IP tapılmadı.")
                    self._emit_progress(0.0, "Aktiv IP tapılmadı.")
                    self.log_queue.put(("done", None))
                    return

                fallback_creds = [
                    (self.fb1_user_var.get().strip(), self.fb1_pass_var.get()),
                    (self.fb2_user_var.get().strip(), self.fb2_pass_var.get()),
                ]
                discovered_devices, ip_to_host, ncs_hosts = collect_all_devices_parallel(
                    ncs_ips=ncs_ips,
                    asr_ips=asr_ips,
                    username=username,
                    password=password,
                    max_workers=workers,
                    auto_ips=alive_subnet_ips,
                    log_fn=self._emit_log,
                    progress_fn=lambda done, tot: self._emit_progress(
                        45.0 + (done / max(1, tot)) * 40.0,
                        f"Addım 2/3 — SSH Kəşfiyyatı: {done}/{tot} IP yoxlanıldı",
                    ),
                    stop_event=self.stop_event,
                    fallback_credentials=fallback_creds,
                )

                if self.stop_event.is_set() or not discovered_devices:
                    self._emit_log("[!] Heç bir cihazdan SSH məlumatı toplana bilmədi.")
                    self._emit_progress(0.0, "SSH məlumatı toplanmadı.")
                    self.log_queue.put(("done", None))
                    return

                self._emit_progress(90.0, "Addım 3/3 — Topologiya və Bandwidth xəritəsi çəkilir...")
                nodes, links = build_unified_topology(discovered_devices, ip_to_host, ncs_hosts)
                png_path = out_dir / "l2_weathermap.png"
                generate_image_weathermap(nodes, links, png_path)

                self._emit_log(f"\n[✓] TAMAMLANDI! PNG Şəkil: {png_path.resolve()}")
                self._emit_log(f"[✓] Draw.io Faylı: {png_path.with_suffix('.drawio').resolve()}")
                self._emit_progress(100.0, f"Tamamlandı! Cihaz: {len(nodes)}, Bağlantı: {len(links)}")
                self.log_queue.put(("done", str(png_path)))
            except Exception as exc:
                self._emit_log(f"[!] Xəta baş verdi: {exc}")
                self.log_queue.put(("done", None))

        self.worker_thread = threading.Thread(target=_worker, daemon=True)
        self.worker_thread.start()

    def _open_output_folder(self) -> None:
        target = self.last_out_dir or Path(self.folder_var.get().strip() or ".")
        target.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system().lower().startswith("win"):
                os.startfile(str(target.resolve()))
            elif platform.system().lower() == "darwin":
                subprocess.Popen(["open", str(target.resolve())])
            else:
                subprocess.Popen(["xdg-open", str(target.resolve())])
        except Exception as exc:
            messagebox.showinfo("Qovluq Yolu", f"Qovluq: {target.resolve()}\n({exc})")

    def _open_image_external(self) -> None:
        if not self.last_png_path or not self.last_png_path.exists():
            messagebox.showinfo("Məlumat", "Hələ topologiya şəkli yaradılmayıb.")
            return
        try:
            if platform.system().lower().startswith("win"):
                os.startfile(str(self.last_png_path.resolve()))
            elif platform.system().lower() == "darwin":
                subprocess.Popen(["open", str(self.last_png_path.resolve())])
            else:
                subprocess.Popen(["xdg-open", str(self.last_png_path.resolve())])
        except Exception as exc:
            messagebox.showinfo("Şəkil Yolu", f"Şəkil: {self.last_png_path.resolve()}\n({exc})")


def run_cli_mode(args: argparse.Namespace) -> None:
    """
    Terminal (CLI) rejimində Subnet Ping + Eyni Cihaz Filtrləməsi + SSH + Topologiya.
    """
    print("=== Aztelekom L3 Topology With Draw.io And PNG with BW (CLI Rejimi) ===")

    folder_name = args.output_dir.strip()
    if not folder_name:
        folder_name = input("1) Nəticələrin saxlanacağı qovluğun adını daxil edin (məs: Region_Topologiya): ").strip()
    if not folder_name:
        folder_name = f"weathermap_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    out_dir = Path(folder_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"   [+] Qovluq hazırlandı: {out_dir.resolve()}")

    # 2. Subnet daxiletməsi (Ping skanı üçün)
    raw_subnets = " ".join(args.subnets) if args.subnets else ""
    if not raw_subnets and not args.ncs_ips and not args.asr_ips:
        raw_subnets = input(
            "2) Pinglənəcək Subnet(lər)i daxil edin (məs: 10.58.1.0/24, 10.58.100.0/24 — ötürmək üçün Enter): "
        ).strip()

    alive_subnet_ips: List[str] = []
    if raw_subnets:
        expanded_ips = expand_subnets_and_ranges(raw_subnets)
        if expanded_ips:
            alive_subnet_ips = ping_sweep_ips(expanded_ips, timeout_ms=700, max_workers=64)

    # 3. Əlavə Core NCS və Access ASR IP-ləri
    ncs_ips = args.ncs_ips or []
    if not ncs_ips:
        prompt_ncs = (
            "3) Əlavə Mərkəz (Core NCS) IP-lərini daxil edin (Subnet kifayətdirsə boş buraxıb Enter sıxın): "
            if alive_subnet_ips
            else "3) Mərkəz (Core NCS) cihazların IP ünvanlarını daxil edin (məs: x.x.x.x, x.x.x.y): "
        )
        raw_ncs = input(prompt_ncs).strip()
        ncs_ips = parse_ip_list(raw_ncs)

    asr_ips = args.asr_ips or []
    if not asr_ips:
        raw_asr = input("4) Əlavə Access (ASR 920/901) IP-lərini daxil edin (boş buraxmaq üçün Enter): ").strip()
        asr_ips = parse_ip_list(raw_asr)

    if not alive_subnet_ips and not ncs_ips and not asr_ips:
        print("[!] Heç bir aktiv IP tapılmadı və daxil edilmədi. Çıxılır.")
        sys.exit(1)

    username = input("5) Əsas SSH Login (Username 1): ").strip()
    password = getpass.getpass("6) Əsas SSH Parol (Password 1): ")

    fallback_creds: List[Tuple[str, str]] = []
    fb1_u = input("7) Fallback 1 SSH Username (boş buraxmaq üçün Enter): ").strip()
    if fb1_u:
        fb1_p = getpass.getpass("   Fallback 1 SSH Password: ")
        fallback_creds.append((fb1_u, fb1_p))
        fb2_u = input("8) Fallback 2 SSH Username (boş buraxmaq üçün Enter): ").strip()
        if fb2_u:
            fb2_p = getpass.getpass("   Fallback 2 SSH Password: ")
            fallback_creds.append((fb2_u, fb2_p))

    discovered_devices, ip_to_host, ncs_hosts = collect_all_devices_parallel(
        ncs_ips=ncs_ips,
        asr_ips=asr_ips,
        username=username,
        password=password,
        max_workers=args.workers,
        auto_ips=alive_subnet_ips,
        fallback_credentials=fallback_creds,
    )

    if not discovered_devices:
        print("[!] Heç bir cihazdan məlumat toplana bilmədi.")
        sys.exit(1)

    nodes, links = build_unified_topology(discovered_devices, ip_to_host, ncs_hosts)

    png_path = out_dir / "l2_weathermap.png"
    generate_image_weathermap(nodes, links, png_path)
    print(f"\n[✓] PNG Weather Map şəkli hazırlandı: '{png_path.resolve()}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 Topology With Draw Io And Png with BW (Desktop GUI & CLI)")
    parser.add_argument("--demo", action="store_true", help="Nümunə məlumatlarla test PNG şəkli yarat")
    parser.add_argument("--cli", action="store_true", help="Desktop GUI əvəzinə Terminal (CLI) rejimində işlət")
    parser.add_argument("--subnets", nargs="*", help="Avtomatik Ping skanı üçün subnet(lər) (məs: 10.58.1.0/24)")
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

    # Əgər `--cli` verilibsə və ya komanda sətrindən konkret arqumentlər ötürülübsə, yaxud ekran (DISPLAY) yoxdursa:
    has_cli_args = bool(args.cli or args.subnets or args.ncs_ips or args.asr_ips)
    if not has_cli_args and TK_AVAILABLE:
        try:
            root = tk.Tk()
            _app = L3TopologyDesktopApp(root)
            root.mainloop()
            return
        except Exception as exc:
            print(f"[!] Desktop GUI pəncərəsi açıla bilmədi ({exc}), Terminal (CLI) rejiminə keçilir...")

    run_cli_mode(args)


if __name__ == "__main__":
    main()