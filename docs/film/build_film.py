"""Builds the Vyterlix walkthrough film of the demo salon: a headless Chrome records the website while a
British voice reads the script, scene by scene, and ffmpeg joins them.

Run (system Python with websocket-client, edge-tts and imageio-ffmpeg installed), with the API on port
8000, the website on port 5500 and the demo salon built (`python -m app.cli.demo salon`):

    python docs/film/build_film.py [only-scene-id ...]

The sign-in is done off screen, so no password is ever shown. The narration is sent to Microsoft's
text-to-speech service (edge-tts); it holds nothing private. Output: docs/Vyterlix-Salon-Walkthrough.mp4
and docs/Vyterlix-Salon-Walkthrough-Script.txt. Scene files are kept in %TEMP%/vyterlix-film so one
scene can be redone without redoing the rest.
"""

import asyncio
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import edge_tts
import imageio_ffmpeg
import websocket

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LOGINS = ROOT / "backend" / ".demo-logins.local.md"
WORK = Path(tempfile.gettempdir()) / "vyterlix-film"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SITE = "http://localhost:5500"
PORT = 9556
WIDTH, HEIGHT, FPS = 1280, 720, 25
VOICE, RATE = "en-GB-SoniaNeural", "-4%"
TAIL = 0.7  # seconds of quiet after each scene's last word
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
LOOP = asyncio.new_event_loop()

# (id, page, where, how, narration). page: landing, or a file such as kpis.html.
# where: top, an element's selector, or a fraction of the page height. how: still, or drift (pixels).
SCENES = [
    ("intro", "landing", "top", 0,
     "This is Vyterlix. It helps a small business understand its own figures, see what has changed and why, "
     "know what to do next, and check whether it worked. In this film we look at a made-up hair and beauty "
     "salon called Glow, which also sells products. Everything you see is practice data, not a real business."),
    ("today", "dashboard.html", "top", 0,
     "This is Today, the page the owner opens each morning. The card at the top gives the health of the "
     "business as one number out of a hundred, and says what is pulling it down."),
    ("today-questions", "dashboard.html", 0.28, 380,
     "Below it are plain questions. What is going well. What needs my attention. What should I do first. "
     "What am I waiting for. And did my actions help."),
    ("figures", "kpis.html", "top", 0,
     "Key figures are the numbers that matter. They are grouped into parts, and Vyterlix only shows the parts "
     "that fit the business. A salon sees appointments and stock, because it takes bookings and sells "
     "products. A shop that only sells things would not see appointments, and a business that only sells "
     "services would not see stock."),
    ("money", "kpis.html", "#module-sales", 520,
     "Sales and revenue. Financial figures, such as profit and cash. And the profitability of each service "
     "and product, including what each appointment brings in, costs, and leaves."),
    ("clients", "kpis.html", "#module-retention", 520,
     "Customer retention and loyalty shows how many clients come back, and how many have gone quiet. "
     "Customer insight shows how many clients were seen, and who is new."),
    ("bookings", "kpis.html", "#module-bookings", 620,
     "Bookings and appointments cover what was kept, cancelled or missed. Workforce and productivity covers "
     "the hours the team worked, and how much of that time was spent on appointments."),
    ("stock", "kpis.html", "#module-inventory", 620,
     "Stock and inventory is for businesses that sell products. Marketing covers visits to the website and "
     "what is spent on Facebook and Instagram adverts, including the cost of winning each new customer."),
    ("operations", "kpis.html", "#module-operations", 380,
     "Operational performance covers how well the work gets done: appointments that run over, and how fast "
     "customers are answered on WhatsApp. These figures come from the times that messages were sent. "
     "Vyterlix never keeps what a message says, and never keeps a phone number."),
    ("suppliers", "kpis.html", "#module-suppliers", 620,
     "Supplier performance shows who the business buys from, and how much it leans on one supplier. Demand "
     "shows whether the number of sales and appointments is rising or falling, with a link to the forecast."),
    ("health", "health.html", "top", 380,
     "Business health brings all of it into one score. Finance, sales, customers, stock, marketing and "
     "operations each have their own score, and open to say why. Only the parts that apply to this business "
     "count towards the score."),
    ("switches", "business.html", "#modules", 300,
     "On the business page, the owner can switch any part on or off. When a part is switched off, it "
     "disappears from the screen, and the health score is worked out again straight away without it."),
    ("changes", "changes.html", "top", 300,
     "What changed lists the figures that moved by more than usual, good news as well as bad, and can explain "
     "why from the business's own records."),
    ("forecast", "forecast.html", "top", 300,
     "The forecast shows where the business is heading. The shaded band is the likely range, and Vyterlix "
     "says plainly how far to trust it."),
    ("connections", "connections.html", "top", 0,
     "Connections link Vyterlix to the systems a business already uses. There are eleven. Xero, QuickBooks "
     "and Zoho for accounts. Shopify, WooCommerce and Square for sales. Google Analytics for the website. "
     "Meta for advert spending. HubSpot for customers and deals. WhatsApp for customer messages. And the "
     "bank, through TrueLayer."),
    ("connections-read", "connections.html", "#systems", 900,
     "Vyterlix only ever reads from these systems. It can never change anything in them. All eleven are "
     "built, and tested against practice services. Trying each one with a real account is the next step."),
    ("close", "dashboard.html", "top", 0,
     "That is Vyterlix. Your figures in plain English, what changed and why, what to do next, and whether it "
     "worked. The phone app shows the same figures. Thank you for watching."),
]


