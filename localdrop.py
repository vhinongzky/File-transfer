#!/usr/bin/env python3
"""
LocalDrop - wireless file transfer between a desktop and iPhone/Android.

Run on your desktop (Windows, macOS or Linux), scan the QR code with your phone
(on the same Wi-Fi), and use the page that opens to send and receive files.
No app is needed on the phone.

Usage:
    python localdrop.py                  # shares ./shared
    python localdrop.py ~/Pictures       # shares a chosen folder
    python localdrop.py -p 9000          # use another port

Optional (for a QR code in the terminal):  pip install qrcode
"""
import argparse
import html
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK = 1024 * 1024
TOKEN = secrets.token_urlsafe(6)
ROOT = "."
ON_RECEIVE = None  # optional callback(name, size_bytes), used by the desktop app

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LocalDrop</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--text:#1b1d21;--mute:#6b7280;--acc:#2563eb;--line:#e5e7eb}
@media(prefers-color-scheme:dark){:root{--bg:#111316;--card:#1b1e23;--text:#f1f2f4;--mute:#9aa1ab;--acc:#60a5fa;--line:#2a2e35}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.4 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:16px}
#drop{border:2px dashed var(--line);text-align:center;padding:28px 12px;border-radius:12px;color:var(--mute)}
#drop.over{border-color:var(--acc);color:var(--acc)}
button,label.btn{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:16px;display:inline-block;margin-top:10px}
input[type=file]{display:none}
.row{display:flex;align-items:center;gap:12px;padding:10px 0;border-top:1px solid var(--line)}
.row:first-child{border-top:0}
.thumb{width:48px;height:48px;border-radius:8px;background:var(--line);object-fit:cover;flex:none;display:flex;align-items:center;justify-content:center;font-size:20px}
.name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.size{color:var(--mute);font-size:13px}
a{color:var(--acc);text-decoration:none}
progress{width:100%;height:8px}
.up{padding:6px 0;font-size:14px}
.empty{color:var(--mute);text-align:center;padding:12px}
</style></head><body><main>
<h1>LocalDrop</h1>
<div class="card">
  <div id="drop">
    <div>Send files to the computer</div>
    <label class="btn">Choose photos / videos / files
      <input id="pick" type="file" multiple></label>
    <div style="margin-top:8px;font-size:13px">or drop files here</div>
  </div>
  <div id="uploads"></div>
</div>
<div class="card">
  <strong>On the computer</strong>
  <div id="list"></div>
  <div class="size" style="margin-top:8px">Tip: on iPhone, open a photo or video, then touch and hold (or tap Share) and choose Save to Photos. On Android, tap Download.</div>
</div>
<script>
const K=%TOKEN%;
const q=s=>document.querySelector(s);
function fmt(n){const u=['B','KB','MB','GB'];let i=0;while(n>=1024&&i<3){n/=1024;i++}return n.toFixed(i?1:0)+' '+u[i]}
function esc(s){return s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function refresh(){
  try{
    const r=await fetch('/api/list?k='+K);const files=await r.json();
    q('#list').innerHTML=files.length?files.map(f=>{
      const url='/files/'+encodeURIComponent(f.name)+'?k='+K;
      const isImg=/\.(jpe?g|png|gif|webp|bmp)$/i.test(f.name);
      const th=isImg?`<img class="thumb" loading="lazy" src="${url}">`:`<div class="thumb">${/\.(mp4|mov|m4v|webm|mkv)$/i.test(f.name)?'🎬':'📄'}</div>`;
      return `<div class="row">${th}<div class="name"><a href="${url}">${esc(f.name)}</a><div class="size">${fmt(f.size)}</div></div><a href="${url}&dl=1" download="${esc(f.name)}">Download</a></div>`}).join(''):'<div class="empty">No files yet. Put files in the shared folder on your computer.</div>';
  }catch(e){}
}
function upload(file){
  const el=document.createElement('div');el.className='up';
  el.innerHTML=`<div>${esc(file.name)} <span class="size">${fmt(file.size)}</span></div><progress max="100" value="0"></progress><div class="size st">Uploading...</div>`;
  q('#uploads').prepend(el);
  const x=new XMLHttpRequest();
  x.open('POST','/api/upload?k='+K+'&name='+encodeURIComponent(file.name));
  x.upload.onprogress=e=>{if(e.lengthComputable)el.querySelector('progress').value=e.loaded/e.total*100};
  x.onload=()=>{el.querySelector('.st').textContent=x.status==200?'Done':'Failed ('+x.status+')';refresh()};
  x.onerror=()=>{el.querySelector('.st').textContent='Failed'};
  x.send(file);
}
function addFiles(fs){[...fs].forEach(upload)}
q('#pick').onchange=e=>{addFiles(e.target.files);e.target.value=''};
const d=q('#drop');
['dragenter','dragover'].forEach(t=>d.addEventListener(t,e=>{e.preventDefault();d.classList.add('over')}));
['dragleave','drop'].forEach(t=>d.addEventListener(t,e=>{e.preventDefault();d.classList.remove('over')}));
d.addEventListener('drop',e=>addFiles(e.dataTransfer.files));
refresh();setInterval(refresh,4000);
</script></main></body></html>
"""


def safe_name(name):
    name = os.path.basename(urllib.parse.unquote(name).replace("\\", "/"))
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(". ")
    return name or "file"


def unique_path(name):
    base, ext = os.path.splitext(name)
    path = os.path.join(ROOT, name)
    i = 1
    while os.path.exists(path):
        path = os.path.join(ROOT, f"{base} ({i}){ext}")
        i += 1
    return path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if sys.stderr:  # no console when packaged as a windowed app
            sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def _auth(self, qs):
        if qs.get("k", [""])[0] == TOKEN:
            return True
        return TOKEN in self.headers.get("Cookie", "")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if not self._auth(qs):
            return self._send(403, b"Forbidden. Scan the QR code shown on the computer.")
        cookie = {"Set-Cookie": f"ld={TOKEN}; Path=/; SameSite=Lax"}
        if u.path == "/":
            page = PAGE.replace("%TOKEN%", json.dumps(TOKEN)).encode()
            return self._send(200, page, "text/html; charset=utf-8", cookie)
        if u.path == "/api/list":
            items = []
            for n in sorted(os.listdir(ROOT), key=str.lower):
                p = os.path.join(ROOT, n)
                if os.path.isfile(p) and not n.startswith("."):
                    items.append({"name": n, "size": os.path.getsize(p)})
            return self._send(200, json.dumps(items).encode(), "application/json")
        if u.path.startswith("/files/"):
            return self._serve_file(safe_name(u.path[len("/files/"):]), "dl" in qs)
        self._send(404, b"Not found")

    def _serve_file(self, name, download):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            return self._send(404, b"Not found")
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                s, e = m.groups()
                if s == "" and e:
                    start = max(0, size - int(e))
                elif s:
                    start = int(s)
                    if e:
                        end = min(int(e), size - 1)
                if start > end or start >= size:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            q = urllib.parse.quote(name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{q}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if not self._auth(qs):
            return self._send(403, b"Forbidden")
        if u.path != "/api/upload":
            return self._send(404, b"Not found")
        name = safe_name(qs.get("name", ["file"])[0])
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, b"Bad length")
        path = unique_path(name)
        tmp = path + ".part"
        try:
            with open(tmp, "wb") as f:
                left = length
                while left > 0:
                    buf = self.rfile.read(min(CHUNK, left))
                    if not buf:
                        raise ConnectionError("client disconnected")
                    f.write(buf)
                    left -= len(buf)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            self.close_connection = True
            return
        print(f"  Received: {os.path.basename(path)} ({length} bytes)")
        if ON_RECEIVE:
            ON_RECEIVE(os.path.basename(path), length)
        self._send(200, b"ok")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="Wireless file transfer for desktop <-> iPhone/Android")
    ap.add_argument("folder", nargs="?", default="shared", help="folder to share (default: ./shared)")
    ap.add_argument("-p", "--port", type=int, default=8080)
    a = ap.parse_args()
    ROOT = os.path.abspath(a.folder)
    os.makedirs(ROOT, exist_ok=True)

    url = f"http://{lan_ip()}:{a.port}/?k={TOKEN}"
    print("\n  LocalDrop is running")
    print(f"  Shared folder: {ROOT}")
    print(f"  Open on your phone (same Wi-Fi): {url}\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (Install the qrcode package to show a scannable QR code: pip install qrcode)")
    print("\n  Press Ctrl+C to stop.\n")
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
