"""CLIVE presentation UI must keep optional explanations accessible but unobtrusive."""
from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[2] / 'clive' / 'visualizer' / 'static'
PAGES = ('index.html', 'learning.html', 'events.html')


def test_every_help_button_has_a_content_template():
    expected = {'index.html': 8, 'learning.html': 7, 'events.html': 2}
    for name in PAGES:
        page = (STATIC / name).read_text(encoding='utf-8')
        topics = re.findall(r'<button[^>]+data-help="([a-z0-9-]+)"', page)
        templates = set(re.findall(r'<template id="help-([a-z0-9-]+)"', page))
        assert len(topics) == expected[name]
        assert len(topics) == len(set(topics))
        assert set(topics) == templates
        assert re.search(r'<script src="/?help\.js"></script>', page), 'Help script must load in both standalone and unified mounts'
        assert 'aria-label="Help:' in page


def test_teaching_text_is_not_persistently_rendered_on_pages():
    home = (STATIC / 'index.html').read_text(encoding='utf-8').split('<template id="help-')[0]
    learning = (STATIC / 'learning.html').read_text(encoding='utf-8').split('<template id="help-')[0]
    history = (STATIC / 'events.html').read_text(encoding='utf-8').split('<template id="help-')[0]
    assert 'How is each Cluster Head' not in learning
    assert 'What would the model guess' not in learning
    assert '<p class="event-window-explanation">' not in home
    assert 'Protocol fields and data completeness' not in history
    assert '<div id="linkNotice"' in home
    assert 'id="protocolHealth"' in home
    for page in (home, learning, history):
        assert 'data-help=' in page


def test_help_script_supports_escape_close_and_focus_restoration():
    code = (STATIC / 'help.js').read_text(encoding='utf-8')
    assert 'dialog.showModal()' in code
    assert 'dialog.close()' in code
    assert "dialog.addEventListener('close'" in code
    assert 'opener?.focus()' in code
    assert 'event.stopPropagation()' in code
    assert 'template.content.cloneNode(true)' in code
