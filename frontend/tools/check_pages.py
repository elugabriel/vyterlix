"""Checks every page of the website for the mistakes a person would feel: sideways scrolling, controls
with no label, buttons and boxes too small for a thumb, and text too faint to read. It opens each
page in a headless Chrome at phone and computer width, so it needs the site running and Google
Chrome installed. No passwords are kept here: give the demo owner's login in the environment.

    set VYTERLIX_CHECK_EMAIL=...   set VYTERLIX_CHECK_PASSWORD=...
    python frontend/tools/check_pages.py [http://localhost:5500]

It exits with 1 and says what to fix if anything fails.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # pip install websocket-client

CHROME = os.environ.get("CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SITE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5500").rstrip("/")
PORT = 9444
W, H = 1280, 800
PAGES = ["dashboard", "kpis", "health", "reports", "changes", "assistant", "forecast", "alerts", "actions",
         "memory", "data", "import", "imports", "entry", "connections", "business", "billing", "account"]
SIZES = [("phone", 390, 844, True), ("computer", 1280, 800, False)]
MIN_TARGET = 44


class Page:
    def __init__(self):
        profile = Path(tempfile.mkdtemp())
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
             f"--window-size={W},{H}", "--hide-scrollbars", "--no-first-run", "--disable-gpu", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                self.ws = websocket.create_connection(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"], timeout=60)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("chrome did not start")
        self.n = 0
        self.send("Page.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def send(self, method, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, code):
        res = self.send("Runtime.evaluate", expression=code, returnByValue=True, awaitPromise=True)
        return res.get("result", {}).get("value")

    def settle(self, extra=1.5):
        last, stable = -1, 0
        for _ in range(40):
            size = self.js("document.body ? document.body.innerText.length : 0") or 0
            stable = stable + 1 if size == last and size > 80 else 0
            last = size
            if stable >= 2:
                break
            time.sleep(0.5)
        time.sleep(extra)

    def goto(self, url, extra=1.5):
        self.send("Page.navigate", url=url)
        time.sleep(1.0)
        self.settle(extra)

    def scroll_to(self, y):
        self.js(f"window.scrollTo(0, {y})")
        time.sleep(0.6)

    def height(self):
        return self.js("document.documentElement.scrollHeight") or H

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass




AUD = r"""
(() => {
  const lum = (c) => { const [r,g,b] = c.map(v => { v/=255; return v<=0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055,2.4)}); return 0.2126*r+0.7152*g+0.0722*b };
  const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if(!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {c:p.slice(0,3), a: p.length>3 ? p[3] : 1} };
  const bgOf = (el) => { while (el) { const cs = getComputedStyle(el); if (cs.backgroundImage !== 'none') return null; const p = parse(cs.backgroundColor); if (p && p.a > 0.9) return p.c; el = el.parentElement } return [255,255,255] };
  const low = []; const seen = new Set();
  const w = document.createTreeWalker(document.querySelector('main') || document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode; if (!n.textContent.trim()) continue;
    const el = n.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect(); if (!r.width || cs.visibility==='hidden') continue;
    const fg = parse(cs.color); const bg = bgOf(el); if (!fg || !bg) continue;
    const a = lum(fg.c), b = lum(bg); const ratio = (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    if (ratio < ((size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5)) low.push(`${ratio.toFixed(2)} ${size}px "${n.textContent.trim().slice(0,34)}" ${el.className||el.tagName}`);
  }
  const noName = [...document.querySelectorAll('button, a, select, input')].filter(e => !(e.innerText||'').trim() && !e.getAttribute('aria-label') && !(e.labels&&e.labels.length) && e.type!=='hidden').map(e => e.outerHTML.slice(0,110));
  const small = [...document.querySelectorAll('main a, main button, main summary, main select, main input')].filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && (r.height<44) }).map(e => `${e.tagName.toLowerCase()} ${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)} "${(e.innerText||'').trim().slice(0,22)}"`);
  const fs = {}; document.querySelectorAll('main *').forEach(e => { if (e.childNodes.length && [...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())) { const s = getComputedStyle(e).fontSize; fs[s]=(fs[s]||0)+1 } });
  return {low: low.length, lowS: low.slice(0,8), noName: noName.slice(0,5), nSmall: small.length, small: small.slice(0,8), fontSizes: fs};
})()
"""


CHECK = r"""
(() => {
  const bad = [];
  if (document.documentElement.scrollWidth > window.innerWidth + 2) bad.push("the page scrolls sideways");
  const unlabelled = [...document.querySelectorAll('main select, main input:not([type=hidden]), main textarea')].filter(e => e.getBoundingClientRect().width > 0 && !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby'));
  for (const e of unlabelled.slice(0, 5)) bad.push("no label: " + e.outerHTML.slice(0, 80));
  const small = [...document.querySelectorAll('main button, main select, main input:not([type=hidden]):not([type=checkbox]):not([type=radio]), main summary')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 43.5 });
  for (const e of small.slice(0, 5)) bad.push(`too small (${Math.round(e.getBoundingClientRect().height)} px): ` + (e.innerText || e.name || e.tagName).trim().slice(0, 30));
  return bad;
})()
"""


def main():
    email, password = os.environ.get("VYTERLIX_CHECK_EMAIL"), os.environ.get("VYTERLIX_CHECK_PASSWORD")
    if not (email and password):
        sys.exit("Set VYTERLIX_CHECK_EMAIL and VYTERLIX_CHECK_PASSWORD (a demo owner) first.")
    p = Page()
    failures = []
    try:
        p.goto(f"{SITE}/login.html")
        p.js(f"document.getElementById('email').value={json.dumps(email)};document.getElementById('password').value={json.dumps(password)};document.querySelector('form').requestSubmit();")
        time.sleep(6)
        p.settle(2.0)
        link = p.js("""(() => { const a=[...document.querySelectorAll('a')].find(a=>a.href.includes('org=')); return a?a.href:null })()""")
        match = re.search(r"org=([0-9a-f-]{36})", link or "")
        if not match:
            sys.exit("Could not sign in, or the account has no business.")
        org = match.group(1)
        for name, width, height, mobile in SIZES:
            p.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=mobile)
            for page in PAGES:
                p.goto(f"{SITE}/{page}.html?org={org}", 1.5)
                for problem in p.js(CHECK) or []:
                    failures.append(f"{page} ({name}): {problem}")
                low = p.js(AUD) or {}
                if low.get("low"):
                    failures.append(f"{page} ({name}): {low['low']} piece(s) of faint text, e.g. {low['lowS'][0]}")
    finally:
        p.close()
    for line in failures:
        print(line)
    print(f"{len(failures)} problem(s) across {len(PAGES) * len(SIZES)} page views")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
