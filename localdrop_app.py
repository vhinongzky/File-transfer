#!/usr/bin/env python3
"""
LocalDrop (desktop app) - send photos, videos and files between this computer
and an iPhone or Android phone over Wi-Fi. No cables, no app on the phone.

Double-click to run (needs Python), or build a standalone app with the
build_windows.bat / build_mac.command scripts.
"""
import os
import platform
import queue
import secrets
import subprocess
import sys
import threading
import tkinter as tk
from http.server import ThreadingHTTPServer
from tkinter import filedialog, messagebox, ttk

import localdrop

try:
    import qrcode
except ImportError:
    qrcode = None

DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads", "LocalDrop")


class Server:
    def __init__(self):
        self.httpd = None

    @property
    def running(self):
        return self.httpd is not None

    def start(self, folder, port=8080):
        os.makedirs(folder, exist_ok=True)
        localdrop.ROOT = folder
        localdrop.TOKEN = secrets.token_urlsafe(6)  # new secret every start
        err = None
        for p in range(port, port + 20):
            try:
                self.httpd = ThreadingHTTPServer(("0.0.0.0", p), localdrop.Handler)
                break
            except OSError as e:
                err = e
        else:
            raise err
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://{localdrop.lan_ip()}:{p}/?k={localdrop.TOKEN}"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def open_folder(path):
    if platform.system() == "Windows":
        os.startfile(path)  # noqa
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LocalDrop")
        self.resizable(False, False)
        self.server = Server()
        self.folder = DEFAULT_FOLDER
        self.url = ""
        self.events = queue.Queue()
        localdrop.ON_RECEIVE = lambda name, size: self.events.put((name, size))
        self._qr_img = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(300, self._poll)
        self.after(100, self.start)

    def _build(self):
        pad = {"padx": 16}
        ttk.Label(self, text="LocalDrop", font=("", 18, "bold")).pack(pady=(14, 0), **pad)
        self.status = ttk.Label(self, text="Starting...", foreground="#6b7280")
        self.status.pack(**pad)

        self.qr_label = ttk.Label(self)
        self.qr_label.pack(pady=10, **pad)

        ttk.Label(self, text="1. Connect your phone to the same Wi-Fi as this computer\n"
                             "2. Scan the code with your phone's camera and open the link",
                  justify="left").pack(**pad)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(10, 0), **pad)
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var, state="readonly", width=44).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Copy link", command=self._copy).pack(side="left", padx=(6, 0))

        frow = ttk.Frame(self)
        frow.pack(fill="x", pady=(14, 0), **pad)
        ttk.Label(frow, text="Files are saved in:").pack(anchor="w")
        self.folder_var = tk.StringVar(value=self.folder)
        ttk.Label(frow, textvariable=self.folder_var, foreground="#2563eb", wraplength=380).pack(anchor="w")
        brow = ttk.Frame(frow)
        brow.pack(anchor="w", pady=(4, 0))
        ttk.Button(brow, text="Open folder", command=lambda: open_folder(self.folder)).pack(side="left")
        ttk.Button(brow, text="Change folder...", command=self._change_folder).pack(side="left", padx=6)
        self.toggle = ttk.Button(brow, text="Stop", command=self._toggle)
        self.toggle.pack(side="left")

        ttk.Label(self, text="Received from phone:").pack(anchor="w", pady=(14, 0), **pad)
        self.log = tk.Listbox(self, height=5, width=60, activestyle="none")
        self.log.pack(pady=(2, 14), **pad)

    # ---- actions
    def start(self):
        try:
            self.url = self.server.start(self.folder)
        except Exception as e:
            self.status.config(text="Could not start", foreground="#dc2626")
            messagebox.showerror("LocalDrop", f"Could not start the server:\n{e}")
            return
        self.url_var.set(self.url)
        self.status.config(text="Running - ready for your phone", foreground="#16a34a")
        self.toggle.config(text="Stop")
        self._draw_qr(self.url)

    def stop(self):
        self.server.stop()
        self.status.config(text="Stopped", foreground="#6b7280")
        self.toggle.config(text="Start")
        self.qr_label.config(image="", text="")
        self.url_var.set("")

    def _toggle(self):
        self.stop() if self.server.running else self.start()

    def _change_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder, title="Choose a folder for transferred files")
        if d:
            self.folder = d
            self.folder_var.set(d)
            if self.server.running:
                self.stop()
                self.start()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.url)
        self.status.config(text="Link copied", foreground="#16a34a")

    def _draw_qr(self, url):
        if qrcode is None:
            self.qr_label.config(image="", text="(QR code unavailable - type the link below on your phone)")
            return
        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        img = tk.PhotoImage(width=n, height=n)
        for y, row in enumerate(m):
            img.put("{" + " ".join("#000000" if c else "#ffffff" for c in row) + "}", to=(0, y))
        scale = max(1, 260 // n)
        self._qr_img = img.zoom(scale, scale)
        self.qr_label.config(image=self._qr_img, text="")

    def _poll(self):
        try:
            while True:
                name, size = self.events.get_nowait()
                self.log.insert(0, f"{name}  ({fmt_size(size)})")
        except queue.Empty:
            pass
        self.after(300, self._poll)

    def _close(self):
        self.server.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
