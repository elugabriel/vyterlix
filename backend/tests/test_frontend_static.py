"""Checks on the static frontend (plain HTML/JS, no build step, no Node).

These don't run the JavaScript; they enforce the rules that keep the pages safe and
wired up correctly. Behaviour is checked in a real browser (see README).
"""

import re
from pathlib import Path

import pytest

from app.services import auth as auth_service
from app.services import invitations, password_reset

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGES = sorted(FRONTEND.glob("*.html"))
SCRIPTS = sorted((FRONTEND / "js").rglob("*.js"))


def test_frontend_exists():
    assert PAGES and SCRIPTS


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_references_files_that_exist(page):
    html = page.read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"#?]+\.(?:js|css))"', html)
    assert refs, "page loads no scripts or styles"
    for ref in refs:
        assert (FRONTEND / ref).is_file(), f"{page.name} references missing {ref}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_config_loads_before_page_scripts_and_no_referrer_is_set(page):
    html = page.read_text(encoding="utf-8")
    assert '<meta name="referrer" content="no-referrer">' in html
    config = html.find('src="js/config.js"')
    module = html.find('type="module"')
    assert 0 <= config < module, f"{page.name}: config.js must load before the page module"


@pytest.mark.parametrize(
    ("service", "page"),
    [
        (auth_service, "verify-email.html"),
        (password_reset, "reset-password.html"),
        (password_reset, "forgot-password.html"),
        (invitations, "accept-invite.html"),
    ],
)
def test_pages_linked_from_emails_exist(service, page):
    assert page in Path(service.__file__).read_text(encoding="utf-8")
    assert (FRONTEND / page).is_file()


DANGEROUS = [
    r"\.innerHTML\b",
    r"\.outerHTML\b",
    r"insertAdjacentHTML",
    r"document\.write",
    r"\beval\(",
    r"new Function\(",
]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_no_raw_html_writes(script):
    """No framework auto-escaping here: API data must go in via textContent / el()."""
    source = script.read_text(encoding="utf-8")
    for pattern in DANGEROUS:
        assert not re.search(pattern, source), f"{script.name} uses {pattern}"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_tokens_never_go_in_local_storage(script):
    assert "localStorage" not in script.read_text(encoding="utf-8")


def test_access_token_lives_only_in_auth_module_memory():
    for script in SCRIPTS:
        if script.name != "auth.js":
            assert "access_token" not in script.read_text(encoding="utf-8"), script.name


def test_every_api_method_the_pages_call_exists():
    """Catches calls like api.put(...) when the client has no put() (a real bug, once)."""
    client = (FRONTEND / "js" / "api.js").read_text(encoding="utf-8")
    provided = set(re.findall(r"^\s+(\w+): \(path", client, re.MULTILINE))
    assert {"get", "post", "put", "patch", "delete"} <= provided
    for script in SCRIPTS:
        for method in re.findall(r"\bapi\.(\w+)\(", script.read_text(encoding="utf-8")):
            assert method in provided, f"{script.name} calls api.{method}(), which doesn't exist"


@pytest.mark.parametrize(
    "name",
    [
        "data",
        "imports",
        "import",
        "entry",
        "connections",
        "kpis",
        "health",
        "changes",
        "forecast",
        "actions",
        "memory",
        "assistant",
        "alerts",
        "notifications",
        "dashboard",
    ],
)
def test_data_pages_start_last_so_their_constants_are_ready(name):
    """A top-level `await start()` before later `const`s run makes a reload crash with
    "Cannot access ... before initialization" (found in a real browser)."""
    source = (FRONTEND / "js" / "pages" / f"{name}.js").read_text(encoding="utf-8")
    start = source.index("await start(opened)")
    after = source[start:]
    assert not re.search(r"^(const|let|class) ", after, re.MULTILINE), name
    assert not re.search(r"^function ", after, re.MULTILINE), name


def test_the_connection_callback_page_runs_last_and_clears_the_address_bar():
    """The provider's one-time code arrives in the address; it must be removed straight away."""
    source = (FRONTEND / "js" / "pages" / "integrations-callback.js").read_text(encoding="utf-8")
    run = source.index("await finish()")
    after = source[run:]
    assert not re.search(r"^(const|let|class|function) ", after, re.MULTILINE)
    body = source[source.index("async function finish()") : run]
    assert body.index("history.replaceState") < body.index("api.post")


# --- one shell for every page of a business -------------------------------------


BUSINESS_PAGES = sorted(
    p
    for p in PAGES
    if "openBusiness" in (FRONTEND / "js" / "pages" / f"{p.stem}.js").read_text(encoding="utf-8")
)


def test_every_business_page_is_found():
    assert len(BUSINESS_PAGES) >= 17 and "dashboard.html" in {p.name for p in BUSINESS_PAGES}


@pytest.mark.parametrize("page", BUSINESS_PAGES, ids=lambda p: p.name)
def test_every_business_page_has_the_same_shell(page):
    """The same top bar, the same place for messages and a title that says which business: so that a
    page cannot drift from the rest (the navigation bar is added to all of them by openBusiness)."""
    html = page.read_text(encoding="utf-8")
    assert (
        '<header class="topbar">' in html
        and '<a class="brand" href="app.html">Vyterlix</a>' in html
    )
    assert 'id="user-name"' in html and 'id="logout"' in html
    assert '<main class="container stack">' in html
    assert 'id="message" class="message" role="alert" hidden' in html
    assert 'id="org-name"' in html
    assert '<link rel="stylesheet" href="css/base.css">' in html
    assert re.search(r"<title>[^<]+ · Vyterlix</title>", html)
    assert '<html lang="en-GB">' in html


def test_the_navigation_bar_only_points_at_pages_that_exist():
    source = (FRONTEND / "js" / "nav.js").read_text(encoding="utf-8")
    targets = re.findall(r'\["([a-z-]+\.html)", "', source) + re.findall(
        r'"([a-z-]+\.html)"', source
    )
    assert len(set(targets)) >= 12
    for target in set(targets):
        assert (FRONTEND / target).is_file(), target


def test_the_navigation_bar_is_added_by_the_one_shared_opener():
    business = (FRONTEND / "js" / "business.js").read_text(encoding="utf-8")
    assert "mountNav(orgId)" in business and 'from "./nav.js"' in business
    for script in (FRONTEND / "js" / "pages").glob("*.js"):
        assert "mountNav" not in script.read_text(encoding="utf-8"), (
            script.name
        )  # never done by hand


def test_the_list_of_businesses_opens_the_front_screen():
    assert "dashboard.html?org=" in (FRONTEND / "js" / "pages" / "app.js").read_text(
        encoding="utf-8"
    )
