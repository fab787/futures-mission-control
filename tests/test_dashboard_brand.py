import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_header_uses_brand_logo():
    assert 'class="fmc-logo"' in HTML
    assert '<div class="mark">FQ</div>' not in HTML


def test_favicon_is_brand_mark():
    assert 'rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,' in HTML


def test_brand_files_present():
    assert (ROOT / "mission_control" / "brand" / "fab787_quantum_logo_mark_v2.svg").exists()
    assert (ROOT / "mission_control" / "brand" / "fab787_quantum_logo_lockup_v2.svg").exists()


def test_element_id_baseline_unchanged():
    ids = set(re.findall(r'getElementById\("([^"]+)"\)', HTML))
    assert len(ids) >= 172