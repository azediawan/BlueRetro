# SPDX-License-Identifier: Apache-2.0
"""One window, one cable, three jobs: watch, configure, capture.

Tkinter on purpose. It ships with Python, so this runs on the work machine with
only pyserial added, and the theme is hand built because the stock one looks
like 1998. None of the styling leaks into the layers underneath.

The serial port is shared: the monitor thread owns it while running, and any
command pauses that thread for the round trip. One owner at a time, always.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from . import command as C
from .protocol import AdapterState, Kind, State, parse_line
from .reader import PortReader, find_adapters
from .trace import decode

# Same palette as the blink dictionary, so the tools read as one kit.
GROUND, SURFACE, RAISED, HAIRLINE = "#0E1116", "#161A21", "#1B2029", "#252B35"
INK, INK_SOFT, INK_MUTE = "#DDE3EC", "#A3AEBF", "#7C8798"
BLUE, RED, GREEN, AMBER = "#3B9EFF", "#FF4D3D", "#3DD68C", "#FFB020"

STATE_COLOR = {
    State.UNKNOWN: INK_MUTE, State.IDLE: INK_SOFT, State.SEARCHING: BLUE,
    State.CONNECTING: AMBER, State.PAIRED: AMBER, State.READY: GREEN, State.FAILED: RED,
}
KIND_COLOR = {
    Kind.CHANNEL_REFUSED: RED, Kind.ERROR: RED, Kind.HID_READY: GREEN,
    Kind.DEVICE_SKIPPED: AMBER, Kind.PAIRED: BLUE, Kind.ENCRYPTED: BLUE,
}

SANS, SANS_B = ("Segoe UI", 10), ("Segoe UI Semibold", 10)
BIG, LABEL, MONO = ("Segoe UI Semibold", 19), ("Segoe UI", 8), ("Consolas", 9)


def theme(root: tk.Misc) -> None:
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=GROUND, foreground=INK, font=SANS, borderwidth=0)
    s.configure("TFrame", background=GROUND)
    s.configure("Card.TFrame", background=SURFACE)
    s.configure("TLabel", background=GROUND, foreground=INK)
    s.configure("Port.TLabel", background=SURFACE, foreground=INK, font=SANS_B)
    s.configure("Mono.TLabel", background=SURFACE, foreground=INK_MUTE, font=MONO)
    s.configure("Note.TLabel", background=SURFACE, foreground=INK_MUTE)
    s.configure("Field.TLabel", background=SURFACE, foreground=INK_MUTE, font=LABEL)
    s.configure("Value.TLabel", background=SURFACE, foreground=INK, font=SANS_B)
    s.configure("Sub.TLabel", background=SURFACE, foreground=INK_MUTE, font=LABEL)
    s.configure("Title.TLabel", foreground=INK, font=("Segoe UI Semibold", 13))
    s.configure("Hint.TLabel", foreground=INK_MUTE, font=LABEL)
    s.configure("Warn.TLabel", foreground=AMBER, font=LABEL)
    s.configure("TButton", background=RAISED, foreground=INK, padding=(12, 6), relief="flat")
    s.map("TButton", background=[("active", HAIRLINE)], foreground=[("disabled", INK_MUTE)])
    s.configure("TCheckbutton", background=GROUND, foreground=INK_SOFT)
    s.map("TCheckbutton", background=[("active", GROUND)])
    s.configure("TNotebook", background=GROUND, borderwidth=0, tabmargins=(0, 6, 0, 0))
    s.configure("TNotebook.Tab", background=GROUND, foreground=INK_MUTE,
                padding=(18, 9), font=SANS, borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", SURFACE)],
          foreground=[("selected", INK)])
    s.configure("TCombobox", fieldbackground=RAISED, background=RAISED, foreground=INK,
                arrowcolor=INK_SOFT, selectbackground=RAISED, selectforeground=INK)


class AdapterPanel(ttk.Frame):
    """Live state of one adapter, rebuilt from what it prints."""

    def __init__(self, master, port: str):
        super().__init__(master, style="Card.TFrame", padding=(16, 14))
        self.st = AdapterState(port=port)

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=port, style="Port.TLabel").pack(side="left")
        self.addr = ttk.Label(head, text="—", style="Mono.TLabel")
        self.addr.pack(side="right")

        self.state_lbl = tk.Label(self, text="sem contato", font=BIG, bg=SURFACE,
                                  fg=INK_MUTE, anchor="w")
        self.state_lbl.pack(fill="x", pady=(10, 0))
        self.note = ttk.Label(self, text="aguardando", style="Note.TLabel", anchor="w")
        self.note.pack(fill="x", pady=(1, 12))

        ttk.Label(self, text="CONTROLE", style="Field.TLabel", anchor="w").pack(fill="x")
        self.ctrl = ttk.Label(self, text="nenhum", style="Value.TLabel", anchor="w")
        self.ctrl.pack(fill="x")
        self.ctrl_sub = ttk.Label(self, text="", style="Sub.TLabel", anchor="w")
        self.ctrl_sub.pack(fill="x", pady=(0, 12))

        ttk.Label(self, text="ÚLTIMOS EVENTOS", style="Field.TLabel", anchor="w").pack(fill="x")
        self.tl = tk.Text(self, height=8, bg=RAISED, fg=INK_SOFT, font=MONO, bd=0,
                          highlightthickness=0, wrap="none", padx=10, pady=8,
                          state="disabled", cursor="arrow")
        self.tl.pack(fill="both", expand=True)
        for kind, color in KIND_COLOR.items():
            self.tl.tag_configure(kind.name, foreground=color)

    def feed(self, line: str) -> None:
        ev = parse_line(line)
        if not (ev and self.st.apply(ev)):
            return
        s = self.st
        self.state_lbl.config(text=s.state.value, fg=STATE_COLOR[s.state])
        self.note.config(text=s.note or "—")
        self.addr.config(text=s.adapter_addr or "—")
        if s.ctrl_addr or s.ctrl_name:
            self.ctrl.config(text=s.ctrl_name or s.ctrl_addr)
            bits = [b for b in (s.ctrl_type, s.ctrl_addr if s.ctrl_name else None,
                                "criptografado" if s.encrypted else None) if b]
            self.ctrl_sub.config(text="  ·  ".join(bits))
        else:
            self.ctrl.config(text="nenhum")
            self.ctrl_sub.config(text=f"{s.skipped} ignorados" if s.skipped else "")

        if ev.kind is not Kind.NOISE and s.timeline:
            kind, text = s.timeline[-1]
            self.tl.config(state="normal")
            self.tl.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n", kind.name)
            self.tl.see("end")
            self.tl.config(state="disabled")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BlueRetro Monitor")
        self.geometry("1200x820")
        self.minsize(980, 640)
        self.configure(bg=GROUND)
        theme(self)

        self.q: queue.Queue = queue.Queue()
        # Worker threads hand results back through here. Tkinter is not thread
        # safe, not even after(): calling it off the main thread works by luck
        # until it does not.
        self.jobs: queue.Queue = queue.Queue()
        self.readers: dict[str, PortReader] = {}
        self.panels: dict[str, AdapterPanel] = {}
        self.ports = [d for d, _ in find_adapters()]
        self.paused = False
        self.only_events = tk.BooleanVar(value=False)
        self.busy = False

        self._chrome()
        self._tab_monitor()
        self._tab_config()
        self._tab_trace()
        for p in self.ports:
            r = PortReader(p, self.q)
            r.start()
            self.readers[p] = r
        self.after(60, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # --- shell ----------------------------------------------------------------

    def _chrome(self) -> None:
        top = ttk.Frame(self, padding=(18, 16, 18, 4))
        top.pack(fill="x")
        ttk.Label(top, text="BlueRetro Monitor", style="Title.TLabel").pack(side="left")
        self.status = ttk.Label(top, text=f"{len(self.ports)} adaptador(es) por USB",
                                style="Hint.TLabel")
        self.status.pack(side="left", padx=(12, 0))
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(6, 18))

    def _page(self, title: str) -> ttk.Frame:
        f = ttk.Frame(self.nb, padding=(0, 14))
        self.nb.add(f, text=title)
        return f

    # --- monitor --------------------------------------------------------------

    def _tab_monitor(self) -> None:
        page = self._page("Monitor")
        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 12))
        self.btn_pause = ttk.Button(bar, text="Pausar", command=self._toggle, width=10)
        self.btn_pause.pack(side="right")
        ttk.Button(bar, text="Salvar log", command=self._save_log).pack(side="right", padx=6)
        ttk.Checkbutton(bar, text="só eventos", variable=self.only_events).pack(side="right", padx=10)

        cards = ttk.Frame(page)
        cards.pack(fill="x")
        if not self.ports:
            ttk.Label(cards, text="Nenhum adaptador USB serial encontrado.",
                      style="Hint.TLabel").pack(pady=30)
        for i, port in enumerate(self.ports):
            panel = AdapterPanel(cards, port)
            panel.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            cards.columnconfigure(i, weight=1, uniform="a")
            self.panels[port] = panel

        ttk.Label(page, text="CONSOLE", style="Hint.TLabel").pack(anchor="w", pady=(14, 4))
        wrap = tk.Frame(page, bg=HAIRLINE, padx=1, pady=1)
        wrap.pack(fill="both", expand=True)
        self.log = tk.Text(wrap, bg=SURFACE, fg=INK_SOFT, font=MONO, bd=0, wrap="none",
                           highlightthickness=0, padx=12, pady=10, state="disabled")
        sb = ttk.Scrollbar(wrap, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("sys", foreground=AMBER)
        self.log.tag_configure("noise", foreground=INK_MUTE)
        self.log.tag_configure("port", foreground=BLUE)
        self.log.tag_configure("reply", foreground=GREEN)
        for kind, color in KIND_COLOR.items():
            self.log.tag_configure(kind.name, foreground=color)

    # --- config ---------------------------------------------------------------

    def _tab_config(self) -> None:
        page = self._page("Configuração")
        if not self.ports:
            ttk.Label(page, text="Nenhum adaptador.", style="Hint.TLabel").pack(pady=30)
            return

        sel = ttk.Frame(page)
        sel.pack(fill="x", pady=(0, 14))
        ttk.Label(sel, text="Adaptador").pack(side="left", padx=(0, 8))
        self.cfg_port = ttk.Combobox(sel, values=self.ports, state="readonly", width=10)
        self.cfg_port.current(0)
        self.cfg_port.pack(side="left")
        ttk.Button(sel, text="Ler do adaptador", command=self._cfg_read).pack(side="left", padx=10)

        card = ttk.Frame(page, style="Card.TFrame", padding=(18, 16))
        card.pack(fill="x")
        self.cfg_ver = ttk.Label(card, text="—", style="Mono.TLabel")
        self.cfg_ver.pack(anchor="w", pady=(0, 14))

        self.fields: dict[str, ttk.Combobox] = {}
        for label, key, values in (
                ("Busca por controle", "inquiry", ["auto", "manual"]),
                ("Banco do cartão", "bank", ["0", "1", "2", "3", "debug"]),
                ("Multitap", "multitap", ["0", "1", "2", "3", "4"]),
                ("Sistema", "system", [str(i) for i in range(0, 24)])):
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="Value.TLabel", width=20,
                      anchor="w").pack(side="left")
            cb = ttk.Combobox(row, values=values, state="readonly", width=14)
            cb.pack(side="left")
            self.fields[key] = cb

        self.cfg_warn = ttk.Label(card, text="", style="Warn.TLabel")
        self.cfg_warn.pack(anchor="w", pady=(12, 0))

        act = ttk.Frame(page)
        act.pack(fill="x", pady=14)
        ttk.Button(act, text="Aplicar e salvar", command=self._cfg_write).pack(side="left")
        ttk.Button(act, text="Reiniciar adaptador", command=self._reboot).pack(side="left", padx=8)
        self.cfg_msg = ttk.Label(act, text="", style="Hint.TLabel")
        self.cfg_msg.pack(side="left", padx=12)

        ttk.Label(page, style="Hint.TLabel", wraplength=780, justify="left",
                  text="Alterações ficam na memória do adaptador até salvar, então um valor "
                       "errado não custa nada. Salvar grava na memória permanente e sobrevive "
                       "ao reinício.").pack(anchor="w")

    # --- trace ----------------------------------------------------------------

    def _tab_trace(self) -> None:
        page = self._page("Trace")
        if not self.ports:
            ttk.Label(page, text="Nenhum adaptador.", style="Hint.TLabel").pack(pady=30)
            return

        bar = ttk.Frame(page)
        bar.pack(fill="x", pady=(0, 12))
        ttk.Label(bar, text="Adaptador").pack(side="left", padx=(0, 8))
        self.tr_port = ttk.Combobox(bar, values=self.ports, state="readonly", width=10)
        self.tr_port.current(0)
        self.tr_port.pack(side="left")
        ttk.Button(bar, text="Baixar e decodificar", command=self._trace_get).pack(side="left", padx=10)
        ttk.Button(bar, text="Abrir arquivo", command=self._trace_open).pack(side="left")
        ttk.Button(bar, text="Salvar .bin", command=self._trace_save).pack(side="left", padx=6)
        self.tr_msg = ttk.Label(bar, text="", style="Hint.TLabel")
        self.tr_msg.pack(side="left", padx=12)

        wrap = tk.Frame(page, bg=HAIRLINE, padx=1, pady=1)
        wrap.pack(fill="both", expand=True)
        self.tr_text = tk.Text(wrap, bg=SURFACE, fg=INK_SOFT, font=MONO, bd=0, wrap="none",
                               highlightthickness=0, padx=12, pady=10, state="disabled")
        sb = ttk.Scrollbar(wrap, command=self.tr_text.yview)
        self.tr_text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tr_text.pack(fill="both", expand=True)
        self.tr_text.tag_configure("hit", foreground=RED)
        self.tr_text.tag_configure("note", foreground=BLUE)
        self.tr_raw: bytes = b""

    # --- one owner of the port at a time --------------------------------------

    def _with_port(self, port: str, work, done) -> None:
        """Pause the monitor on that port, run `work(serial)`, hand the result to
        `done` back on the UI thread. Two owners on one port means garbled reads.
        """
        if self.busy:
            return
        self.busy = True
        reader = self.readers.get(port)
        if reader:
            reader.stop()

        def run():
            result = err = None
            try:
                s = C.open_port(port)
                try:
                    result = work(s)
                finally:
                    s.close()
            except Exception as e:                      # noqa: BLE001 - surfaced to the user
                err = e
            self.jobs.put((port, result, err, done))

        threading.Thread(target=run, daemon=True).start()

    def _finish(self, port, result, err, done) -> None:
        r = PortReader(port, self.q)
        r.start()
        self.readers[port] = r
        self.busy = False
        done(result, err)

    # --- config actions -------------------------------------------------------

    def _cfg_read(self) -> None:
        port = self.cfg_port.get()
        self.cfg_msg.config(text="lendo...")

        def work(s):
            return C.version(s), C.get_config(s)

        def done(res, err):
            if err or not res or not res[1]:
                self.cfg_msg.config(text=f"falhou: {err or 'sem resposta'}")
                return
            (ver, name), cfg = res
            self.cfg_ver.config(text=f"{name}   {ver}")
            self.fields["inquiry"].set(cfg.inquiry_name)
            self.fields["bank"].set(cfg.bank_name)
            self.fields["multitap"].set(str(cfg.multitap))
            self.fields["system"].set(str(cfg.system))
            self.cfg_warn.config(text=(
                "Esta build ignora a busca guardada e fica sempre em manual."
                if cfg.lock_inquiry else ""))
            self.cfg_msg.config(text="lido")

        self._with_port(port, work, done)

    def _cfg_write(self) -> None:
        port = self.cfg_port.get()
        wanted = {k: cb.get() for k, cb in self.fields.items() if cb.get()}
        if not wanted:
            self.cfg_msg.config(text="leia primeiro")
            return
        self.cfg_msg.config(text="gravando...")

        def work(s):
            bad = [k for k, v in wanted.items() if not C.set_field(s, k, v)[0]]
            return bad, C.save(s)

        def done(res, err):
            if err:
                self.cfg_msg.config(text=f"falhou: {err}")
                return
            bad, saved = res
            if bad:
                self.cfg_msg.config(text=f"recusou: {', '.join(bad)}")
            else:
                self.cfg_msg.config(text="salvo" if saved else "aplicado, mas não salvou")

        self._with_port(port, work, done)

    def _reboot(self) -> None:
        port = self.cfg_port.get()
        if not messagebox.askyesno("Reiniciar", f"Reiniciar o adaptador em {port}?"):
            return
        self._with_port(port, lambda s: C.send(s, "reboot", quiet_for=0.3),
                        lambda r, e: self.cfg_msg.config(text=f"falhou: {e}" if e else "reiniciado"))

    # --- trace actions --------------------------------------------------------

    def _trace_get(self) -> None:
        port = self.tr_port.get()
        self.tr_msg.config(text="baixando 128 KB...")
        self._with_port(port, C.dump_trace, self._trace_done)

    def _trace_done(self, data, err) -> None:
        if err:
            self.tr_msg.config(text=f"falhou: {err}")
            return
        self.tr_raw = data
        self.tr_msg.config(text=f"{len(data):,} bytes")
        self._trace_render(data)

    def _trace_open(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Trace", "*.bin"), ("Tudo", "*.*")])
        if path:
            self.tr_raw = open(path, "rb").read()
            self.tr_msg.config(text=f"{len(self.tr_raw):,} bytes de arquivo")
            self._trace_render(self.tr_raw)

    def _trace_save(self) -> None:
        if not self.tr_raw:
            self.tr_msg.config(text="nada para salvar")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".bin",
            initialfile=f"trace-{datetime.now():%Y%m%d-%H%M%S}.bin")
        if path:
            open(path, "wb").write(self.tr_raw)
            self.tr_msg.config(text="salvo")

    def _trace_render(self, data: bytes) -> None:
        import tempfile, os
        fd, tmp = tempfile.mkstemp(suffix=".bin")
        os.write(fd, data)
        os.close(fd)
        try:
            rows, summary = decode(tmp)
        finally:
            os.unlink(tmp)

        self.tr_text.config(state="normal")
        self.tr_text.delete("1.0", "end")
        self.tr_text.insert("end", summary + "\n\n", "note")
        for ts, tag, text in rows:
            line = f"[{ts / 1e6:9.3f}] {tag:10} {text}\n"
            hit = "hit" if ("**" in text or "NOTE" in tag) else ""
            self.tr_text.insert("end", line, "note" if tag == "NOTE" else hit)
        self.tr_text.config(state="disabled")

    # --- monitor plumbing -----------------------------------------------------

    def _drain(self) -> None:
        while True:
            try:
                port, result, err, done = self.jobs.get_nowait()
            except queue.Empty:
                break
            self._finish(port, result, err, done)

        for _ in range(600):
            try:
                port, tag, text = self.q.get_nowait()
            except queue.Empty:
                break
            if self.paused:
                continue
            if tag == "sys":
                self._write(port, text, "sys")
                continue
            panel = self.panels.get(port)
            kind = Kind.NOISE
            if panel:
                panel.feed(text)
                ev = parse_line(text)
                kind = ev.kind if ev else Kind.NOISE
            if text.startswith("+"):
                self._write(port, text, "reply")
                continue
            if kind is Kind.NOISE and self.only_events.get():
                continue
            self._write(port, text, KIND_COLOR.get(kind, "noise"))
        self.after(60, self._drain)

    def _write(self, port: str, text: str, tag) -> None:
        self.log.config(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S} ", "noise")
        self.log.insert("end", f"{port:6}", "port")
        self.log.insert("end", f" {text}\n", tag if isinstance(tag, str) else (tag or ""))
        if int(self.log.index("end-1c").split(".")[0]) > 4000:
            self.log.delete("1.0", "1500.0")
        self.log.see("end")
        self.log.config(state="disabled")

    def _toggle(self) -> None:
        self.paused = not self.paused
        self.btn_pause.config(text="Retomar" if self.paused else "Pausar")

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".log", initialfile=f"brmon-{datetime.now():%Y%m%d-%H%M%S}.log")
        if path:
            open(path, "w", encoding="utf-8").write(self.log.get("1.0", "end"))

    def _close(self) -> None:
        for r in self.readers.values():
            r.stop()
        self.destroy()


def main() -> None:
    App().mainloop()
