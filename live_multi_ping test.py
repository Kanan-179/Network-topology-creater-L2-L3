import tkinter as tk
from tkinter import scrolledtext, messagebox
import subprocess
import platform
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
import re
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

class ContinuousMultiPingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Canlı Fasiləsiz Multi-Ping Monitoru (Continuous ICMP)")
        self.root.geometry("1020x680")
        self.root.configure(bg="#f4f6f9")

        self.is_running = False
        self.log_filename = f"ping_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        # Hər IP üzrə canlı statistika bazası
        # Format: { ip: {"status": "UP"/"DOWN", "reply_ms": "...", "ttl": "...", "sent": 0, "ok": 0, "fail": 0, "last_check": "..."} }
        self.ip_stats = {}

        self.setup_ui()

    def setup_ui(self):
        # 1. İdarəetmə Paneli
        top_frame = tk.LabelFrame(self.root, text=" İdarəetmə Paneli ", font=("Arial", 10, "bold"), bg="#f4f6f9", padx=10, pady=8)
        top_frame.pack(fill="x", padx=10, pady=5)

        self.btn_start = tk.Button(top_frame, text="▶ Başlat (Fasiləsiz)", font=("Arial", 10, "bold"), bg="#28a745", fg="white", width=18, command=self.start_ping)
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = tk.Button(top_frame, text="⏹ Dayandır", font=("Arial", 10, "bold"), bg="#dc3545", fg="white", width=15, state="disabled", command=self.stop_ping)
        self.btn_stop.pack(side="left", padx=10)

        tk.Label(top_frame, text="Fasilə (san):", font=("Arial", 9, "bold"), bg="#f4f6f9").pack(side="left", padx=(15, 5))
        self.ent_interval = tk.Entry(top_frame, width=5, font=("Arial", 10))
        self.ent_interval.insert(0, "1")
        self.ent_interval.pack(side="left", padx=5)

        self.lbl_status = tk.Label(top_frame, text="Status: Hazır", font=("Arial", 10, "bold"), bg="#f4f6f9", fg="#333333")
        self.lbl_status.pack(side="left", padx=20)

        # 2. Əsas Ekran (Sol: IP Siyahısı | Orta: Keçənlər | Sağ: Keçməyənlər)
        main_frame = tk.Frame(self.root, bg="#f4f6f9")
        main_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Sol: IP daxiletmə hissəsi
        left_frame = tk.LabelFrame(main_frame, text=" IP Siyahısı (Hər sətirdə 1 IP) ", font=("Arial", 9, "bold"), bg="#f4f6f9")
        left_frame.pack(side="left", fill="both", expand=False, padx=(0, 5))

        self.txt_ips = scrolledtext.ScrolledText(left_frame, width=22, font=("Consolas", 10))
        self.txt_ips.pack(fill="both", expand=True, padx=5, pady=5)
        self.txt_ips.insert("1.0", "8.8.8.8\n1.1.1.1\n192.168.1.1\n10.255.255.1\n8.8.4.4")

        # Orta: Ping Keçənlər (UP)
        self.mid_frame = tk.LabelFrame(main_frame, text=" ✅ Ping Keçənlər (0) ", font=("Arial", 9, "bold"), fg="#155724", bg="#f4f6f9")
        self.mid_frame.pack(side="left", fill="both", expand=True, padx=5)

        self.lst_up = tk.Listbox(self.mid_frame, font=("Consolas", 10, "bold"), fg="#155724", bg="#d4edda", selectbackground="#28a745")
        self.lst_up.pack(fill="both", expand=True, padx=5, pady=5)

        # Sağ: Ping Keçməyənlər (DOWN)
        self.right_frame = tk.LabelFrame(main_frame, text=" ❌ Ping Keçməyənlər (0) ", font=("Arial", 9, "bold"), fg="#721c24", bg="#f4f6f9")
        self.right_frame.pack(side="left", fill="both", expand=True, padx=(5, 0))

        self.lst_down = tk.Listbox(self.right_frame, font=("Consolas", 10, "bold"), fg="#721c24", bg="#f8d7da", selectbackground="#dc3545")
        self.lst_down.pack(fill="both", expand=True, padx=5, pady=5)

        # 3. Canlı Addım-Addım Konsol / Log Ekranı
        bottom_frame = tk.LabelFrame(self.root, text=" Canlı İcra Addımları və Echo/Reply Logları ", font=("Arial", 9, "bold"), bg="#f4f6f9")
        bottom_frame.pack(fill="both", expand=False, padx=10, pady=5)

        self.txt_console = scrolledtext.ScrolledText(bottom_frame, height=10, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00")
        self.txt_console.pack(fill="both", expand=True, padx=5, pady=5)

    def write_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        
        self.txt_console.insert(tk.END, full_msg + "\n")
        # Konsol şişməsin deyə son 500 sətri ekranda saxlayırıq (faylda hamısı qalır)
        if int(self.txt_console.index('end-1c').split('.')[0]) > 500:
            self.txt_console.delete("1.0", "2.0")
        self.txt_console.see(tk.END)
        print(full_msg)
        
        with open(self.log_filename, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

    def ping_single_ip(self, ip):
        """Tək bir IP-yə ping atır və həqiqi ICMP Reply Time (ms) və TTL dəyərini çıxarır."""
        is_win = platform.system().lower() == "windows"
        param = "-n" if is_win else "-c"
        timeout_param = "-w" if is_win else "-W"
        timeout_val = "1000" if is_win else "1"

        command = ["ping", param, "1", timeout_param, timeout_val, ip]
        start_perf = time.perf_counter()

        try:
            startupinfo = None
            if is_win:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            output = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
            out_text = output.stdout
            calc_ms = int((time.perf_counter() - start_perf) * 1000)

            if output.returncode == 0 and ("ttl=" in out_text.lower()):
                # Ping cavabından həqiqi time=XXms və ya time<1ms dəyərini tuturuq
                time_match = re.search(r"(?:time|시간|zeit|temps|время|vaxt)[=<]\s*(\d+(?:\.\d+)?)\s*ms", out_text, re.IGNORECASE)
                if not time_match:
                    # Ümumi rəqəm+ms axtarışı (dil fərqliliyi olarsa)
                    time_match = re.search(r"[=<]\s*(\d+(?:\.\d+)?)\s*ms", out_text, re.IGNORECASE)

                reply_ms = f"{time_match.group(1)} ms" if time_match else f"{calc_ms} ms"

                ttl_match = re.search(r"ttl=(\d+)", out_text, re.IGNORECASE)
                ttl_str = ttl_match.group(1) if ttl_match else "N/A"

                return ip, True, reply_ms, ttl_str
            else:
                return ip, False, "Timeout", "-"
        except Exception as e:
            return ip, False, "Error", "-"

    def start_ping(self):
        raw_ips = self.txt_ips.get("1.0", tk.END).strip().splitlines()
        # Təkrarlanan IP-ləri sıradan çıxarırıq ki, siyahıda qarışıqlıq olmasın
        ip_list = list(dict.fromkeys([ip.strip() for ip in raw_ips if ip.strip()]))

        if not ip_list:
            messagebox.showwarning("Diqqət", "Ping atmaq üçün ən azı 1 IP ünvanı daxil edin!")
            return

        try:
            interval = float(self.ent_interval.get().strip())
            if interval < 0.1:
                interval = 0.1
        except ValueError:
            interval = 1.0

        self.ip_stats.clear()
        for ip in ip_list:
            self.ip_stats[ip] = {
                "status": "WAITING",
                "reply_ms": "-",
                "ttl": "-",
                "sent": 0,
                "ok": 0,
                "fail": 0,
                "last_check": "-"
            }

        self.lst_up.delete(0, tk.END)
        self.lst_down.delete(0, tk.END)

        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.txt_ips.config(state="disabled")
        self.lbl_status.config(text="Status: Canlı Ping gedir...", fg="#007bff")

        self.write_log(f"Fasiləsiz Ping başladıldı ({len(ip_list)} IP). Dayandırmaq üçün 'Dayandır' düyməsini sıxın.")

        threading.Thread(target=self.continuous_ping_loop, args=(ip_list, interval), daemon=True).start()

    def continuous_ping_loop(self, ip_list, interval):
        cycle = 0
        # Executor dövrdən kənarda yaradılır ki, hər saniyə yeni thread-lər açıb-bağlamağa vaxt itirməsin
        with ThreadPoolExecutor(max_workers=min(100, len(ip_list) + 5)) as executor:
            while self.is_running:
                cycle += 1
                cycle_start = time.time()

                future_to_ip = {executor.submit(self.ping_single_ip, ip): ip for ip in ip_list}

                for future in as_completed(future_to_ip):
                    if not self.is_running:
                        break

                    ip, is_up, reply_ms, ttl_str = future.result()
                    now_str = datetime.now().strftime("%H:%M:%S")

                    st = self.ip_stats[ip]
                    st["sent"] += 1
                    st["last_check"] = now_str
                    st["reply_ms"] = reply_ms
                    st["ttl"] = ttl_str

                    if is_up:
                        st["status"] = "UP"
                        st["ok"] += 1
                        log_msg = f"[Sorğu #{cycle}] {ip:<15} -> Echo Reply: {reply_ms:<8} | TTL={ttl_str:<4} (Keçdi: {st['ok']}/{st['sent']})"
                    else:
                        st["status"] = "DOWN"
                        st["fail"] += 1
                        log_msg = f"[Sorğu #{cycle}] {ip:<15} -> CAVAB YOXDUR ({reply_ms})         (İtki: {st['fail']}/{st['sent']})"

                    # Hər cavab gəldikcə həm konsolu, həm də Keçənlər/Keçməyənlər pəncərəsini canlı yeniləyirik
                    self.root.after(0, self.write_log, log_msg)
                    self.root.after(0, self.refresh_listboxes)

                # Növbəti dalğaya keçməzdən əvvəl fasilə (Dayandır basılarsa dərhal çıxır)
                elapsed = time.time() - cycle_start
                sleep_needed = max(0.1, interval - elapsed)
                step = 0.05
                slept = 0.0
                while self.is_running and slept < sleep_needed:
                    time.sleep(step)
                    slept += step

        self.root.after(0, self.finish_process)

    def refresh_listboxes(self):
        """Keçənlər və Keçməyənlər siyahısını ekranda titrəmə yaratmadan canlı yeniləyir."""
        up_items = []
        down_items = []

        for ip, st in self.ip_stats.items():
            if st["status"] == "UP":
                line = f"{ip:<15} | Reply: {st['reply_ms']:<8} | TTL: {st['ttl']:<3} | Paket: #{st['sent']}"
                up_items.append(line)
            elif st["status"] == "DOWN":
                line = f"{ip:<15} | Status: {st['reply_ms']:<8} | İtki: {st['fail']}/{st['sent']}"
                down_items.append(line)

        # Scroll mövqeyini qoruyaraq yenilə
        up_yview = self.lst_up.yview()
        down_yview = self.lst_down.yview()

        self.lst_up.delete(0, tk.END)
        for item in up_items:
            self.lst_up.insert(tk.END, item)
        if up_yview:
            self.lst_up.yview_moveto(up_yview[0])

        self.lst_down.delete(0, tk.END)
        for item in down_items:
            self.lst_down.insert(tk.END, item)
        if down_yview:
            self.lst_down.yview_moveto(down_yview[0])

        self.mid_frame.config(text=f" ✅ Ping Keçənlər ({len(up_items)}) ")
        self.right_frame.config(text=f" ❌ Ping Keçməyənlər ({len(down_items)}) ")

    def stop_ping(self):
        if self.is_running:
            self.is_running = False
            self.lbl_status.config(text="Status: Dayandırılır...", fg="#dc3545")
            self.write_log("Dayandırılma əmri verildi. Cari paketlər tamamlanır...")

    def finish_process(self):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.txt_ips.config(state="normal")
        self.lbl_status.config(text="Status: Dayandırıldı", fg="#dc3545")

        up_total = sum(1 for st in self.ip_stats.values() if st["status"] == "UP")
        down_total = sum(1 for st in self.ip_stats.values() if st["status"] == "DOWN")

        self.write_log(f"Ping dayandırıldı! Son vəziyyət -> Keçənlər: {up_total} | Keçməyənlər: {down_total}")
        self.export_to_excel()

    def export_to_excel(self):
        if not self.ip_stats:
            return

        excel_filename = f"ping_hesabat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Canlı Ping Statistikası"

            headers = ["IP Ünvanı", "Son Status", "Son Reply Time", "TTL", "Göndərilən Paket", "Uğurlu (UP)", "İtki (DOWN)", "Son Yoxlanma Saatı"]
            ws.append(headers)

            header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True, name="Arial", size=10)
            thin_border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin')
            )

            for col_num in range(1, len(headers) + 1):
                cell = ws.cell(row=1, column=col_num)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border

            up_fill = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")
            down_fill = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")

            for row_idx, (ip, st) in enumerate(self.ip_stats.items(), start=2):
                status_text = "KEÇDİ (UP)" if st["status"] == "UP" else "KEÇMƏDİ (DOWN)"
                row_data = [ip, status_text, st["reply_ms"], st["ttl"], st["sent"], st["ok"], st["fail"], st["last_check"]]
                ws.append(row_data)

                row_fill = up_fill if st["status"] == "UP" else down_fill
                for col_idx in range(1, len(row_data) + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.fill = row_fill
                    cell.font = Font(name="Arial", size=10)
                    cell.border = thin_border
                    cell.alignment = Alignment(horizontal="center" if col_idx != 1 else "left")

            column_widths = [18, 16, 16, 10, 18, 15, 15, 20]
            for i, width in enumerate(column_widths, start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width

            wb.save(excel_filename)
            self.write_log(f"Yekun statistika Excel faylına yazıldı: {excel_filename}")
            self.write_log(f"Bütün detallı loglar saxlanıldı: {self.log_filename}")
        except Exception as e:
            self.write_log(f"Excel faylı yaradılarkən xəta: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ContinuousMultiPingApp(root)
    root.mainloop()

    input("\nProqram başa çatdı. Çıxmaq üçün Enter düyməsini sıxın...")