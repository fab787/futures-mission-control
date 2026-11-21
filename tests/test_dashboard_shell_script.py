from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_shell_adds_styles_and_loader():
    assert 'id="fmc-shell"' in HTML
    assert 'id="fmc-shell-script"' in HTML


def test_injector_markers_are_untouched():
    assert '<button data-tab="risk">Risk & Approvals</button>' in HTML
    assert '<section class="section" id="help">' in HTML


def test_no_duplicate_signals_or_accounts_sections():
    assert HTML.count('id="signals"') == 0
    assert HTML.count('id="accounts"') == 0


def test_original_tab_text_unchanged():
    assert 'data-tab="news">News & Events</button>' in HTML