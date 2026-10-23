#!/usr/bin/env python3
"""Serve the club site on the local network and show a QR-code landing page."""

from __future__ import annotations

import argparse
import html
import http.server
import ipaddress
import io
import json
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "docs"
QR_SCRIPT = ROOT / "local-share" / "qrcode.min.js"
VENDOR_DIR = ROOT / "local-share" / "vendor"
sys.path.insert(0, str(VENDOR_DIR))

import qrcode  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402


def valid_lan_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip())
        return address.version == 4 and not address.is_loopback and not address.is_link_local
    except ValueError:
        return False


def find_lan_ip() -> str:
    # On macOS, en0 is normally Wi-Fi; some models use en1.
    for interface in ("en0", "en1"):
        try:
            result = subprocess.run(
                ["ipconfig", "getifaddr", interface],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            candidate = result.stdout.strip()
            if valid_lan_ip(candidate):
                return candidate
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    # This chooses the address macOS would use for ordinary network traffic.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        candidate = probe.getsockname()[0]
        if valid_lan_ip(candidate):
            return candidate
    except OSError:
        pass
    finally:
        probe.close()

    return "127.0.0.1"


def find_available_port(preferred: int, attempts: int = 100) -> int:
    """Return the first port that is free on every local IPv4 address."""
    if not 1024 <= preferred <= 65535:
        raise SystemExit("端口号需在 1024 到 65535 之间。")

    last_port = min(preferred + attempts, 65536)
    for port in range(preferred, last_port):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Do not enable SO_REUSEADDR here. A loopback-only service can
            # otherwise silently shadow requests sent to 127.0.0.1.
            probe.bind(("0.0.0.0", port))
        except OSError:
            continue
        finally:
            probe.close()
        return port

    raise SystemExit(f"未找到可用端口（已检查 {preferred} 至 {last_port - 1}）。")


def share_page(site_url: str) -> bytes:
    safe_url = html.escape(site_url)
    js_url = json.dumps(site_url)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI交叉科学社 · 局域网分享</title>
  <style>
    :root {{ color-scheme: light; --paper:#f8f5ee; --ink:#302d26; --accent:#ae5035; --line:#ded8cc; }}
    * {{ box-sizing:border-box; }}
    body {{ min-height:100vh; margin:0; display:grid; place-items:center; padding:32px; background:var(--paper); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif; }}
    main {{ width:min(760px,100%); display:grid; grid-template-columns:1fr 300px; gap:54px; align-items:center; }}
    .eyebrow {{ color:var(--accent); font-size:12px; letter-spacing:2px; font-weight:700; }}
    h1 {{ margin:18px 0; font-family:"Songti SC","STSong",serif; font-size:46px; font-weight:500; line-height:1.25; }}
    p {{ color:#787065; line-height:1.9; }}
    code {{ display:block; overflow-wrap:anywhere; margin:25px 0; padding:15px; border:1px solid var(--line); border-radius:6px; color:var(--accent); background:#fffaf2; font-size:14px; }}
    a {{ display:inline-block; padding:14px 20px; border-radius:4px; color:white; background:var(--accent); text-decoration:none; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .actions a:last-child {{ color:var(--accent); background:transparent; border:1px solid var(--accent); }}
    .qr-card {{ padding:25px; background:white; border:1px solid var(--line); border-radius:10px; text-align:center; box-shadow:0 18px 50px #4d38240f; }}
    #qrcode {{ display:flex; justify-content:center; }}
    #qrcode img,#qrcode canvas {{ max-width:100%; height:auto!important; }}
    .qr-card small {{ display:block; margin-top:17px; color:#787065; }}
    @media(max-width:700px) {{ main {{ grid-template-columns:1fr; }} h1 {{ font-size:36px; }} .qr-card {{ width:min(300px,100%); }} }}
  </style>
</head>
<body>
  <main>
    <section>
      <div class="eyebrow">LOCAL NETWORK / 局域网分享</div>
      <h1>手机扫码，<br>查看社团网页。</h1>
      <p>请让手机与这台电脑连接同一个 Wi-Fi，并保持启动程序的终端窗口开启。</p>
      <code>{safe_url}</code>
      <div class="actions"><a href="{safe_url}">在电脑上打开网站 →</a><a href="/__poster__/" target="_blank">打开 A4 二维码海报 →</a></div>
    </section>
    <section class="qr-card">
      <div id="qrcode" aria-label="网站访问二维码"></div>
      <small>使用手机相机或微信扫码</small>
    </section>
  </main>
  <script src="/__share__/qrcode.min.js"></script>
  <script>new QRCode(document.getElementById("qrcode"), {{ text: {js_url}, width: 250, height: 250, colorDark: "#302d26", colorLight: "#ffffff", correctLevel: QRCode.CorrectLevel.M }});</script>
</body>
</html>""".encode("utf-8")


def poster_font(size: int, serif: bool = False):
    candidates = (
        ["/System/Library/Fonts/Supplemental/Songti.ttc"]
        if serif
        else ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def build_poster(site_url: str) -> Image.Image:
    width, height = 2480, 3508
    paper, ink, accent = "#f8f5ee", "#302d26", "#ae5035"
    muted, line, soft = "#71695e", "#d9d0c2", "#dfd3bb"
    poster = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(poster)

    draw.ellipse((1860, -390, 2850, 600), fill="#e7b599")
    draw.text((2140, 150), "✳", font=poster_font(180, serif=True), fill=accent)

    logo = Image.open(SITE_DIR / "assets" / "aiisc-logo.png").convert("RGBA")
    logo.thumbnail((1000, 300), Image.Resampling.LANCZOS)
    poster.paste(logo, (200, 160), logo)

    draw.text((200, 520), "WESTLAKE UNIVERSITY  ·  AI INTERDISCIPLINARY SCIENCE CLUB", font=poster_font(31), fill=accent)
    draw.text((190, 640), "在交叉处，", font=poster_font(180, serif=True), fill=ink)
    draw.text((190, 860), "遇见新的可能。", font=poster_font(180, serif=True), fill=accent)
    intro_font = poster_font(42)
    draw.text((200, 1120), "让不同学科的好奇心在这里相遇。", font=intro_font, fill=muted)
    draw.text((200, 1185), "从每周讨论到创客大赛，一起探索 AI 与科研、生活和未来的连接。", font=intro_font, fill=muted)

    photo_box = (200, 1320, 2280, 2220)
    draw.rounded_rectangle((235, 1365, 2315, 2255), radius=75, fill=soft)
    photo = Image.open(SITE_DIR / "assets" / "photo-16.jpg").convert("RGB")
    photo = ImageOps.fit(photo, (photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]), method=Image.Resampling.LANCZOS, centering=(0.5, 0.46))
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width, photo.height), radius=75, fill=255)
    poster.paste(photo, photo_box[:2], mask)
    draw.rounded_rectangle((290, 2025, 1000, 2150), radius=10, fill="#fffaf2")
    draw.text((325, 2050), "一张桌子，无限种想法。", font=poster_font(52, serif=True), fill=ink)

    draw.line((200, 2360, 2280, 2360), fill=line, width=4)
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=16, border=4)
    qr.add_data(site_url)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color=ink, back_color="white").convert("RGB").resize((620, 620), Image.Resampling.NEAREST)
    draw.rounded_rectangle((200, 2460, 900, 3160), radius=24, fill="white", outline=line, width=4)
    poster.paste(qr_image, (240, 2500))

    text_x = 1030
    draw.text((text_x, 2480), "SCAN TO EXPLORE", font=poster_font(30), fill=accent)
    draw.text((text_x, 2560), "扫码走进", font=poster_font(92, serif=True), fill=ink)
    draw.text((text_x, 2680), "我们的活动现场", font=poster_font(92, serif=True), fill=ink)
    draw.text((text_x, 2860), "请连接与展示电脑相同的 Wi-Fi，", font=poster_font(36), fill=muted)
    draw.text((text_x, 2920), "使用手机相机或微信扫码访问。", font=poster_font(36), fill=muted)
    draw.text((text_x, 3020), site_url, font=poster_font(31), fill=accent)

    draw.text((200, 3380), "保持好奇 · 自由交流 · 共同创造", font=poster_font(28), fill="#898176")
    right_text = "AI × EVERYTHING"
    right_font = poster_font(28)
    right_box = draw.textbbox((0, 0), right_text, font=right_font)
    draw.text((2280 - (right_box[2] - right_box[0]), 3380), right_text, font=right_font, fill="#898176")
    return poster


def poster_files(site_url: str) -> tuple[bytes, bytes]:
    poster = build_poster(site_url)
    png_buffer = io.BytesIO()
    poster.save(png_buffer, format="PNG", optimize=True)
    pdf_buffer = io.BytesIO()
    poster.save(pdf_buffer, format="PDF", resolution=300.0)
    return png_buffer.getvalue(), pdf_buffer.getvalue()


def poster_page() -> bytes:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI交叉科学社 · A4 二维码海报</title>
  <style>
    *{box-sizing:border-box}html,body{margin:0;background:#e8e2d7;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.toolbar{position:sticky;top:0;z-index:2;display:flex;justify-content:center;gap:12px;padding:14px;background:#302d26ee}.toolbar a{padding:11px 19px;border-radius:4px;background:#ae5035;color:white;text-decoration:none;font-size:14px}.toolbar a:last-child{background:#fff3}.poster{display:block;width:min(210mm,100%);height:auto;margin:24px auto;box-shadow:0 18px 60px #39271b24}@media(max-width:700px){.poster{margin:0}}
  </style>
</head>
<body>
  <nav class="toolbar"><a href="/__poster__/download.pdf" download>下载 A4 海报 PDF</a><a href="/__share__/">返回二维码页面</a></nav>
  <img class="poster" src="/__poster__/poster.png" alt="AI交叉科学社网页二维码 A4 海报">
</body>
</html>""".encode("utf-8")


def make_handler(site_url: str):
    poster_png, poster_pdf = poster_files(site_url)

    class ClubSiteHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SITE_DIR), **kwargs)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/__share__", "/__share__/"):
                body = share_page(site_url)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ("/__poster__", "/__poster__/"):
                body = poster_page()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/__poster__/poster.png":
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(poster_png)))
                self.end_headers()
                self.wfile.write(poster_png)
                return
            if path == "/__poster__/download.pdf":
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", 'attachment; filename="AIISC-web-poster-A4.pdf"')
                self.send_header("Content-Length", str(len(poster_pdf)))
                self.end_headers()
                self.wfile.write(poster_pdf)
                return
            share_assets = {"/__share__/qrcode.min.js": QR_SCRIPT}
            if path in share_assets:
                body = share_assets[path].read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, format_string, *args):
            print(f"访问：{self.address_string()} · {format_string % args}")

    return ClubSiteHandler


class LocalServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="在局域网启动 AI交叉科学社网站")
    parser.add_argument("--port", type=int, default=8765, help="服务端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开二维码页面")
    args = parser.parse_args()

    required_files = (SITE_DIR / "index.html", QR_SCRIPT, VENDOR_DIR / "qrcode" / "__init__.py")
    if not all(path.is_file() for path in required_files):
        raise SystemExit("启动文件不完整，请确认 docs 和 local-share 目录都存在。")

    requested_port = args.port
    port = find_available_port(requested_port)
    lan_ip = find_lan_ip()
    site_url = f"http://{lan_ip}:{port}/"
    control_url = f"http://127.0.0.1:{port}/__share__/"

    try:
        server = LocalServer(("0.0.0.0", port), make_handler(site_url))
    except OSError as error:
        raise SystemExit(f"无法启动端口 {port}：{error}\n请重新双击启动程序。") from error

    print("\nAI交叉科学社网站已启动")
    print("=" * 46)
    if port != requested_port:
        print(f"端口 {requested_port} 已被占用，已自动改用 {port}。")
    print(f"手机访问：{site_url}")
    print("手机与电脑必须连接同一个 Wi-Fi。")
    print("保持此窗口开启；按 Control + C 可关闭网站。")
    if lan_ip == "127.0.0.1":
        print("\n未检测到局域网地址。请先连接 Wi-Fi，再重新启动。")
    print("=" * 46 + "\n")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(control_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n网站已关闭。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
