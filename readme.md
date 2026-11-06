# L3 Topology Weather Map (with Bandwidth) 🌐

[](https://www.python.org/downloads/)
[](https://opensource.org/licenses/MIT)
[](#)
[](#)

Telekommunikasiya və IP/MPLS aqreqasiya/core şəbəkələri (Cisco NCS 5500/55A2, ASR 920, ASR 901) üçün **IS-IS** və **OSPF** qonşuluqlarını avtomatik aşkarlayan, xətlər üzərində fiziki və `Bundle-Ether` sürətlərini (Bandwidth) toplayıb göstərən, həm **Desktop GUI**, həm də **CLI** rejimində işləyən avtomatlaşdırma aləti.

Nəticədə yüksək keyfiyyətli **PNG Weather Map** və diagrams.net-də tam redaktə oluna bilən **draw.io (`.drawio`)** faylı generasiya olunur.

---

## 🚀 Əsas Xüsusiyyətlər

1. **İkili İş Rejimi (Desktop GUI & CLI):**
   * **Müasir Desktop Pəncərəsi (Tkinter):** Qaranlıq mövzulu (Dark Theme) qrafik interfeys, canlı ping/SSH loq konsolu, proqres bar və daxili topologiya önizləməsi.
   * **Terminal (CLI) Rejimi:** `--cli` bayrağı ilə serverlərdə və ya skript daxilində tam avtomatlaşdırılmış işləmə imkanı.

2. **Ağıllı Subnet Ping Skaneri (`Subnet -> Ping -> SSH`):**
   * Girişdə bir və ya bir neçə subnet (məs: `10.54.1.0/24, 10.54.100.0/24`) daxil edildikdə, bütün IP-lərə sürətli paralel ICMP ping göndərilir. Yalnız cavab verən (aktiv) cihazlara SSH qoşulması həyata keçirilir.

3. **Hostname Əsaslı Təkrarsızlaşdırma (Deduplication):**
   * Eyni fiziki cihazda həm `10.54.1.101`, həm də `10.54.100.101` kimi fərqli interfeys IP-ləri olduqda, sistem daxil olduqdan sonra `hostname`-lərin eyni olduğunu müəyyən edir və təkrar sorğu göndərmədən onları tək cihaz kimi birləşdirir. Əgər hostname-lər fərqlidirsə, hər ikisi müstəqil cihaz kimi saxlanılır.

4. **Xətt Üzərində Bandwidth Hesablanması (Tək Nişanda):**
   * **Fiziki interfeyslər:** `Te` (`TenGigE`) $\rightarrow$ **10Gbps**, `Gi` $\rightarrow$ **1Gbps**, `TF` $\rightarrow$ **25Gbps**, `Fo` $\rightarrow$ **40Gbps**, `Hu` $\rightarrow$ **100Gbps**.
   * **NCS Bundle-Ether (`BE`):** `show bundle bundle-ether[id]` komandası ilə aqreqasiya olunmuş bütün üzv portları tapır və sürətlərini toplayır (məs: 3 ədəd `Te` port $\rightarrow$ **30Gbps**).
   * Bağlantı xətti üzərində iki cihaz arasında tək bir nişan daxilində nömrə və sürət göstərilir (məs: `2 | 30Gbps`).

5. **Qarşı IS-IS Qonşularının Aşkarlanması:**
   * Şəbəkədə olan, lakin IP-si daxil edilməyən kənar IS-IS qonşularının P2P IP ünvanını `detail` çıxışından çıxarır və adı olmasa belə birbaşa IP ünvanı ilə `REMOTE CORE` qatında göstərir.

6. **Kəsişməsiz Qatlı (Layered Orthogonal) Düzülüş:**
   * `REMOTE CORE` $\rightarrow$ `CORE NCS` $\rightarrow$ `ACCESS L1` $\rightarrow$ `ACCESS L2` iyerarxiyası.
   * Hər xətt üçün ayrılmış portlar və kanal zolaqları (lanes) sayəsində xətlərin bir-birinin üstünə düşməsi aradan qaldırılıb.

---

## 📋 Tələblər

* **Python 3.8 və ya daha yüksək**
* Tələb olunan xarici kitabxanalar: `netmiko`, `matplotlib`, `pillow`
*(Qeyd: `tkinter` standart Python ilə birlikdə gəlir. Linux istifadəçiləri üçün: `sudo apt-get install python3-tk`)*

---

## 🛠 Quraşdırma (Avtomatik və Manual)

### Metod 1: `requirements.bat` vasitəsilə (Windows üçün Ən Asan Yol) ⚡

Repozitoriyanı endirdikdən sonra qovluqda olan **`requirements.bat`** faylından istifadə edə bilərsiniz:

1. **`requirements.bat`** faylına sadəcə **ikiqat klikləyin** (və ya CMD terminalında `requirements.bat` yazıb Enter sıxın).
2. Avtomatik olaraq qara `cmd` pəncərəsi açılacaq, sisteminizdə Python-un olub-olmadığını yoxlayacaq və avtomatik olaraq bu komandanı icra edərək bütün lazımi kitabxanaları quraşdıracaq:
   ```cmd
   python -m pip install -r requirements.txt
   ```
3. Quraşdırma bitdikdə uğurlu mesaj çıxacaq və pəncərə bağlanmadan nəticəni sizə göstərəcək.

---

### Metod 2: Terminal / Komanda Sətri vasitəsilə (Manual)

```bash
# Repozitoriyanı klonlayın
git clone https://github.com/istifadeci_adi/l3-topology-weather-map.git
cd l3-topology-weather-map

# Tələb olunan kitabxanaları quraşdırın
pip install -r requirements.txt
```

---

## 💻 İstifadə Qaydası

### 1. Qrafik İnterfeys (Desktop GUI)

Heç bir arqument vermədən işə saldıqda birbaşa masaüstü pəncərəsi açılır:

```bash
python l3_topology_with_draw.io-and-png_with_BW.py
```

**GUI Pəncərəsində:**
1. Çıxış qovluğunun adını seçin.
2. Skan ediləcək Subnet(lər)i daxil edin (məs: `10.54.1.0/24, 10.54.100.0/24`).
3. (İstəyə bağlı) Əlavə Core NCS və ya Access ASR IP-lərini yazın.
4. SSH istifadəçi adı və parolunu daxil edin.
5. **▶ BAŞLAT** düyməsini sıxın.

 

---

### 2. Demo Rejimi (Canlı Cihaz Olmadan Test)

Şəbəkəyə qoşulmadan nümunə məlumatlarla test PNG və draw.io faylları yaratmaq üçün:

```bash
python l3_topology_with_draw.io-and-png_with_BW.py --demo --output-dir demo_output
```

---

## 📂 Çıxış Faylları

Proqram tamamlandıqda təyin olunmuş qovluqda iki əsas fayl yaradır:

* **`l2_weathermap.png`**: Yüksək dəqiqlikli (High-DPI), qaranlıq mövzulu rəsmi topologiya şəkli. Sağ tərəfdə xətlərin nömrə, interfeys, protokol və sürət (BW) cədvəli yer alır.
* **`l2_weathermap.drawio`**: [draw.io (diagrams.net)](https://app.diagrams.net) brauzerində və ya masaüstü tətbiqində birbaşa açılan, bütün qutuları, xətləri və nişanları ayrı-ayrılıqda redaktə edilə bilən XML sxemi.

---

## 🛡 Təhlükəsizlik

* SSH parolları heç bir faylda və ya loqda saxlanılmır.
* Cihazlarda yalnız oxuma (`show`) komandaları icra olunur, konfiqurasiyaya heç bir müdaxilə edilmir.

---

## 👤 Müəllif

**Kanan-179**  
*IP/MPLS Core & Access Network Engineer*