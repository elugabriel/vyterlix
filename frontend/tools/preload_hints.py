"""Tells the browser, in each page, about every script file the page will need, so it fetches them all at
once instead of discovering them one level at a time.

The site has no build step: a page loads its own script, which imports others, which import others. A
browser only finds the second level once the first has arrived, so on a connection with a long round
trip (most of the world, to a server in Europe) every level costs a full wait before the page works.
`<link rel="modulepreload">` lists them up front. This tool works the list out from the imports, so it
cannot go stale by hand, and writes it between two marker comments in each page.

    python frontend/tools/preload_hints.py            # rewrite the pages
    python frontend/tools/preload_hints.py --check    # say which pages are out of date (exit 1)

Only static `import ... from "x"` and `export ... from "x"` are followed; `import("x")` is loaded on
demand, on purpose.
"""

import re
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
START = "  <!-- preload:start (written by frontend/tools/preload_hints.py; do not edit by hand) -->"
END = "  <!-- preload:end -->"
MODULE_TAG = re.compile(r'<script type="module" src="([^"]+)"></script>')
BLOCK = re.compile(
    r"[ \t]*" + re.escape(START.strip()) + r".*?" + re.escape(END.strip()) + r"\n?", re.DOTALL
)
IMPORT = re.compile(r"""(?:^|[;\s])(?:import|export)\s+(?:[^'";]*?\s+from\s+)?["']([^"']+)["']""", re.MULTILINE)


def dependencies(entry: Path) -> list[Path]:
    """Every file the entry script needs (not the entry itself), nearest first."""
    seen: list[Path] = []
    queue = [entry]
    while queue:
        current = queue.pop(0)
        for target in IMPORT.findall(current.read_text(encoding="utf-8")):
            if not target.startswith("."):
                continue  # only our own files
            path = (current.parent / target).resolve()
            if path.is_file() and path != entry and path not in seen:
                seen.append(path)
                queue.append(path)
    return seen


def block_for(page: Path) -> str | None:
    match = MODULE_TAG.search(page.read_text(encoding="utf-8"))
    if not match:
        return None
    entry = (page.parent / match.group(1)).resolve()
    files = dependencies(entry)
    if not files:
        return None
    links = [f'  <link rel="modulepreload" href="{p.relative_to(FRONTEND).as_posix()}">' for p in files]
    return "\n".join([START, *links, END]) + "\n"


def rewritten(page: Path) -> str:
    html = BLOCK.sub("", page.read_text(encoding="utf-8"))
    block = block_for(page)
    if block is None:
        return html
    tag = MODULE_TAG.search(html)
    end_of_line = html.index("\n", tag.end()) + 1
    return html[:end_of_line] + block + html[end_of_line:]


def main(check: bool = False) -> list[str]:
    """The names of the pages that were (or, with check, would be) changed."""
    changed = []
    for page in sorted(FRONTEND.glob("*.html")):
        new = rewritten(page)
        if new != page.read_text(encoding="utf-8"):
            changed.append(page.name)
            if not check:
                page.write_text(new, encoding="utf-8", newline="")
    return changed


if __name__ == "__main__":
    out_of_date = main(check="--check" in sys.argv)
    if "--check" in sys.argv:
        if out_of_date:
            print("Out of date (run frontend/tools/preload_hints.py):", ", ".join(out_of_date))
            sys.exit(1)
        print("Preload hints are up to date.")
    else:
        print(f"Updated {len(out_of_date)} page(s):", ", ".join(out_of_date) or "none")
