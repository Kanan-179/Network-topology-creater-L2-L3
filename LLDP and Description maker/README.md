# LLDP Automation (Cisco IOS-XR / IOS-XE)

Cisco **NCS (IOS-XR)** və **ASR 920 / 901 (IOS-XE)** cihazlarında LLDP-ni idarə edən və interface description-larını LLDP qonşularına görə düzəldən Python scriptləri.

Bütün cihazlara SSH ilə qoşulur, tipi (XR / XE) avtomatik təyin edir, hər dəyişikliyi icradan əvvəl **təsdiq üçün soruşur**, **execution log** yazır. IOS-XR-də dəyişikliklər `commit confirmed` ilə tətbiq olunur və təsdiq **canlı ping monitoru** pəncərəsində gözlənilir.

## Fayllar

| Fayl | Təyinatı |
|------|----------|
| `lldp_manager.py` | LLDP-ni yalnız OSPF/ISIS qonşuluğu olan portlarda saxlayır, qalan up fiziki portlarda söndürür |
| `lldp_description.py` | Port description-larını LLDP qonşusuna görə yazır: `TO_<System Name>_<Port id>` |
| `live_monitor.py` | Canlı ping monitoru və XR `commit confirmed` təsdiq pəncərəsi. Hər iki script bunu istifadə edir, **eyni qovluqda** olmalıdır |
| `requirements.txt` | Lazımi Python paketləri |
| `lldp_description_wavelength.py` | `lldp_description.py`-nin DWDM wavelength əlavə edən variantı (aşağıya bax) |

> **Qeyd:** `lldp_description_wavelength.py` adı nümunədir, faylı repoda necə saxlamısansa o adı yaz. Bu variant `commit confirmed` və ping monitoru yeniliyini **daxil etmir** (adi `commit` istifadə edir). Yeniliyi istəyirsənsə, `lldp_description.py` ilə eyni qaydada yenilənməlidir.

## Tələblər və qurulum

### Nələr yüklənməlidir

| Komponent | Versiya | Təyinatı |
|-----------|---------|----------|
| **Python** | 3.8 və ya yuxarı | Scriptləri işə salmaq üçün |
| **netmiko** | 4.2 və ya yuxarı | Cihazlara SSH ilə qoşulmaq və əmr göndərmək (**mütləqdir**) |
| **tkinter** | Python ilə gəlir | Ping monitoru pəncərəsi üçün (aşağıya bax) |
| **colorama** | istənilən | Windows terminalında rəngli çıxış (tövsiyə olunur) |
| **openpyxl** | istənilən | Excel hesabat yaratmaq (**istəyə bağlıdır**) |

> `netmiko` 4.x lazımdır, çünki scriptlərdə `read_timeout` və `cmd_verify` parametrləri işlənir. 3.x versiyada işləməyəcək.

> **tkinter:** Windows-da python.org-dan quraşdırılan Python-la birlikdə gəlir. Linux-da ayrıca yüklənir: `sudo apt install python3-tk`. tkinter yoxdursa script işləyir, amma ping monitoru açılmır və XR-də adi `commit` istifadə olunur.

### Addım-addım

1. **Python-u yüklə:** https://www.python.org/downloads/ (Windows-da quraşdırma zamanı **"Add python.exe to PATH"** qutusunu mütləq işarələ).
2. Versiyanı yoxla:
   ```bash
   python --version
   ```
3. Lazımi paketləri yüklə:
   ```bash
   pip install -r requirements.txt
   ```
   və ya birbaşa:
   ```bash
   pip install netmiko colorama openpyxl
   ```
4. (İstəyə bağlı) Ayrıca virtual mühitdə qurmaq üçün:
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Linux / macOS
   pip install -r requirements.txt
   ```

### Şəbəkə və hesab tələbləri

- Scripti işlədən kompüterdən cihazların idarəetmə IP-lərinə **SSH (port 22)** və **ping (ICMP)** çatımı olmalıdır.
- Cihazlarda SSH aktiv olmalıdır. Terminal / VTY-də SSH icazəsi olmalıdır.
- İstifadə olunan hesabın konfiqurasiya hüququ olmalıdır:
  - **IOS-XE:** privilege 15 (birbaşa `#` prompta düşən hesab)
  - **IOS-XR:** konfiqurasiya və `commit` hüququ olan task-group (məs. `root-lr` / `cisco-support`)
