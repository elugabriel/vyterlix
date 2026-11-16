"""Builds the Vyterlix walkthrough film of the demo salon, covering the whole website: a headless Chrome
records each page while a British voice reads the script, scene by scene, and ffmpeg joins them.

Run (system Python with websocket-client, edge-tts and imageio-ffmpeg installed), with the API on port
8000, the website on port 5500 and the demo salon built (`python -m app.cli.demo salon`):

    python docs/film/build_film.py [only-scene-id ...]

The sign-in is done off screen, so no password is ever shown (the sign-in page itself is filmed before
anyone signs in, with nothing typed). The narration is sent to Microsoft's text-to-speech service
(edge-tts); it holds nothing private. Output: docs/Vyterlix-Salon-Walkthrough.mp4 and
docs/Vyterlix-Salon-Walkthrough-Script.txt. Scene files are kept in %TEMP%/vyterlix-film so one scene can
be redone (name it on the command line) without redoing the rest.
"""

import asyncio
import base64
import json
import re
import shutil
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

# (id, page, where, drift, narration, act).
# page: login (filmed before signing in), landing (the list of businesses), first-change (the story page
# of the first change listed), or a file such as kpis.html.
# where: top, an element's selector, or a fraction of the page height.
# drift: pixels to glide down while talking. act: JavaScript run just after the recording starts.
SCENES = [
    ("login", "login", "top", 0,
     "This is Vyterlix: a simple way for a small business to understand its own figures, see what has "
     "changed and why, know what to do next, and check whether it worked. This film walks through the whole "
     "website, using a made-up hair and beauty salon. Everyone signs in with their own email and password. "
     "Repeated wrong guesses are blocked for a while, and each phone or browser you sign in on is "
     "remembered, so you can see it later and sign it out.", None),
    ("businesses", "landing", "top", 0,
     "One login can look after more than one business, and each business is kept completely apart from the "
     "others. We will open the demo salon, Glow Hair and Beauty. Everything you see from here is practice "
     "data, not a real business.", None),
    ("today", "dashboard.html", "top", 0,
     "This is Today, the page the owner opens each morning. The card at the top gives the health of the "
     "business as one number out of a hundred, and says what is pulling it down.", None),
    ("today-questions", "dashboard.html", 0.28, 380,
     "Below it are plain questions. What is going well. What needs my attention. What should I do first. "
     "What am I waiting for. And did my actions help.", None),
    ("today-chart", "dashboard.html", 0.7, 300,
     "Further down, a chart shows how sales have moved, with every month labelled, and each key figure has a "
     "small picture of the last six months.", None),
    ("figures", "kpis.html", "top", 0,
     "Key figures are the numbers that matter. They are grouped into parts, and Vyterlix only shows the parts "
     "that fit the business. A salon sees appointments and stock, because it takes bookings and sells "
     "products. A shop that only sells things would not see appointments, and a business that only sells "
     "services would not see stock.", None),
    ("money", "kpis.html", "#module-sales", 520,
     "Sales and revenue. Financial figures, such as profit and cash. And the profitability of each service "
     "and product, including what each appointment brings in, costs, and leaves.", None),
    ("clients", "kpis.html", "#module-retention", 520,
     "Customer retention and loyalty shows how many clients come back, and how many have gone quiet. "
     "Customer insight shows how many clients were seen, and who is new.", None),
    ("bookings", "kpis.html", "#module-bookings", 620,
     "Bookings and appointments cover what was kept, cancelled or missed. Workforce and productivity covers "
     "the hours the team worked, and how much of that time was spent on appointments.", None),
    ("stock", "kpis.html", "#module-inventory", 620,
     "Stock and inventory is for businesses that sell products. Marketing covers visits to the website and "
     "what is spent on Facebook and Instagram adverts, including the cost of winning each new customer.", None),
    ("operations", "kpis.html", "#module-operations", 380,
     "Operational performance covers how well the work gets done: appointments that run over, and how fast "
     "customers are answered on WhatsApp. These figures come from the times that messages were sent. "
     "Vyterlix never keeps what a message says, and never keeps a phone number.", None),
    ("suppliers", "kpis.html", "#module-suppliers", 620,
     "Supplier performance shows who the business buys from, and how much it leans on one supplier. Demand "
     "shows whether the number of sales and appointments is rising or falling, with a link to the forecast.",
     None),
    ("figure-detail", "kpis.html", "#module-sales", 120,
     "Open any figure for a full chart, a plain explanation of what it means, and how it was worked out.",
     "document.querySelector('#module-sales details summary')?.click()"),
    ("health", "health.html", "top", 380,
     "Business health brings all of it into one score. Finance, sales, customers, stock, marketing and "
     "operations each have their own score, and open to say why. Only the parts that apply to this business "
     "count towards the score. The chart underneath shows the score month by month, so you can see whether "
     "things are improving.", None),
    ("reports", "reports.html", "top", 300,
     "Reports are written for you in plain English. Choose one, such as the monthly report or the health "
     "report, and Vyterlix writes it from your own figures. Each report is kept exactly as it was written, "
     "and can be downloaded as a PDF or a spreadsheet, or sent to your team by email every week or month.",
     None),
    ("changes", "changes.html", "top", 300,
     "What changed lists the figures that moved by more than usual, good news as well as bad. Each one shows "
     "last month against this month, so the size of the move is clear at a glance, and Vyterlix can explain "
     "why from the business's own records, and says honestly when it cannot tell.", None),
    ("story", "first-change", "top", 420,
     "Every change has its own page that tells the whole story in steps: what happened, why it happened, "
     "what we suggest, your decision, getting it done, and how it went. It is the heart of Vyterlix: not "
     "just telling you a number moved, but helping you decide what to do and checking whether it worked.",
     None),
    ("assistant", "assistant.html", "top", 300,
     "Ask Vyterlix like you would ask an accountant. Ask how the business is doing, or why sales fell. The "
     "answers come only from your own figures and records, they say where they came from, and if something "
     "cannot be answered from your data, you are told so rather than given a guess.",
     "(()=>{const i=document.querySelector('input[name=question]');if(i){i.value='How is the business doing?';"
     "i.closest('form').requestSubmit();}})()"),
    ("forecast", "forecast.html", "top", 300,
     "The forecast shows where the business is heading. The line is the best estimate, and the shaded band is "
     "the likely range. When there is only a little history, it says so plainly and tells you how much to "
     "trust it.", None),
    ("alerts", "alerts.html", "top", 400,
     "Alerts tell you when something needs a decision. There is one alert for each figure, written in a "
     "single plain sentence, and the most serious are listed first. Open one and you can accept the "
     "suggestion, put it off for a few days, or say no. Vyterlix remembers what you decided.", None),
    ("actions", "actions.html", "top", 300,
     "Actions are everything you decided to do: who is doing it, by when, and how far it has got. When an "
     "action is finished, Vyterlix waits for the next month's figures and then checks them, to tell you "
     "honestly whether it worked, partly worked, or did not.", None),
    ("memory", "memory.html", "top", 300,
     "What we know is what Vyterlix has learned from what you have tried, along with the limits you have "
     "set. Later advice respects both, so the suggestions get more useful the longer you use it.", None),
    ("data", "data.html", "top", 500,
     "Your data shows how complete and trustworthy your information is, and what to fix first. Each kind of "
     "data has a score, and the chart shows which months are missing information. Better data means better "
     "advice.", None),
    ("upload", "import.html", "top", 300,
     "To bring in information, upload a spreadsheet from any system. Vyterlix matches the columns, and "
     "checks every row before anything is saved, so a mistake in the file never ends up in your figures.",
     None),
    ("imports", "imports.html", "top", 200,
     "Every import is recorded here. If one turns out to be wrong, it can be undone cleanly.", None),
    ("entry", "entry.html", "top", 300,
     "You can also type things in by hand: sales, costs, customers, stock, projects, appointments, leads and "
     "the hours your team worked. That suits a business that has no spreadsheet at all.", None),
    ("connections", "connections.html", "top", 0,
     "Connections link Vyterlix to the systems a business already uses. There are eleven. Xero, QuickBooks "
     "and Zoho for accounts. Shopify, WooCommerce and Square for sales. Google Analytics for the website. "
     "Meta for advert spending. HubSpot for customers and deals. WhatsApp for customer messages. And the "
     "bank, through TrueLayer.", None),
    ("connections-read", "connections.html", "#systems", 900,
     "Vyterlix only ever reads from these systems. It can never change anything in them. All eleven are "
     "built, and tested against practice services. Trying each one with a real account is the next step.",
     None),
    ("settings", "business.html", "top", 400,
     "In Settings you describe the business, such as what it sells and its busy and quiet seasons. You can "
     "also invite your team as an owner, a manager or a viewer, and each person sees only what their role "
     "allows.", None),
    ("switches", "business.html", "#modules", 300,
     "The owner can also switch any part of Vyterlix on or off. When a part is switched off, it disappears "
     "from the screen, and the health score is worked out again straight away without it.", None),
    ("billing", "billing.html", "top", 300,
     "Billing shows your plan, what you have used, and what you have been charged. Live payments are not "
     "switched on yet, because that depends on the payment account being set up.", None),
    ("account", "account.html", "top", 300,
     "Your account lists every browser and phone you are signed in on. You can name them, and sign any of "
     "them out with one click.", None),
    ("notifications", "notifications.html", "top", 200,
     "Each person chooses what they hear about, and how: in the app, or by email. Quiet hours hold the "
     "emails until they end, but anything about your account security always gets through.", None),
    ("close", "dashboard.html", "top", 0,
     "That is the whole website. Everything you have seen works with practice data. All eleven connections "
     "are built and tested against practice services, and a live check with real accounts is the next step. "
     "Still to come: the hosting and the website address, the app stores, push notifications, and live "
     "payments. The phone app follows the same pattern as the website. Thank you for watching.", None),
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


class Film:
    def __init__(self, chrome, wanted):
        self.chrome, self.wanted = chrome, wanted
        self.current = None  # the address the browser is on, so a scene on the same page just scrolls
        self.landing = self.org = None

    def address(self, page):
        if page == "login":
            return f"{SITE}/login.html"
        if page == "landing":
            return self.landing
        if page == "first-change":
            self.chrome.go(f"{SITE}/changes.html?org={self.org}")
            href = self.chrome.js("(()=>{const a=document.querySelector('a[href*=\"change.html\"]');"
                                  "return a?a.href:null})()")  # fmt: skip
            if not href:
                raise SystemExit("No change was found to tell the story of.")
            return href
        return f"{SITE}/{page}?org={self.org}"

    def sign_in(self):
        email, password = owner()
        self.chrome.go(f"{SITE}/login.html")
        self.chrome.js(f"document.getElementById('email').value={json.dumps(email)};"
                       f"document.getElementById('password').value={json.dumps(password)};"
                       "document.querySelector('form').requestSubmit();")  # fmt: skip
        time.sleep(4)
        self.chrome.settle(0.5)
        self.landing = self.chrome.js("location.href")
        href = self.chrome.js("(()=>{const a=[...document.querySelectorAll('a')].find(a=>a.href.includes('org=')"
                              "&&a.textContent.includes('Glow'));return a?a.href:null})()")  # fmt: skip
        self.org = re.search(r"org=([0-9a-f-]{36})", href).group(1)
        self.current = None

    def record(self, scene):
        name, page, where, drift, text, act = scene
        chrome = self.chrome
        folder = WORK / name
        folder.mkdir(parents=True, exist_ok=True)
        audio = folder / "voice.mp3"
        LOOP.run_until_complete(speak(text, audio))
        length = duration(audio) + TAIL
        url = self.address(page)
        if self.current != url:
            chrome.go(url)
            self.current = url
        chrome.js(SCROLL_JS)
        chrome.frames, chrome.recording = [], True
        chrome.send("Page.startScreencast", format="jpeg", quality=88, maxWidth=WIDTH,
                    maxHeight=HEIGHT, everyNthFrame=1)  # fmt: skip
        begin = time.time()
        if act:
            chrome.js(act)
        chrome.js(f"__glide({target_y(chrome, where)}, 1600)")
        if drift:
            chrome.pump(2.2)
            chrome.js(f"__glide({target_y(chrome, where) + drift}, {max(1000, int((length - 3.2) * 1000))})")
        chrome.pump(max(0, length - (time.time() - begin)))
        chrome.send("Page.stopScreencast")
        chrome.recording = False
        if not chrome.frames:
            raise SystemExit(f"No frames were recorded for {name}.")
        encode(chrome.frames, length, audio, folder / "scene.mp4", folder / "frames")
        print(f"{name}: {length:.1f}s, {len(chrome.frames)} frames", flush=True)

    def run(self):
        signed_in = False
        for scene in SCENES:
            name, page = scene[0], scene[1]
            if page != "login" and not signed_in:
                self.sign_in()
                signed_in = True
            if self.wanted and name not in self.wanted:
                self.current = None  # the page is not where the next scene expects it
                continue
            self.record(scene)


def join():
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
    for scene in SCENES:
        lines += [f"[{int(stamp // 60)}:{int(stamp % 60):02d}] {scene[0]}", scene[4], ""]
        stamp += duration(WORK / scene[0] / "voice.mp3") + TAIL
    (DOCS / "Vyterlix-Salon-Walkthrough-Script.txt").write_text("\n".join(lines), encoding="utf8")
    print(f"Done: {out} ({int(stamp // 60)} min {int(stamp % 60)} s)")


def main():
    WORK.mkdir(exist_ok=True)
    shutil.rmtree(WORK / "profile", ignore_errors=True)  # no earlier sign-in is remembered
    chrome = Chrome()
    try:
        Film(chrome, set(sys.argv[1:])).run()
    finally:
        chrome.close()
    join()


if __name__ == "__main__":
    main()