def owner():
    for line in LOGINS.read_text(encoding="utf8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Owner":
            return cells[1], cells[2]
    raise SystemExit("No owner login found in the local logins file.")


class Chrome:
    def __init__(self):
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
             f"--user-data-dir={WORK / 'profile'}", f"--window-size={WIDTH},{HEIGHT}",
             "--hide-scrollbars", "--no-first-run", "--disable-gpu", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.n, self.frames, self.recording = 0, [], False
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
                self.ws = websocket.create_connection(url, timeout=90)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("Chrome did not start.")
        self.send("Page.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT,
                  deviceScaleFactor=1, mobile=False)  # fmt: skip

    def _event(self, message):
        if message.get("method") == "Page.screencastFrame" and self.recording:
            p = message["params"]
            self.frames.append((p["metadata"]["timestamp"], base64.b64decode(p["data"])))
            self.ws.send(json.dumps({"id": self._next(), "method": "Page.screencastFrameAck",
                                     "params": {"sessionId": p["sessionId"]}}))  # fmt: skip

    def _next(self):
        self.n += 1
        return self.n

    def send(self, method, **params):
        ident = self._next()
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})
            self._event(message)

    def pump(self, seconds):
        end = time.time() + seconds
        self.ws.settimeout(0.25)
        while time.time() < end:
            try:
                self._event(json.loads(self.ws.recv()))
            except websocket.WebSocketTimeoutException:
                pass
        self.ws.settimeout(90)

    def js(self, code):
        return self.send("Runtime.evaluate", expression=code, returnByValue=True,
                         awaitPromise=True).get("result", {}).get("value")  # fmt: skip

    def settle(self, extra=2.0):
        last = stable = 0
        for _ in range(40):
            size = self.js("document.body ? document.body.innerText.length : 0") or 0
            stable = stable + 1 if size == last and size > 80 else 0
            last = size
            if stable >= 2:
                break
            time.sleep(0.5)
        time.sleep(extra)

    def go(self, url):
        self.send("Page.navigate", url=url)
        time.sleep(1.0)
        self.settle()

    def close(self):
        self.proc.terminate()


SCROLL_JS = """
window.__glide = (y, ms) => { const y0 = scrollY, t0 = performance.now();
  const ease = t => t < .5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2) / 2;
  const step = now => { const t = Math.min(1, (now - t0) / ms); scrollTo(0, y0 + (y - y0) * ease(t));
    if (t < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); };
"""


def target_y(chrome, where):
    if where == "top":
        return 0
    if isinstance(where, float):
        return int(chrome.js("document.documentElement.scrollHeight - innerHeight") * where)
    return chrome.js(f"(() => {{ const e = document.querySelector({json.dumps(where)}); "
                     f"return e ? Math.max(0, e.getBoundingClientRect().top + scrollY - 90) : 0; }})()")  # fmt: skip


async def speak(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))