- Eyni username/password bütün IP-lərdə işləməlidir (script bir dəfə soruşur).
- Scriptlər `enable` parolu istəmir.

## İşə salmaq

```bash
python lldp_manager.py ips.txt
python lldp_description.py ips.txt
```

`ips.txt` verilməzsə (və ya tapılmazsa) IP-ləri terminala yapışdırırsan, bitirmək üçün boş sətir yazırsan. Fayl formatı sərbəstdir (txt/csv): içindəki bütün IPv4 ünvanları oxunur. XR və XE cihazları **qarışıq** və istənilən sırada ola bilər. Cihaz başına bir IP yazmaq məsləhətdir (eyni cihazın iki IP-si ayrı cihaz kimi işlənir).

Başlanğıcda soruşulanlar:
1. `Log/hesabat fayllari ucun ad` - yazdığın ad log və Excel fayllarının **əvvəlinə** əlavə olunur (boş buraxmaq olar).
2. `Username` / `Password` - parol heç yerdə saxlanmır.
3. `Excel hesabat da yaradilsin?` - `y` yazsan Excel də çıxır.
4. `Ping monitor penceresi acilsin? [Y/n]` - `Enter` və ya `y` desən ping monitoru pəncərəsi açılır (XR üçün `commit confirmed` təsdiqi orada olur).

## Təsdiq rejimi (terminal)

Hər dəyişiklik bloku icradan əvvəl ekrana çıxır və soruşulur:

```
Icra edilsin? [y]es / [n]o / [a]ll yes / [s]top
```

- `y` - bu blok icra olunsun
- `n` - bu blok keçilsin
- `a` - bu və bütün qalan blokları (bütün cihazlarda) sorğusuz icra et
- `s` - dayandır

`Ctrl+C` ilə də təhlükəsiz dayandırmaq olar.

## Ping monitoru və IOS-XR `commit confirmed` təsdiqi

Scripti işə salanda **2 pəncərə** olur:

1. **Terminal:** əvvəlki kimi icra gedir (rənglər, təsdiq sualları, execution log).
2. **Ping monitoru** (ayrı Tkinter pəncərəsi): siyahıdakı bütün IP-ləri hər 2 saniyədən bir ping edir və iki siyahıya bölür: **✔ Ping keçən** və **✖ Ping keçməyən**. Ardıcıl 2 ping keçməyəndə cihaz "keçməyən" sayılır. Script bitəndə ping dayanır, pəncərə ENTER-ə qədər açıq qalır.

### XR-də commit necə təsdiq olunur

IOS-XR-də konfiqurasiya `commit` əvəzinə **`commit confirmed minutes 1`** ilə tətbiq olunur. Bu o deməkdir ki, 1 dəqiqə ərzində təsdiq (`commit`) gəlməzsə, cihaz dəyişiklikləri **özü geri alır**. Əlaqə kəsilsə belə cihaz əvvəlki vəziyyətə qayıdır.

```
config göndərilir
        │
        ▼
commit confirmed minutes 1        (cihazda 60 saniyəlik geri sayım başlayır)
        │
        ▼
Ping monitoru pəncərəsində cihaz üçün sətir çıxır:
"IP host - commit ucun 42 san qalib | ping: OK (kesintisiz) | avto: 5 san qalanda qerar"
        │
        ├── Avto rejim: geri alınmasına 5 san qalanda
        │       ├── ping kəsilməyib və son ping keçir  →  commit (təsdiq)  → CONFIRMED
        │       └── ping kəsilib və ya keçmir          →  təsdiq yoxdur    → ROLLED_BACK
        │
        └── Əl ilə: [✔ Təsdiq et (commit)]  /  [✖ Geri al]  düymələri
```

