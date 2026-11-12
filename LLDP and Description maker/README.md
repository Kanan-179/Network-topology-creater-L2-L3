# LLDP Automation (Cisco IOS-XR / IOS-XE)

Cisco **NCS (IOS-XR)** və **ASR 920 / 901 (IOS-XE)** cihazlarında LLDP-ni idarə edən və interface description-larını LLDP qonşularına görə düzəldən Python scriptləri.

Bütün cihazlara SSH ilə qoşulur, tipi (XR / XE) avtomatik təyin edir, hər dəyişikliyi icradan əvvəl **təsdiq üçün soruşur** və **execution log** yazır.

## Fayllar

| Fayl | Təyinatı |
|------|----------|
| `lldp_manager.py` | LLDP-ni yalnız OSPF/ISIS qonşuluğu olan portlarda saxlayır, qalan up fiziki portlarda söndürür |
| `lldp_description.py` | Port description-larını LLDP qonşusuna görə yazır: `TO_<System Name>_<Port id>` |
| `lldp_description_wavelength.py` | Yuxarıdakının eyni, əlavə olaraq XR-də DWDM optikanın wavelength-ini description-ın sonuna əlavə edir |

> `lldp_description_wavelength.py` adı nümunədir, faylı repoda necə saxlamısansa o adı yaz.

## Qurulum

```bash
pip install netmiko colorama openpyxl
```

`openpyxl` yalnız Excel hesabat üçündür, istəyə bağlıdır. Python 3.8+ tələb olunur.

## İşə salmaq

```bash
python lldp_manager.py ips.txt
python lldp_description.py ips.txt
```

`ips.txt` verilməzsə (və ya tapılmazsa) IP-ləri terminala yapışdırırsan, bitirmək üçün boş sətir yazırsan. Fayl formatı sərbəstdir (txt/csv): içindəki bütün IPv4 ünvanları oxunur. XR və XE cihazları **qarışıq** və istənilən sırada ola bilər.

Başlanğıcda soruşulanlar:
1. `Log/hesabat fayllari ucun ad` - yazdığın ad log və Excel fayllarının **əvvəlinə** əlavə olunur (boş buraxmaq olar).
2. `Username` / `Password` - parol heç yerdə saxlanmır.
3. `Excel hesabat da yaradilsin?` - `y` yazsan Excel də çıxır.

## Təsdiq rejimi

Hər dəyişiklik bloku icradan əvvəl ekrana çıxır və soruşulur:

```
Icra edilsin? [y]es / [n]o / [a]ll yes / [s]top
```

- `y` - bu blok icra olunsun
- `n` - bu blok keçilsin
- `a` - bu və bütün qalan blokları (bütün cihazlarda) sorğusuz icra et
- `s` - dayandır

`Ctrl+C` ilə də təhlükəsiz dayandırmaq olar.

## `lldp_manager.py` necə işləyir

**IOS-XR (NCS)**
1. `show ip ospf neighbor` və `show isis neighbors` ilə qonşuluq olan interface-ləri tapır.
2. ISIS/OSPF bundle-dadırsa `show bundle bundle-etherN` ilə üzv portları tapır.
3. `show interfaces description | i up` ilə up olan **fiziki** portları götürür (sub-interface götürmür).
4. Qonşuluq portlarında və bundle üzvlərində LLDP qalır (`no lldp transmit disable`, `no lldp receive disable`), global `lldp` aktiv edilir.
5. Qalan up fiziki portlarda `lldp` → `receive disable`, `transmit disable` yazılır.
6. Hamısı sonda **bir dəfə** `commit` olunur.

**IOS-XE (ASR 920 / 901)**
1. `show ip ospf neighbor` / `show isis neighbors` ilə qonşuluq interface-lərini tapır.
2. OSPF `BDI` və ya `Vlan` üzərindədirsə `show bridge-domain <id>` ilə fiziki / port-channel portu tapır.
3. `show etherchannel summary` ilə port-channel üzvlərini qonşuluq portlarına əlavə edir.
4. `lldp run` yazır. `BDI` halında port-channel/porta `service instance`, `encapsulation untagged`, `l2protocol peer lldp`, `bridge-domain` əlavə edir.
5. Qonşuluq portlarında `lldp transmit` / `lldp receive`, qalan up fiziki portlarda `no lldp transmit` / `no lldp receive` yazır.
6. Sonda `write memory` soruşulur.

**Təhlükəsizlik:** cihazda heç bir OSPF/ISIS qonşusu tapılmazsa, bütün portlarda LLDP söndürülməsin deyə cihaz keçilir.

## `lldp_description.py` necə işləyir

**IOS-XR**
1. `show lldp neighbors` oxuyur. Port ID-si `Bundle-Ether...` olan sətirləri keçir.
2. Hər Local Intf üçün `show lldp neighbors <port> detail` yazır.
3. Fiziki port: `TO_<System Name>_<Port id>`, məsələn `TO_YVLK_NCS5508_B_Te0/2/0/0`.
4. `Parent Interface` varsa bundle interface-ə `TO_<System Name>` yazılır.
5. Hamısı sonda bir dəfə `commit` olunur.

**IOS-XE**
- `show lldp neighbors`, sonra `show lldp neighbors <port> detail` ilə fiziki portların description-ı eyni formatda yazılır, sonda `write memory` soruşulur.
- Port-channel üçün description hələ dəstəklənmir.

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

Terminalda rənglər: adi yazılar ağ, əmrlər qırmızı dırnaqda yaşıl, cihaz IP-ləri mavi, cihazlar bir-birindən xətlə ayrılır. Log fayllarında rəngsiz mətn yazılır.

## Ayarlar (faylın yuxarısında)

| Dəyişən | Təyinatı |
|---------|----------|
| `SHOW_RAW` | `True` olsa cihazdan gələn xam cavab da göstərilir (debug üçün) |
| `SERVICE_INST`, `SERVICE_BD` | `lldp_manager.py`: ASR-də service instance və bridge-domain nömrəsi |
| `SHORT_NUMBERS` | `lldp_description.py`: port nömrəsinin qısaldılması |

## Qeydlər

- İlk dəfə **bir XR və bir XE cihazda** sınaqdan keçir, sonra siyahını genişləndir.
- XR-də `commit` xəta versə, candidate konfiqurasiya `abort` olunur.
- Qoşulma uğursuz olarsa 3 dəfə təkrar cəhd edilir, sonra cihaz keçilir və növbətiyə ötürülür.
- Repoya `ips.txt`, log və Excel fayllarını əlavə etmə (cihaz IP-ləri və hostname-lər var). Bunun üçün `.gitignore`:

```
ips.txt
*_log_*.txt
*_report_*.xlsx
```
