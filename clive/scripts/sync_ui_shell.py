"""Generate one consistent CLIVE header and synchronize its shared CSS.

Run this after changing clive/ui_shared/shell.css or header content.
"""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'visualizer/static'
STUDIO=ROOT/'control_ui'
CSS=(ROOT/'ui_shared/shell.css').read_bytes()
for target in (STUDIO/'ui-shell.css',STATIC/'ui-shell.css'):
    target.write_bytes(CSS)

BRAND='<a class="clive-site-brand" data-studio-link="setup" href="/" aria-label="CLIVE home"><span class="clive-brand-emblem"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M6 21V11l10-6 10 6v10l-10 6z"/><path d="M6 21l10-10 10 10M16 11v16"/></svg></span><span class="clive-brand-wordmark">CLIVE<small>NETWORK EMULATOR</small></span></a>'
LABELS=('Setup','Topology','Learning','Events','Run History')

def header(page):
    links=[]
    for label in LABELS:
        active=(label==page)
        css='clive-site-link'+(' active' if active else '')
        current=' aria-current="page"' if active else ''
        if page in ('Setup','Run History') and label in ('Setup','Run History'):
            view='design' if label=='Setup' else 'history'
            links.append(f'<button type="button" class="{css}" data-view="{view}"{current}>{label}</button>')
        elif page in ('Setup','Run History'):
            target={'Topology':('topologyNavLink','/topology/'),'Learning':('studioLearningLink','/topology/learning.html'),'Events':('studioEventsLink','/topology/events.html')}[label]
            links.append(f'<a class="{css}" id="{target[0]}" href="{target[1]}"{current}>{label}</a>')
        elif label in ('Setup','Run History'):
            dest='setup' if label=='Setup' else 'history'
            links.append(f'<a class="{css}" data-studio-link="{dest}" href="/"{current}>{label}</a>')
        else:
            href={'Topology':'./','Learning':'learning.html','Events':'events.html'}[label]
            ids={'Topology':'','Learning':' id="learningPageLink"' if page=='Topology' else '', 'Events':' id="eventsPageLink"' if page=='Topology' else ''}
            data=' data-run-link' if page in ('Learning','Events') else ''
            links.append(f'<a class="{css}"{ids[label]}{data} href="{href}"{current}>{label}</a>')
    nav='<nav class="clive-site-nav" aria-label="CLIVE sections">'+''.join(links)+'</nav>'
    side='<div class="clive-site-context clive-run-context"><label class="clive-context-label" for="runSelect">RUN</label><select id="runSelect" aria-label="Choose results run"><option value="">No runs yet</option></select>'
    if page=='Setup':
        side+='<span id="stateBadge" class="state-badge idle" hidden aria-live="polite"><span></span>IDLE</span>'
    if page=='Topology':
        side+='<div id="statusPill" class="status waiting" hidden>WAITING</div>'
    side+='<button id="cliveThemeToggle" type="button" class="clive-theme-toggle" aria-pressed="false" aria-label="Switch to light mode" title="Switch to light mode"><span class="theme-icon" aria-hidden="true">☀</span><span class="theme-label">Light mode</span></button>'
    side+='</div>'
    return '<header class="clive-site-header" role="banner">'+BRAND+nav+side+'</header>'

pages=((STUDIO/'index.html','Setup'),(STATIC/'index.html','Topology'),(STATIC/'learning.html','Learning'),(STATIC/'events.html','Events'))
for file,page in pages:
    data=file.read_text(encoding='utf-8')
    data,count=re.subn(r'<header\b[^>]*\bclive-site-header\b[^>]*>.*?</header>',lambda m:header(page),data,count=1,flags=re.S)
    if count != 1:
        raise RuntimeError(f'Cannot find canonical header in {file}')
    file.write_text(data,encoding='utf-8')
    print(f'Updated {page} header')