- **Avto tesdiq** qutusu pəncərənin altındadır və default olaraq **aktivdir** (mavi fon, `☑ ... [AKTIV]`). Klik edərək söndürsən (`☐ ... [SONDURULUB]`), təsdiqi yalnız düymələrlə əl ilə verirsən.
- Avto rejimdə də düymələr işləyir. Hər ikisi son **3 saniyədə** bağlanır.
- Avto qərar verməzdən əvvəl həmin IP ayrıca bir yeni ping ilə yoxlanılır. Pəncərə ərzində ping bir dəfə də kəsilibsə (2 ardıcıl itki), təsdiq verilmir.
- **Geri al** seçilərsə və ya təsdiq verilməzsə, script cihazın geri almasını (~1 dəq) gözləyib növbəti cihaza keçir.
- Pəncərə kiçildikdə düymələr kəsilmir, ping siyahıları alt-alta düzülür.

### Fallback

- Ping monitoru açılmasa (tkinter yoxdur, `live_monitor.py` tapılmadı, və ya `n` seçildi), XR-də **adi `commit`** istifadə olunur.
- Təsdiq gözlənilərkən pəncərə bağlansa, terminalda `y/n` soruşulur.
- XE cihazlarda `commit confirmed` yoxdur (XE-də `write memory` soruşulur), onlar yalnız ping siyahısında görünür.

## `lldp_manager.py` necə işləyir

**IOS-XR (NCS)**
1. `show ip ospf neighbor` və `show isis neighbors` ilə qonşuluq olan interface-ləri tapır.
2. ISIS/OSPF bundle-dadırsa `show bundle bundle-etherN` ilə üzv portları tapır.
3. `show interfaces description | i up` ilə up olan **fiziki** portları götürür (sub-interface götürmür).
4. Qonşuluq portlarında və bundle üzvlərində LLDP qalır (`no lldp transmit disable`, `no lldp receive disable`), global `lldp` aktiv edilir.
5. Qalan up fiziki portlarda `lldp` → `receive disable`, `transmit disable` yazılır.
6. Hamısı **bir dəfə** `commit confirmed minutes 1` ilə göndərilir, təsdiq ping monitorunda verilir (yuxarıya bax).

**IOS-XE (ASR 920 / 901)**
1. `show ip ospf neighbor` / `show isis neighbors` ilə qonşuluq interface-lərini tapır (`BDI`, `BD`, `Vlan`, fiziki, port-channel).
2. OSPF/ISIS `BDI` / `BD` / `Vlan` üzərindədirsə `show bridge-domain <id>` ilə portu tapır. Bridge-domain-də bir neçə port varsa MAC cədvəlindən (`show bridge-domain`, sonra `show mac address-table bdomain <id>`, lazım olsa qonşunu ping edib) hansı portdan MAC gəldiyini təyin edir. Müəyyən olunmazsa hansı portun qalacağını səndən soruşur.
3. `show etherchannel summary` ilə port-channel üzvlərini qonşuluq portlarına əlavə edir.
4. `lldp run` yazır. `BDI`/`BD` halında portda `service instance`, `encapsulation untagged`, `l2protocol peer lldp`, `bridge-domain` əlavə edir.
5. Qonşuluq portlarında `lldp transmit` / `lldp receive`, qalan up fiziki portlarda `no lldp transmit` / `no lldp receive` yazır.
6. Sonda `write memory` soruşulur.

**Təhlükəsizlik:** cihazda heç bir OSPF/ISIS qonşusu və ya heç bir fiziki qonşuluq portu tapılmazsa, bütün portlarda LLDP söndürülməsin deyə cihaz dəyişdirilmədən keçilir.

## `lldp_description.py` necə işləyir

**IOS-XR**
1. `show lldp neighbors` oxuyur. Port ID-si `Bundle-Ether...` olan sətirləri keçir.
2. Hər Local Intf üçün `show lldp neighbors <port> detail` yazır.
3. Fiziki port: `TO_<System Name>_<Port id>`, məsələn `TO_YVLK_NCS5508_B_Te0/2/0/0`.
4. `Parent Interface` varsa bundle interface-ə `TO_<System Name>` yazılır.
5. Hamısı `commit confirmed minutes 1` ilə göndərilir, təsdiq ping monitorunda verilir.

**IOS-XE**
- `show lldp neighbors`, sonra `show lldp neighbors <port> detail` ilə fiziki portların description-ı eyni formatda yazılır.
- Port-channel üzvü olan portlar üçün (`show etherchannel summary` ilə tapılır) port-channel-in özünə `TO_<System Name>` yazılır.
- Sonda `write memory` soruşulur.

