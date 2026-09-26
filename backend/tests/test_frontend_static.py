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
