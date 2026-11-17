"""Isolated Chromium acceptance: results correctness at 24+ CHs and failed run loads.

Browser fixtures are synthetic. They are NEVER thesis experiment results.
"""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parents[1] / 'visualizer' / 'static'


def install(page, html, css, scripts, mock):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content((BASE / html).read_text(), wait_until='domcontentloaded')
    for filename in css:
        page.add_style_tag(path=str(BASE / filename))
    # JS function is passed as source; returning JSON-friendly fixture is easier via constants.
    page.evaluate('window.history.replaceState = () => {}')
    page.evaluate('''fixture => {
      window.fetch = async (url) => {
        const path = String(url);
        if (path.includes('/api/runs')) return {ok:true,json:async()=>fixture.runs};
        if (path.includes('/api/events/archive')) {
          if(path.includes('run-2'))return {ok:false,status:503,json:async()=>({})};
          return {ok:true,json:async()=>fixture.archive};
        }
        if (path.includes('/api/learning')) return {ok:true,json:async()=>fixture.learning};
        return {ok:false,status:404,json:async()=>({})};
      };
    }''', mock)
    for filename in scripts:
        page.add_script_tag(path=str(BASE / filename))
    page.wait_for_timeout(200)
    assert errors == [], errors
    return errors


def learning_data():
    nodes=[]
    for i in range(1,51):
        counts=[0]*10
        counts[i%10]=i
        nodes.append({'name':f'CH-{i}', 'points':[
           {'time_s':1.,'revision':4,'accuracy':.25+i/1000, 'macro_f1':.31,'cause':'local_training'},
           {'time_s':2.,'revision':5,'accuracy':.26+i/1000, 'cause':'peer_aggregation'}],
           'model_revision':5,'counters':{'trained':1,'aggregated':1},
           'received_digit_distribution':{'counts':counts,'unclassified':0},
           'final':{'status':'ok','accuracy':.29,'macro_f1':.33}})
    return {'run':'run-1','duration_s':10,'status':'complete','nodes':nodes}


def test_events(browser):
    page=browser.new_page(viewport={'width':1280,'height':850})
    try:
        errors=install(page,'events.html',['panel_pages.css','ui-shell.css'],
            ['ui-nav.js','help.js','event_stream.js','events.js'],
            {'runs':{'runs':[{'name':'run-1'},{'name':'run-2'}]},
             'archive':{'events':[{'time_s':1.5,'stream':'packet','event':'transmit','packet_type':0,
                                   'node':'CH-1','source':'CH-1','target':'SN-1','sequence_number':3}],
                        'has_more':False,'available':True}})
        assert 'HELLO' in page.locator('#rows').inner_text()
        page.locator('#runSelect').select_option('run-2')
        page.wait_for_timeout(100)
        assert 'HELLO' not in page.locator('#rows').inner_text(), 'stale previous run events remain'
        assert 'Events unavailable for this run' in page.locator('#rows').inner_text()
        assert 'run-2' in page.locator('#summary').inner_text()
        assert page.locator('#next').is_disabled() and page.locator('#previous').is_disabled()
        assert errors == [], errors
    finally:page.close()


def test_learning(browser, width):
    page=browser.new_page(viewport={'width':width,'height':870})
    try:
        errors=install(page,'learning.html',['panel_pages.css','ui-shell.css','learning-dashboard.css'],
             ['ui-nav.js','help.js','learning.js'],
             {'runs':{'runs':[{'name':'run-1'}]},'learning':learning_data()})
        assert 'Model update events' in page.locator('.learning-kpis').inner_text()
        # Independent CH panel: All + Follow master must ask for explicit CH, not silently show CH-1.
        assert 'Choose a Cluster Head' in page.locator('#distributionContext').inner_text()
        page.locator('#distributionCHSelect').select_option('CH-25')
        assert 'CH-25' in page.locator('#distributionContext').inner_text()
        ch1=page.evaluate("chColor('CH-1')")
        ch25=page.evaluate("chColor('CH-25')")
        ch50=page.evaluate("chColor('CH-50')")
        assert len({ch1,ch25,ch50}) == 3,(ch1,ch25,ch50)
        assert ch25 == page.evaluate("chColor('CH-25')")
        # Latest accuracy still exists; macro-F1 missing from newer point uses last valid revision.
        page.locator('#metricSelect').select_option('macro_f1')
        page.locator('#qualityCHSelect').select_option('CH-25')
        quality=page.locator('#comparison').inner_text()
        assert '31.00%' in quality,quality
        assert 'rev 4' in quality,quality
        assert 'rev 5' not in quality.split('31.00%')[-1].split('33.00%')[0],quality
        assert '33.00%' in quality,quality
        assert page.locator('#sampledAccuracy').inner_text()=='31.00%'
        # Global and local choices are independent across 50 CHs.
        page.locator('#nodeSelect').select_option('CH-50')
        assert 'CH-25' in page.locator('#distributionContext').inner_text()
        assert page.locator('#comparison').inner_text().startswith('CH-25')
        assert 'CH-50' in page.locator('#chartCHSelect').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'),width
        assert errors == [], errors
    finally:page.close()


