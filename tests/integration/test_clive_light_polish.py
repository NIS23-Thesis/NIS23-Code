"""Visual-only light-theme state and contrast regression checks.

These checks validate authored CSS; they are not browser screenshot tests.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2] / 'clive'
STUDIO = (ROOT / 'control_ui/theme.css').read_text(encoding='utf-8')
VISUALIZER = (ROOT / 'visualizer/static/theme.css').read_text(encoding='utf-8')


def _luminance(color):
    assert re.fullmatch(r'#[0-9a-fA-F]{6}', color)
    rgb = [int(color[start:start + 2], 16) / 255 for start in (1, 3, 5)]
    adjusted = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in rgb]
    return sum(channel * weight for channel, weight in zip(adjusted, (0.2126, 0.7152, 0.0722)))


def _contrast(first, second):
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_panel_ready_theme_sets_explicit_studio_states():
    block = STUDIO.split('Panel-ready light UI polish (2026-11-17)', 1)[1]
    for state in (
        '.preset-item.active', '.preset-item:hover:not(.active)',
        '.nav-item:hover:not(.selected)', '.nav-item.selected',
        '.primary:hover:not(:disabled)', '.danger:hover:not(:disabled)',
        'button[aria-disabled="true"]:hover', '.tab.active',
        '.run-log-details summary:hover', '.history-entry:hover',
        ':focus-visible',
    ):
        assert state in block, f'Light studio theme missing state: {state}'


def test_panel_ready_theme_covers_all_visualizer_controls():
    block = VISUALIZER.split('Panel-ready light UI polish (2026-11-17)', 1)[1]
    for state in (
        '.ghost-button:hover:not(:disabled)', '.play-button:hover:not(:disabled)',
        '.mode-button:hover:not(.active)', '.tab-button.active',
        '.ch-legend-item:hover', '.ch-legend-item[aria-pressed="true"]',
        '.chart-tooltip', '.node-tooltip', '.next-action-button:hover',
        '.archive-table tr:hover', '.learning-detail-tabs .detail-tab:hover',
        'button:disabled', ':focus-visible',
    ):
        assert state in block, f'Light visualizer theme missing state: {state}'


def test_key_panel_ready_text_and_button_pairs_clear_standard_contrast():
    for fg, bg in (
        ('#075d59', '#e3f3f0'),  # selected preset
        ('#354f64', '#ffffff'),  # normal preset
        ('#ffffff', '#08756e'), # primary action
        ('#ffffff', '#b72f44'), # Stop action
        ('#586e80', '#edf1f4'), # legible disabled action
        ('#095d7d', '#eaf2f6'), # tab hover
        ('#486177', '#ffffff'), # neutral metadata
        ('#ffffff', '#096d80'), # playback action
    ):
        assert _contrast(fg, bg) >= 4.5, f'Contrast {_contrast(fg,bg):.2f} too low for {fg} on {bg}'


def test_dark_theme_is_not_overridden_by_polish_rules():
    for css in (STUDIO, VISUALIZER):
        appended = css.split('Panel-ready light UI polish (2026-11-17)', 1)[1]
        # Every independent styling rule in the polish block is light scoped,
        # except reduced-motion @media which only has light-scoped descendants.
        assert ':root:not([data-theme' not in appended
        assert 'html[data-theme="light"]' in appended
