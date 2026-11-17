"""UI-only light/dark mode regression checks for CLIVE's visualizer."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "clive"
PAGES = (
    ROOT / "control_ui/index.html",
    ROOT / "visualizer/static/index.html",
    ROOT / "visualizer/static/learning.html",
    ROOT / "visualizer/static/events.html",
)


@pytest.mark.parametrize("page", PAGES, ids=["studio", "topology", "learning", "events"])
def test_each_page_has_accessible_theme_toggle_and_assets(page):
    content = page.read_text(encoding="utf-8")
    assert content.count('id="cliveThemeToggle"') == 1
    assert 'aria-pressed="false"' in content
    assert 'Switch to light mode' in content
    assert 'theme.js' in content
    assert 'theme.css' in content
    assert (page.parent / "theme.js").is_file()
    assert (page.parent / "theme.css").is_file()


def test_shared_theme_runtime_consistent_and_remembers_choice():
    source = (ROOT / "visualizer/static/theme.js").read_text(encoding="utf-8")
    assert source == (ROOT / "control_ui/theme.js").read_text(encoding="utf-8")
    assert "window.localStorage.setItem" in source
    assert "window.localStorage.getItem" in source
    assert "clive-theme-change" in source
    assert "url.searchParams.set('theme', current)" in source


def test_canvas_rendering_recolors_immediately_on_theme_toggle():
    topology = (ROOT / "visualizer/static/app.js").read_text(encoding="utf-8")
    learning = (ROOT / "visualizer/static/learning.js").read_text(encoding="utf-8")
    assert 'window.addEventListener("clive-theme-change", () => { drawTopology(); drawTrafficChart(); });' in topology
    assert 'themeCanvas("#090d12", "#f8fbff")' in topology
    assert 'window.addEventListener("clive-theme-change", () => { if (modelData) render(); });' in learning
    assert "CH_PALETTE_LIGHT" in learning and "window.CLIVETheme?.isLight()" in learning


def test_header_generator_does_not_erase_theme_button():
    source = (ROOT / "scripts/sync_ui_shell.py").read_text(encoding="utf-8")
    assert 'id=\\"cliveThemeToggle\\"' in source or 'id="cliveThemeToggle"' in source