# Separate browser check for the Studio History UI using fully intercepted local HTTP
# responses, so browser actions are exercised without relying on localhost access.
def test_history_ui(browser):
    import json
    import sys
    root = BASE.parents[1]
    sys.path.insert(0, str(root))
    from control_center import DEFAULT_SCENARIO
    control_assets = root / 'control_ui'
    preset_id = 'demos/panel-01-static-baseline-real-mnist-2ch-2sn.json'
    scenario = json.loads((root / 'configs' / preset_id).read_text())
    runs = [{'name':f'run-{i}', 'status':'complete' if i%2==0 else 'failed',
             'duration_s':20, 'started_at':1700000000+i} for i in range(111,0,-1)]
    page=browser.new_page(viewport={'width':1440,'height':900})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    fixture={'runs':runs, 'preset':scenario,'id':preset_id,
             'bootstrap':{'presets':[{'id':preset_id,'name':'Static baseline','category':'demos','custom':False}],
                  'defaults':DEFAULT_SCENARIO,'examples':{'mobility':{},'link':{}},'visualizer_url':None,
                  'status':{'status':'idle','running':False,'run':None},'runs':runs[:30]}}
    try:
        page.set_content((control_assets/'index.html').read_text(),wait_until='domcontentloaded')
        for css in ('styles.css','ui-shell.css'):
            page.add_style_tag(path=str(control_assets/css))
        page.evaluate('window.history.replaceState = () => {}')
        page.evaluate('''f => window.fetch = async (url) => {
            const path=String(url), q=new URLSearchParams(path.split('?')[1]||'');
            let response;
            if(path==='/api/bootstrap')response=f.bootstrap;
            else if(path.startsWith('/api/scenario/'))response={id:f.id,scenario:f.preset};
            else if(path==='/api/status')response={status:'idle',running:false,run:null};
            else if(path.startsWith('/api/history')){
               let items=f.runs;
               const term=(q.get('search')||'').toLowerCase();
               if(term)items=items.filter(r=>r.name.includes(term)||r.status.includes(term));
               const offset=Number(q.get('offset')||0);
               const limit=q.get('limit')==='all'?items.length:Number(q.get('limit')||30);
               response={runs:items.slice(offset,offset+limit),total:items.length,
                    offset,limit,has_more:offset+limit<items.length};
            } else response={error:'Not available'};
            return {ok:true,json:async()=>response};
        }''',fixture)
        page.add_script_tag(path=str(control_assets/'app.js'))
        page.wait_for_timeout(300)
        page.get_by_role('button',name='Run History').click()
        page.wait_for_timeout(120)
        assert page.locator('.history-entry').count()==30
        assert '1–30 of 111' in page.locator('#historyPage').inner_text()
        assert page.locator('#runSelect option').count()==111
        page.locator('#historyNext').click()
        assert '31–60 of 111' in page.locator('#historyPage').inner_text()
        assert page.locator('.history-entry').count()==30
        page.locator('#historySearch').fill('run-108')
        page.wait_for_timeout(350)
        assert page.locator('.history-entry').count()==1
        assert 'run-108' in page.locator('.history-entry').first.inner_text()
        assert '1–1 of 1' in page.locator('#historyPage').inner_text()
        assert page.locator('#historyPrevious').is_disabled() and page.locator('#historyNext').is_disabled()
        assert errors==[],errors
    finally:
        page.close()

if __name__=='__main__':
    executable = shutil.which('chromium') or shutil.which('google-chrome')
    if not executable:
        raise SystemExit('Chromium browser not available; browser validation incomplete')
    with sync_playwright() as library:
        browser = library.chromium.launch(executable_path=executable,
                                          headless=True,args=['--no-sandbox','--disable-gpu'])
        test_events(browser)
        print('Event History: stale data cleared after failed run selection — PASS')
        for width in (1440,1280,768,390):
            test_learning(browser,width)
            print(f'Model Learning: metrics, CH colors, filters, empty selection at {width}px — PASS')
        test_history_ui(browser)
        print('Studio History: 111-run pagination/search and global selector — PASS')
        browser.close()