def duration(path):
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def encode(frames, length, audio, out, folder):
    """Frames carry their own times; hold each until the next one, and the last until `length`."""
    folder.mkdir(parents=True, exist_ok=True)
    start = frames[0][0]
    lines = []
    for i, (stamp, data) in enumerate(frames):
        (folder / f"f{i:05d}.jpg").write_bytes(data)
        nxt = frames[i + 1][0] if i + 1 < len(frames) else start + length
        lines += [f"file 'f{i:05d}.jpg'", f"duration {max(nxt - stamp, 0.001):.4f}"]
    lines.append(f"file 'f{len(frames) - 1:05d}.jpg'")
    (folder / "list.txt").write_text("\n".join(lines), encoding="utf8")
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(folder / "list.txt"),
         "-i", str(audio), "-vf", f"fps={FPS},scale={WIDTH}:{HEIGHT},format=yuv420p",
         "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac", "-b:a", "160k",
         "-ar", "44100", "-ac", "2", "-t", f"{length:.3f}", str(out)],
        check=True,
    )  # fmt: skip


def main():
    wanted = set(sys.argv[1:])
    WORK.mkdir(exist_ok=True)
    email, password = owner()
    chrome = Chrome()
    try:
        chrome.go(f"{SITE}/login.html")
        chrome.js(f"document.getElementById('email').value={json.dumps(email)};"
                  f"document.getElementById('password').value={json.dumps(password)};"
                  "document.querySelector('form').requestSubmit();")  # fmt: skip
        time.sleep(4)
        chrome.settle(0.5)
        landing = chrome.js("location.href")
        href = chrome.js("(()=>{const a=[...document.querySelectorAll('a')].find(a=>a.href.includes('org=')"
                         "&&a.textContent.includes('Glow'));return a?a.href:null})()")  # fmt: skip
        org = re.search(r"org=([0-9a-f-]{36})", href).group(1)
        current = None
        for scene, page, where, drift, text in SCENES:
            folder = WORK / scene
            clip = folder / "scene.mp4"
            if wanted and scene not in wanted:
                current = None  # the page is not where this scene expects it
                continue
            folder.mkdir(parents=True, exist_ok=True)
            audio = folder / "voice.mp3"
            LOOP.run_until_complete(speak(text, audio))
            length = duration(audio) + TAIL
            url = landing if page == "landing" else f"{SITE}/{page}?org={org}"
            if current != page:
                chrome.go(url)
                chrome.js(SCROLL_JS)
                current = page
            chrome.js(SCROLL_JS)
            chrome.frames, chrome.recording = [], True
            chrome.send("Page.startScreencast", format="jpeg", quality=88, maxWidth=WIDTH,
                        maxHeight=HEIGHT, everyNthFrame=1)  # fmt: skip
            begin = time.time()
            y = target_y(chrome, where)
            chrome.js(f"__glide({y}, 1600)")
            if drift:
                chrome.pump(2.2)
                chrome.js(f"__glide({y + drift}, {max(1000, int((length - 3.2) * 1000))})")
            chrome.pump(max(0, length - (time.time() - begin)))
            chrome.send("Page.stopScreencast")
            chrome.recording = False
            if not chrome.frames:
                raise SystemExit(f"No frames were recorded for {scene}.")
            encode(chrome.frames, length, audio, clip, folder / "frames")
            print(f"{scene}: {length:.1f}s, {len(chrome.frames)} frames")
    finally:
        chrome.close()

    clips = [WORK / s[0] / "scene.mp4" for s in SCENES]
    missing = [c.parent.name for c in clips if not c.exists()]
    if missing:
        raise SystemExit(f"Not joined: no recording yet for {missing}")
    listing = WORK / "all.txt"
    listing.write_text("\n".join(f"file '{c.as_posix()}'" for c in clips), encoding="utf8")
    out = DOCS / "Vyterlix-Salon-Walkthrough.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(out)], check=True)  # fmt: skip
    stamp = 0.0
    lines = ["VYTERLIX WALKTHROUGH OF THE DEMO SALON: VOICE-OVER SCRIPT", ""]
    for scene, _, _, _, text in SCENES:
        lines += [f"[{int(stamp // 60)}:{int(stamp % 60):02d}] {scene}", text, ""]
        stamp += duration(WORK / scene / "voice.mp3") + TAIL
    (DOCS / "Vyterlix-Salon-Walkthrough-Script.txt").write_text("\n".join(lines), encoding="utf8")
    print(f"Done: {out} ({stamp // 60:.0f} min {stamp % 60:.0f} s)")


if __name__ == "__main__":
    main()
