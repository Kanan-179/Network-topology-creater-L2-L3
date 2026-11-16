#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Canli ping monitoru + "commit confirmed" tesdiq penceresi (ayri proses, Tkinter).

Isleyis:
  - Monitor(ips)  -> ayri pencere acilir, butun IP-leri canli ping edir
                     ("Ping kecen" ve "Ping kecmeyen" olaraq 2 sutunda)
  - ask_confirm() -> XR cihazda "commit confirmed" yazildiqdan sonra pencerede geri sayim ve
                     [Tesdiq et] / [Geri al] duymeleri gosterir
  - done()        -> kod bitende pinq dayanir (pencere ENTER-e qeder acıq qalir)

Digər scriptler tərəfindən import olunur:  from live_monitor import Monitor
"""
import multiprocessing as mp
import os
import queue
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PING_INTERVAL = 2.0   # saniye
FAIL_THRESHOLD = 2    # ardicil bu qeder ping kecmese FAIL sayilir
AUTO_AT = 5           # avto-tesdiq: cihazin geri alinmasina bu qeder saniye qalanda qerar verilir
LOCK_AT = 3           # bu qeder saniye qalanda duymeler baglanir


def _ping(ip):
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
        flags = subprocess.CREATE_NO_WINDOW
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        flags = 0
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5, creationflags=flags)
        return r.returncode == 0 and b"ttl=" in r.stdout.lower()
    except Exception:
        return False


# ============================ PENCERE PROSESI ============================
def _monitor_main(cmd_q, ans_q):
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as e:  # tkinter yoxdur
        print("Ping monitor ucun tkinter lazimdir:", e)
        return

    BG, PANEL, FG = "#0d1117", "#161b22", "#e6edf3"
    GREEN, RED, YEL, GREY = "#3fb950", "#f85149", "#d29922", "#8b949e"

    root = tk.Tk()
    root.title("LLDP Ping Monitor")
    root.geometry(os.environ.get("LLDP_MON_GEOM", "1000x680"))
    root.minsize(360, 320)
    root.configure(bg=BG)

    lock = threading.Lock()
    stop = threading.Event()
    state = {"ips": [], "hosts": {}, "status": {}, "since": {}, "fails": {}, "done": False}
    pending = {}

    # ---------------------------- ping dovru ----------------------------
    def ping_loop():
        while not stop.is_set():
            with lock:
                ips = list(state["ips"])
                done = state["done"]
            if ips and not done:
                with ThreadPoolExecutor(max_workers=min(64, len(ips))) as ex:
                    res = list(ex.map(_ping, ips))
                now = time.time()
                with lock:
                    for ip, ok in zip(ips, res):
                        if ok:
                            state["fails"][ip] = 0
                            new = "OK"
                        else:
                            state["fails"][ip] = state["fails"].get(ip, 0) + 1
                            new = "FAIL" if state["fails"][ip] >= FAIL_THRESHOLD else state["status"].get(ip)
                        if new and new != state["status"].get(ip):
                            state["status"][ip] = new
                            state["since"][ip] = now
            stop.wait(PING_INTERVAL)

    threading.Thread(target=ping_loop, daemon=True).start()

    # ------------------------------ GUI ---------------------------------
    head = tk.Label(root, text="", bg=BG, fg=FG, font=("Consolas", 13, "bold"), anchor="w",
                    justify="left", wraplength=600)
    head.pack(fill="x", padx=12, pady=(10, 4))

    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                 rowheight=22, borderwidth=0, font=("Consolas", 10))
    st.configure("Treeview.Heading", background="#21262d", foreground=FG, font=("Consolas", 10, "bold"))
    st.map("Treeview", background=[("selected", "#30363d")])

    bottom = tk.Frame(root, bg=BG)          # evvelce pack olunur -> pencere kicilse de duymeler gorunur
    bottom.pack(side="bottom", fill="x")

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=10)
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    def make_pane(col, title, color):
        fr = tk.Frame(body, bg=BG)
        fr.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)
        lbl = tk.Label(fr, text=title, bg=BG, fg=color, font=("Consolas", 12, "bold"), anchor="w")
        lbl.pack(fill="x")
        tree = ttk.Treeview(fr, columns=("ip", "host", "since"), show="headings", height=5)
        for c, t, w in (("ip", "IP", 120), ("host", "Hostname", 190), ("since", "Beri", 70)):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w")
        tree.tag_configure("row", foreground=color)
        sb = ttk.Scrollbar(fr, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return fr, tree, lbl

    ok_fr, ok_tree, ok_lbl = make_pane(0, "", GREEN)
    fail_fr, fail_tree, fail_lbl = make_pane(1, "", RED)

    layout = {"mode": "wide"}

    def relayout(event):
        # dar pencerede iki siyahi alt-alta duzulur
        mode = "narrow" if event.width < 700 else "wide"
        if mode == layout["mode"]:
            return
        layout["mode"] = mode
        if mode == "narrow":
            ok_fr.grid_configure(row=0, column=0)
            fail_fr.grid_configure(row=1, column=0)
            body.columnconfigure(1, weight=0)
            body.rowconfigure(1, weight=1)
        else:
            ok_fr.grid_configure(row=0, column=0)
            fail_fr.grid_configure(row=0, column=1)
            body.columnconfigure(1, weight=1)
            body.rowconfigure(1, weight=0)

    body.bind("<Configure>", relayout)
    last_sig = {ok_tree: None, fail_tree: None}

    auto_var = tk.BooleanVar(value=True)
    pend_title = tk.Label(bottom, text="Commit confirmed tesdiq gozleyen XR cihazlar", bg=BG, fg=YEL,
                          font=("Consolas", 12, "bold"), anchor="w", justify="left", wraplength=600)
    pend_title.pack(fill="x", padx=12, pady=(8, 0))
    # tünd fonda gorunmeyen "tik" problemi olmasin deye: indicatoron=False -> acik olanda goy fon + ☑/☐
    auto_chk = tk.Checkbutton(
        bottom, variable=auto_var, indicatoron=False, bg="#21262d", fg=FG, selectcolor="#1f6feb",
        activebackground="#30363d", activeforeground=FG, relief="flat", anchor="w", justify="left",
        wraplength=600, font=("Consolas", 10, "bold"), padx=10, pady=5, bd=0)

    def refresh_auto(*_):
        on = auto_var.get()
        auto_chk.config(text=("☑ " if on else "☐ ") +
                        f"Avto tesdiq: {AUTO_AT} san qalanda ping OK (kesintisiz) olarsa commit, kecmirse inkar"
                        + ("   [AKTIV]" if on else "   [SONDURULUB - el ile tesdiq]"))

    auto_var.trace_add("write", refresh_auto)
    refresh_auto()
    auto_chk.pack(fill="x", padx=10, pady=(2, 0))
    pend_frame = tk.Frame(bottom, bg=BG)
    pend_frame.pack(fill="x", padx=10, pady=(2, 10))
    empty_lbl = tk.Label(pend_frame, text="(hazirda tesdiq gozleyen cihaz yoxdur)", bg=BG, fg=GREY,
                         font=("Consolas", 10), anchor="w")
    empty_lbl.pack(fill="x", padx=4)

    def answer(ip, kind, who="el ile"):
        p = pending.get(ip)
        if not p or p["answered"]:
            return
        p["answered"] = True
        p["decision"] = kind
        p["who"] = who
        p["yes"].config(state="disabled")
        p["no"].config(state="disabled")
        ans_q.put((kind, ip))

    def add_pending(ip, host, expire):
        remove_pending(ip)
        empty_lbl.pack_forget()
        f = tk.Frame(pend_frame, bg=PANEL, highlightbackground=YEL, highlightthickness=1)
        f.pack(fill="x", padx=4, pady=3)
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        lbl = tk.Label(f, text="", bg=PANEL, fg=YEL, font=("Consolas", 11, "bold"),
                       anchor="w", justify="left", wraplength=300)
        lbl.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 2))
        b_yes = tk.Button(f, text="✔ Təsdiq et (commit)", bg="#238636", fg="white", relief="flat",
                          padx=10, pady=4, command=lambda: answer(ip, "confirm"))
        b_no = tk.Button(f, text="✖ Geri al", bg="#da3633", fg="white", relief="flat",
                         padx=10, pady=4, command=lambda: answer(ip, "rollback"))
        b_yes.grid(row=1, column=0, sticky="ew", padx=(8, 4), pady=(2, 8))
        b_no.grid(row=1, column=1, sticky="ew", padx=(4, 8), pady=(2, 8))
        f.bind("<Configure>", lambda e, l=lbl: l.config(wraplength=max(120, e.width - 24)))
        pending[ip] = {"frame": f, "lbl": lbl, "yes": b_yes, "no": b_no, "host": host,
                       "deadline": expire, "answered": False, "decision": None, "who": "",
                       "broken": False, "fresh": None, "fresh_started": False}
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        root.after(600, lambda: root.attributes("-topmost", False))

    def remove_pending(ip):
        p = pending.pop(ip, None)
        if p:
            p["frame"].destroy()
        if not pending:
            empty_lbl.pack(fill="x", padx=4)

    def tick():
        now = time.time()
        for ip, p in list(pending.items()):
            rem = p["deadline"] - now          # cihazin real geri alinmasina qalan vaxt
            with lock:
                st_now = state["status"].get(ip)
                fails = state["fails"].get(ip, 0)
            if st_now == "FAIL":
                p["broken"] = True             # pencere ərzində ping kəsilibsə yadda saxla
            ping_txt = (st_now or "?") + (" (kesintisiz)" if not p["broken"] else " (KESILIB!)")

            if p["answered"]:
                if p["decision"] == "confirm":
                    p["lbl"].config(text=f"{ip}  {p['host']}   -  {p['who']}: TESDIQ gonderildi, commit yazilir...",
                                    fg=GREEN)
                else:
                    p["lbl"].config(text=f"{ip}  {p['host']}   -  {p['who']}: INKAR (cihaz geri alacaq)", fg=RED)
                continue

            # ---- avto qerar: AUTO_AT saniye qalanda ----
            if auto_var.get():
                if rem <= AUTO_AT + 1 and not p["fresh_started"]:
                    p["fresh_started"] = True      # son anda yeni (fresh) ping
                    threading.Thread(target=lambda ip=ip, p=p: p.__setitem__("fresh", _ping(ip)),
                                     daemon=True).start()
                if rem <= AUTO_AT and (p["fresh"] is not None or rem <= AUTO_AT - 1):
                    good = (not p["broken"]) and st_now == "OK" and fails == 0 and p["fresh"] is True
                    answer(ip, "confirm" if good else "rollback", "avto")
                    continue

            if rem <= LOCK_AT:
                p["yes"].config(state="disabled")
                p["no"].config(state="disabled")
                p["lbl"].config(text=f"{ip}  {p['host']}   -  VAXT BITDI, cihaz avtomatik geri alinir", fg=RED)
            else:
                col = RED if rem <= 15 else YEL
                mode = f"avto: {AUTO_AT} san qalanda qerar" if auto_var.get() else "el ile tesdiq"
                p["lbl"].config(
                    text=f"{ip}  {p['host']}   -  commit ucun {int(rem)} san qalib   |   ping: {ping_txt}   |   {mode}",
                    fg=col)
        root.after(500, tick)

    def fmt_since(ts):
        return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""

    def refresh():
        with lock:
            ips = list(state["ips"])
            hosts = dict(state["hosts"])
            status = dict(state["status"])
            since = dict(state["since"])
            done = state["done"]
        ok_rows = [(ip, hosts.get(ip, ""), fmt_since(since.get(ip))) for ip in ips if status.get(ip) == "OK"]
        fail_rows = [(ip, hosts.get(ip, ""), fmt_since(since.get(ip))) for ip in ips if status.get(ip) == "FAIL"]
        unknown = len(ips) - len(ok_rows) - len(fail_rows)
        for tree, rows in ((ok_tree, ok_rows), (fail_tree, fail_rows)):
            if rows != last_sig[tree]:
                tree.delete(*tree.get_children())
                for r in rows:
                    tree.insert("", "end", values=r, tags=("row",))
                last_sig[tree] = rows
        ok_lbl.config(text=f"✔ Ping kecen ({len(ok_rows)})")
        fail_lbl.config(text=f"✖ Ping kecmeyen ({len(fail_rows)})")
        mode = "BITDI - ping dayandirildi" if done else "icra olunur - canli ping"
        head.config(text=f"Cihaz: {len(ips)}   |   yoxlanilir: {unknown}   |   {mode}")
        root.title("LLDP Ping Monitor - " + ("BITDI" if done else "canli"))
        root.after(1000, refresh)

    def poll():
        try:
            while True:
                m = cmd_q.get_nowait()
                kind = m[0]
                if kind == "ips":
                    with lock:
                        state["ips"] = list(m[1])
                elif kind == "host":
                    with lock:
                        state["hosts"][m[1]] = m[2]
                elif kind == "pending":
                    add_pending(m[1], m[2], m[3])
                elif kind == "clear":
                    remove_pending(m[1])
                elif kind == "done":
                    with lock:
                        state["done"] = True
                elif kind == "close":
                    stop.set()
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(200, poll)

    def on_close():
        stop.set()
        root.destroy()

    def on_resize(event):
        if event.widget is root:
            w = max(150, event.width - 30)
            head.config(wraplength=w)
            pend_title.config(wraplength=w)
            auto_chk.config(wraplength=w)

    root.bind("<Configure>", on_resize)
    root.protocol("WM_DELETE_WINDOW", on_close)
    poll()
    refresh()
    tick()
    root.mainloop()
    stop.set()


# ============================ ANA PROSES TERAFI ============================
class Monitor:
    def __init__(self, ips):
        ctx = mp.get_context("spawn")
        self.cmd_q = ctx.Queue()
        self.ans_q = ctx.Queue()
        self.proc = ctx.Process(target=_monitor_main, args=(self.cmd_q, self.ans_q), daemon=True)
        self.proc.start()
        self.cmd_q.put(("ips", list(ips)))

    def alive(self):
        return self.proc.is_alive()

    def set_host(self, ip, host):
        self.cmd_q.put(("host", ip, host))

    def ask_confirm(self, ip, host, expire, margin=3):
        """expire = cihazin commit confirmed-i geri alacagi an (epoch).
        'confirm' | 'rollback' | 'timeout' | None (pencere baglanib)"""
        deadline = expire - margin
        self.cmd_q.put(("pending", ip, host, expire))
        result = "timeout"
        while time.time() < deadline:
            if not self.alive():
                return None
            try:
                kind, who = self.ans_q.get(timeout=0.3)
            except queue.Empty:
                continue
            if who == ip:
                result = kind
                break
        self.cmd_q.put(("clear", ip))
        return result

    def done(self):
        if self.alive():
            self.cmd_q.put(("done",))

    def close(self):
        if self.alive():
            self.cmd_q.put(("close",))