**Ümumi qaydalar**
- `System Name`-in domain hissəsi atılır (`Mingachevir-C920-2.aztelekom.az` → `Mingachevir-C920-2`).
- Description artıq düzgündürsə dəyişdirilmir.
- Port prefiksi qısalır (`TenGigE` → `Te`). `SHORT_NUMBERS = True` etsən 4 hissəli nömrələrdə sıfırlar da atılır (`Te0/2/0/0` → `Te0/2`).

**`lldp_description_wavelength.py` əlavəsi (yalnız XR):** hər port üçün `show controllers optics <id>` yazılır. Çıxışda `DWDM carrier Info` və `Wavelength` varsa, description `TO_<System Name>_<Port id>_<wavelength>` olur, məsələn `TO_YVLK_NCS5508_B_Te0/2_1560.606`.

## Çıxışlar

| Fayl | Məzmun |
|------|--------|
| `<ad>_lldp_execution_log_<tarix>.txt` | `lldp_manager.py` execution log |
| `<ad>_lldp_description_log_<tarix>.txt` | `lldp_description.py` execution log |
| `<ad>_lldp_report_<tarix>.xlsx` / `<ad>_lldp_description_report_<tarix>.xlsx` | Excel hesabat (istəyə görə) |

Excel və log-da hər əməliyyatın statusu yazılır:

| Status | Mənası |
|--------|--------|
| `APPLIED` | Tətbiq olundu (XE, və ya monitorsuz adi XR `commit`) |
| `CONFIRMED` | XR: `commit confirmed` təsdiq olundu, dəyişiklik qalıcıdır |
| `ROLLED_BACK` | XR: təsdiq verilmədi, cihaz dəyişikliyi geri aldı |
| `SKIPPED` | Keçildi (sən `n` dedin və ya cihaz təhlükəsizlik üçün keçildi) |
| `ERROR` | Qoşulma və ya commit xətası |

Terminalda rənglər: adi yazılar ağ, əmrlər qırmızı dırnaqda yaşıl, cihaz IP-ləri mavi, cihazlar bir-birindən xətlə ayrılır. Log fayllarında rəngsiz mətn yazılır.

## Ayarlar (faylların yuxarısında)

| Dəyişən | Fayl | Təyinatı |
|---------|------|----------|
| `SHOW_RAW` | scriptlər | `True` olsa cihazdan gələn xam cavab da göstərilir (debug üçün) |
| `SERVICE_INST`, `SERVICE_BD` | `lldp_manager.py` | ASR-də service instance və bridge-domain nömrəsi |
| `SHORT_NUMBERS` | `lldp_description.py` | Port nömrəsinin qısaldılması |
| `CONFIRM_MIN` | scriptlər | `commit confirmed minutes N` (default 1) |
| `CONFIRM_MARGIN` | scriptlər | Geri alınmaya bu qədər saniyə qalanda təsdiq qəbul olunmur (default 3) |
| `AUTO_AT` | `live_monitor.py` | Avto qərarın verildiyi an: geri alınmaya qalan saniyə (default 5) |
| `LOCK_AT` | `live_monitor.py` | Düymələrin bağlandığı an (default 3) |
| `PING_INTERVAL`, `FAIL_THRESHOLD` | `live_monitor.py` | Ping aralığı (2 san) və neçə ardıcıl itkidə "keçmir" sayılması (2) |

## Qeydlər

- İlk dəfə **bir XR və bir XE cihazda** sınaqdan keçir, sonra siyahını genişləndir.
- XR-də `commit confirmed` xəta versə, candidate konfiqurasiya `abort` olunur.
- Qoşulma uğursuz olarsa 3 dəfə təkrar cəhd edilir, sonra cihaz keçilir və növbətiyə ötürülür.
- Avto təsdiq və ya inkar yalnız sənin kompüterindən cihazın idarəetmə IP-sinə gedən pinqə əsaslanır. Ping başqa səbəbdən (şəbəkə problemi) kəsilsə də təsdiq verilməyəcək.
- Repoya `ips.txt`, log və Excel fayllarını əlavə etmə (cihaz IP-ləri və hostname-lər var). Bunun üçün `.gitignore`:

```
ips.txt
*_log_*.txt
*_report_*.xlsx
```
