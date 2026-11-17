from pathlib import Path
from playwright.sync_api import sync_playwright
import shutil
import json, traceback
p=Path(__file__).resolve().parents[1]/'visualizer/static'
html=(p/'learning.html').read_text()
"""Optional Chromium visual/behavioral acceptance with synthetic API fixtures.
Requires playwright and a Chromium installation. Does not access a live CLIVE run.
"""
# Test values are synthetic fixtures, not results of an executed experiment.
def point(t,revision,cause,a):
 cm=[[3 if i==j else 0 for j in range(10)] for i in range(10)]
 return {'time_s':t,'revision':revision,'cause':cause,'accuracy':a,'macro_f1':a-.02,'weighted_f1':a-.01,'macro_precision':a-.015,'macro_recall':a-.025,
 'per_class':[{'digit':i,'support':18,'precision':a,'recall':a,'f1':a} for i in range(10)],'confusion_matrix':cm,
 'example_confidence':[{'true_digit':i,'predicted_digit':i,'confidence':[0.88 if j==i else 0.12/9 for j in range(10)]} for i in range(10)]}
data={'run':'run-1','status':'complete','duration_s':20,'nodes':[
 {'name':'CH-1','points':[point(2,1,'local_training',.22),point(4,2,'peer_aggregation',.29),point(6,3,'local_training',.32)],'final':{'status':'ok','accuracy':.32,'macro_f1':.30},'model_revision':3},
 {'name':'CH-2','points':[point(2.4,1,'local_training',.18),point(5,2,'peer_aggregation',.26),point(8,3,'local_training',.30)],'final':{'status':'ok','accuracy':.30,'macro_f1':.29},'model_revision':3}]}
with sync_playwright() as pw:
 browser=pw.chromium.launch(**({'executable_path': shutil.which('chromium') or shutil.which('google-chrome')} if shutil.which('chromium') or shutil.which('google-chrome') else {}),headless=True,args=['--no-sandbox','--disable-gpu'])
 for width,height in [(1440,900),(1280,800),(768,900),(390,844)]:
  page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
  err=[];page.on('pageerror',lambda e:err.append(str(e)))
  page.set_content(html,wait_until='domcontentloaded')
  for x in ['panel_pages.css','ui-shell.css','learning-dashboard.css']:
   page.add_style_tag(path=str(p/x))
  page.evaluate('(data)=>window.fetch=async (url)=>({ok:true,json:async()=>url.includes("/runs")?{runs:[{name:"run-1",status:"complete"}]}:data})',data)
  for x in ['ui-nav.js','help.js','learning.js']:
   page.add_script_tag(path=str(p/x))
  page.wait_for_timeout(800)
  kpis=page.locator('#sampledAccuracy').inner_text(),page.locator('#updateCount').inner_text()
  panels=page.locator('.learning-grid > .card').count()
  table_count=page.locator('#comparison tr').count()
  per_digit=page.locator('#perClassRows tr').count()
  overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
  header=page.locator('.clive-site-header').bounding_box()
  print(width,'kpis',kpis,'panels',panels,'qualityrows',table_count,'digits',per_digit,'overflow',overflow,'headerH',header['height'],'errors',err)
  assert not err,err
  assert kpis[0]!='—' and kpis[1]=='6',kpis
  assert per_digit==10
  assert not overflow
  if width==1440:
   page.locator('.help-trigger[data-help="learning-chart"]').click()
   assert page.locator('.clive-help-dialog').is_visible()
   page.keyboard.press('Escape')
   assert not page.locator('.clive-help-dialog').is_visible()
   page.locator('#nodeSelect').select_option('CH-2')
   assert page.locator('#updateCount').inner_text()=='3'
   page.locator('#metricSelect').select_option('macro_f1')
   assert 'F1' in page.locator('#chartHeading').inner_text()
   assert page.locator('#perClassRows tr').count()==10
   # A failed fetch must not leave the previous run's measurements visible.
   page.evaluate('window.fetch=async()=>({ok:false,status:503})')
   page.locator('#refresh').click()
   page.wait_for_timeout(100)
   assert 'Could not load' in page.locator('#learningNotice').inner_text()
   assert page.locator('#sampledAccuracy').inner_text()=='—'
   assert page.locator('#comparison').inner_text().startswith('No learning data available')
   assert not err,err
  page.close()
 browser.close()
print('Learning dashboard isolated browser regression: PASS')
