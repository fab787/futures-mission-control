from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "mission_control" / "dashboard.html").read_text(encoding="utf-8")


def test_quantum_word_has_gradient():
    assert 'class="fmc-word">QUANTUM</span>' in HTML
    assert "-webkit-background-clip:text" in HTML
    assert "#22d3ee 55%,#a78bfa 100%" in HTML