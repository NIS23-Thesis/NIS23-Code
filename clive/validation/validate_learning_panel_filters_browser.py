"""Browser acceptance for master/follow/pinned CH filtering and digit sorting.

Uses illustrative fixtures; never claims these are experimental measurements.
"""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parents[1]/'visualizer'/'static'
OUTPUT = Path('/mnt/data')/'clive_per_ch_previews'
OUTPUT.mkdir(exist_ok=True)

def dataset(size=24):
    nodes=[]
    for i in range(1,size+1):
        counts=[0]*10
        counts[i%10]=i+7
        counts[(i+1)%10]=3
        counts[(i+2)%10]=1
        nodes.append({
            'name':f'CH-{i}',
            'points':[{'time_s':2.0,'accuracy':.2+i/200,'revision':i,'cause':'local_training',
                       'macro_f1':.19+i/200,'per_class':[{'digit':k,'support':8,'precision':.2+k/100,'recall':.2+k/100,'f1':.2+k/100} for k in range(10)]},
                      {'time_s':4.0,'accuracy':.27+i/200,'revision':i+1,'cause':'peer_aggregation',
                       'macro_f1':.26+i/200}],
            'counters':{'trained':1,'aggregated':1},'model_revision':i+1,
            'final':{'status':'ok','accuracy':.27+i/200},
            'received_digit_distribution':{'counts':counts,'unclassified':0,'sample_source':'mnist'}
        })
    return {'run':'run-1','status':'complete','duration_s':10,'nodes':nodes}

def install(page,data):
    errors=[]
    page.on('pageerror',lambda exc:errors.append(str(exc)))
    page.set_content((BASE/'learning.html').read_text(),wait_until='domcontentloaded')
    for css in ('panel_pages.css','ui-shell.css','learning-dashboard.css'):
        page.add_style_tag(path=str(BASE/css))
    page.evaluate('''d => window.fetch = async url => ({ok: true, json: async () =>
      String(url).includes('/runs') ? {runs:[{name:'run-1',status:'complete'}]} : d})''',data)
    for js in ('ui-nav.js','help.js','learning.js'):
        page.add_script_tag(path=str(BASE/js))
    page.wait_for_timeout(220)
    assert not errors, errors
    return errors


def check(width,height):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('google-chrome'),
                                  headless=True,args=['--no-sandbox','--disable-gpu'])
        page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
        errors=install(page,dataset(50))
        assert page.locator('#chart').is_visible()
        assert 'Choose a Cluster Head' in page.locator('#distributionContext').inner_text()
        page.locator('#distributionCHSelect').select_option('CH-1')
        assert page.locator('.single-digit-row').count()==10
        assert page.locator('#distributionCHSelect option').count()==51
        assert page.locator('#nodeSelect option').count()==51
        assert page.locator('.single-digit-row').first.locator('.single-digit-label').inner_text()=='1'
        # Pin the digits panel to CH-20. Neither master nor chart changes.
        page.locator('#distributionCHSelect').select_option('CH-20')
        assert page.locator('#distributionContext').inner_text().startswith('CH-20')
        assert page.locator('.single-digit-row').first.locator('.single-digit-label').inner_text()=='0'
        page.locator('#nodeSelect').select_option('CH-50')
        assert page.locator('#distributionCHSelect').input_value()=='CH-20'
        assert page.locator('#distributionContext').inner_text().startswith('CH-20')
        assert page.locator('#comparison tr').count()==1
        assert page.locator('#comparison tr').first.inner_text().startswith('CH-50')
        # Chart and table can point at different CHs.
        page.locator('#chartCHSelect').select_option('CH-10')
        assert page.locator('.ch-legend-item').count()==1
        assert 'CH-10' in page.locator('.ch-legend-item').first.inner_text()
        page.locator('#qualityCHSelect').select_option('CH-3')
        assert page.locator('#comparison tr').first.inner_text().startswith('CH-3')
        assert page.locator('#nodeSelect').input_value()=='CH-50'
        # Details can be pinned to a fourth CH.
        page.locator('#detailsCHSelect').select_option('CH-4')
        assert 'CH-4' in page.locator('#history').inner_text()
        # Sort only the ten digits within the chosen CH.
        page.locator('#distributionSort').select_option('digit')
        assert [int(t) for t in page.locator('.single-digit-label').all_inner_texts()]==list(range(10))
        page.locator('#distributionSort').select_option('least')
        assert page.locator('.single-digit-row').first.locator('.single-digit-count').inner_text()=='0'
        page.locator('#distributionSort').select_option('most')
        assert page.locator('.single-digit-row').first.locator('.single-digit-label').inner_text()=='0'
        # Switch panel to follow master; master is still CH-50.
        page.locator('#distributionCHSelect').select_option('follow')
        assert page.locator('#distributionContext').inner_text().startswith('CH-50')
        # Restore the global all view and inspect individual CH by panel.
        page.locator('#nodeSelect').select_option('all')
        assert 'Choose a Cluster Head' in page.locator('#distributionContext').inner_text()
        assert 'Select a CH in this panel' in page.locator('#receivedDigitRows').inner_text()
        # Table sorting must not change chart filter.
        page.locator('#qualityCHSelect').select_option('all')
        page.locator('#qualitySort').select_option('revision')
        assert 'CH-50' in page.locator('#comparison tr').first.inner_text()
        assert page.locator('#chartCHSelect').input_value()=='CH-10'
        # Chart legend click affects chart ONLY.
        page.locator('#chartCHSelect').select_option('all')
        page.locator('.ch-legend-item[data-focus-ch="CH-2"]').click()
        assert page.locator('#chartCHSelect').input_value()=='CH-2'
        assert page.locator('#nodeSelect').input_value()=='all'
        # Responsive, no clipping/JS issues.
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),f'overflow at {width}'
        assert not errors,errors
        # Screenshot the most sensible 3-panel example instead of huge 50-line chart.
        page.locator('#nodeSelect').select_option('CH-2')
        page.locator('#chartCHSelect').select_option('follow')
        page.locator('#distributionCHSelect').select_option('CH-20')
        page.locator('#qualityCHSelect').select_option('follow')
        page.locator('#detailsCHSelect').select_option('follow')
        page.screenshot(path=str(OUTPUT/f'per-ch-{width}.png'),full_page=True)
        page.close()
        browser.close()
        print(f'{width}x{height}: master, independent panels, CH-50, sorting, legend, responsive PASS')

if __name__=='__main__':
    for w,h in ((1440,900),(1280,800),(768,900),(390,844)):
        check(w,h)
