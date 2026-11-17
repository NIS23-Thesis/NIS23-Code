'use strict';
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
// Identity, not array position, determines a Cluster Head's color.
// Keeping these values stable across filters, runs and reloads avoids CH-2
// silently inheriting CH-1's color when the user filters the dashboard.
const CH_PALETTE=Object.freeze([
 '#67ddd0', // CH-1  • teal
 '#ffca78', // CH-2  • amber
 '#9facff', // CH-3  • periwinkle
 '#f3a4ce', // CH-4  • rose
 '#bedb83', // CH-5  • lime
 '#ff967f', // CH-6  • coral
 '#8bd9ff', // CH-7  • sky
 '#c5a3fa', // CH-8  • violet
 '#f5df83', // CH-9  • yellow
 '#94e7ad', // CH-10 • green
 '#ef9cb0', // CH-11 • pink
 '#afd6e4', // CH-12 • frost
 '#ffad5c', // CH-13 • orange
 '#f48fbd', // CH-14 • orchid
 '#78c8db', // CH-15 • ice
 '#d9a0ff', // CH-16 • lilac
 '#e8d57a', // CH-17 • gold
 '#76e0a6', // CH-18 • jade
 '#fdac95', // CH-19 • peach
 '#b4c9ff', // CH-20 • lavender
 '#ddba83', // CH-21 • sand
 '#8ccdb0', // CH-22 • seafoam
 '#dbadf2', // CH-23 • thistle
 '#a9d0e9'  // CH-24 • pale blue
]);
const CH_PALETTE_LIGHT=Object.freeze([
 '#008b81','#af6913','#595dba','#a53d75','#68832c','#ba5a3f',
 '#087eaa','#7f56b0','#927700','#277a50','#a64462','#386e84',
 '#b36b21','#ab4882','#287d8f','#8452bf','#8c7c1e','#198a5d',
 '#c16b50','#5564bd','#976b35','#347f66','#9955aa','#317fa2'
]);
function chColor(name) {
 const palette=(typeof window !== "undefined" && window.CLIVETheme?.isLight()) ? CH_PALETTE_LIGHT : CH_PALETTE;
 const label=String(name??'').trim();
 const numbered=/^CH[-\s_]*0*([1-9]\d*)$/i.exec(label);
 if(numbered){
  const index=Number(numbered[1]);
  if(index<=palette.length)return palette[index-1];
  // Beyond the first 24 CHs use an identity-derived, non-repeating hue.
  // Labels always accompany swatches: color alone is not an identifier.
  const hue=(37+(index-palette.length-1)*137.508)%360;
  return (typeof window !== "undefined" && window.CLIVETheme?.isLight()) ? `hsl(${hue.toFixed(3)} 62% 39%)` : `hsl(${hue.toFixed(3)} 72% 72%)`;
 }
 // Stable fallback for nonstandard names. Never depend on series order.
 let hash=2166136261;
 for(const char of label.toUpperCase()){hash=Math.imul(hash^char.charCodeAt(0),16777619)>>>0;}
 return palette[hash%palette.length];
}
function chMark(name){
 const color=chColor(name);
 return `<span class="ch-label" style="--ch-color:${color}"><span class="ch-swatch" aria-hidden="true"></span>${esc(name)}</span>`;
}
let selectedRun=params.get('run')||window.CLIVERunSelection?.read()||'', modelData=null, fullHistory=false;
let hoverTime=null;
// Revision selection is presentation state only; the snapshots come from the saved run.
let activeRevision=null, visibleHistoryRows=[];
const metrics=Object.freeze({accuracy:'Accuracy',macro_f1:'Macro F1',weighted_f1:'Weighted F1',
 macro_precision:'Macro precision',macro_recall:'Macro recall'});
const selectedMetric=()=>document.getElementById('metricSelect').value;
const metricValue=(p)=>Number.isFinite(p?.[selectedMetric()])&&p[selectedMetric()]>=0&&p[selectedMetric()]<=1?p[selectedMetric()]:null;

let hoverFrame=null;
let loadingVersion=0;

function syncNavigation() {
 const suffix=selectedRun?`?run=${encodeURIComponent(selectedRun)}`:'';
 for(const a of document.querySelectorAll('.clive-site-nav a[data-run-link]')) {
  const base=a.getAttribute('href').split('?')[0];
  a.href=base+suffix;
 }
}
const pct=n=>Number.isFinite(n)?(n*100).toFixed(2)+'%':'—';
const esc=s=>String(s??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
async function api(path){const r=await fetch(window.location.pathname.startsWith("/topology/") ? path.replace(/^\/api(?=\/)/,"/api/viz") : path,{cache:'no-store'});if(!r.ok)throw Error(`HTTP ${r.status}`);return await r.json()}
async function refreshRuns(){
 const data=await api('/api/runs'),runs=data.runs||[];
 if(!runs.length){$('runSelect').innerHTML='<option value="">No runs yet</option>';return false}
 if(!runs.some(r=>r.name===selectedRun))selectedRun=runs[0].name;
 $('runSelect').innerHTML=runs.map(r=>`<option value="${esc(r.name)}" ${r.name===selectedRun?'selected':''}>${esc(window.CLIVERunSelection?.label(r) || r.name)}</option>`).join('');
 window.CLIVERunSelection?.write(selectedRun);
 syncNavigation();
 return true;
}
function showNotice(message,isError=false){const el=$('learningNotice');el.textContent=message;el.dataset.error=isError?'true':'false';el.hidden=!message}
function clearLearning(){
 for(const id of ['sampledAccuracy','updateCount','trainCount','aggregateCount'])$(id).textContent='—';
 for(const [id,count] of [['comparison',5],['history',7],['perClassRows',5],['confusionRows',11]])$(id).innerHTML=`<tr><td colspan="${count}">No learning data available.</td></tr>`;
 $('exampleSummary').textContent='No prediction data available.';$('exampleConfidence').innerHTML='';
 $('legend').innerHTML='';$('chartEmpty').hidden=false;$('chartTooltip').hidden=true;
 $('digitContext').textContent='';$('predictionContext').textContent='';$('distributionContext').textContent='';
 $('receivedDigitRows').innerHTML='<p class="muted">No received-digit data available.</p>';
 activeRevision=null;$('revisionChartControls').hidden=true;$('distributionRevisionSelect').innerHTML='<option value="">Full-run totals</option>';$('distributionHeading').textContent='Digits received';
 
 const canvas=$('chart'),ctx=canvas.getContext('2d');ctx?.clearRect(0,0,canvas.width,canvas.height);
}
function detailNode(nodes){return nodes.find(n=>(n.points||[]).length||n.final?.status==='ok')||nodes[0]||null}
async function load(){
 const version=++loadingVersion;
 if(!modelData)showNotice('Loading learning results…');
 try{
  if(!await refreshRuns()){modelData=null;showNotice('No experiment results yet. Run an experiment from Setup.');clearLearning();return}
  const data=await api(`/api/learning?run=${encodeURIComponent(selectedRun)}`);
  if (version!==loadingVersion || selectedRun!==data.run && data.run) return;
  modelData=data; showNotice('');
  if(!Array.isArray(data.nodes))throw Error('Malformed learning response');
  const names=data.nodes.map(n=>n.name).sort((a,b)=>compareCHNames({name:a},{name:b}));const previous=$('nodeSelect').value;
  $('nodeSelect').innerHTML='<option value="all">All CHs</option>'+names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  if(names.includes(previous))$('nodeSelect').value=previous;
  syncPanelChoices(names);
  render();
 }catch(e){if(version===loadingVersion){modelData=null;showNotice('Could not load learning results: '+e.message,true);clearLearning()}}
}
// Master CH is an opt-in default, NOT a forced filter for pinned panels.
function selectedNodes(){return panelNodes('follow')}
function panelNodes(selection) {
 const all=modelData?.nodes||[], master=$('nodeSelect').value;
 const target=selection==='follow'?master:selection;
 return all.filter(n=>target==='all'||n.name===target);
}
function panelSelection(id){return $(id)?.value||'follow'}
function panelNodesFor(id){return panelNodes(panelSelection(id))}
function specificNodeFor(id) {
 const selection=panelSelection(id);
 // A single-CH chart cannot represent "All". Do not silently substitute CH-1.
 if(selection==='all'||(selection==='follow'&&$('nodeSelect').value==='all'))return null;
 const nodes=panelNodesFor(id);
 return nodes[0]||null;
}
function syncPanelChoices(names) {
 for(const id of ['chartCHSelect','distributionCHSelect','qualityCHSelect','detailsCHSelect']) {
  const el=$(id),old=el.value;
  const choices= id==='distributionCHSelect' ? [] : ['<option value="all">All CHs</option>'];
  el.innerHTML='<option value="follow">Follow master</option>'+choices.join('')+names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  el.value=names.includes(old)||old==='follow'||(old==='all'&&id!=='distributionCHSelect')?old:'follow';
 }
}
function nodePoints(node){return(Array.isArray(node?.points)?node.points:[]).filter(p=>p&&Number.isFinite(p.time_s)&&p.time_s>=0).slice().sort((a,b)=>a.time_s-b.time_s)}
function lastValidMeasurement(node){
 const points=nodePoints(node);
 for(let i=points.length-1;i>=0;i--)if(metricValue(points[i])!==null)return points[i];
 return null;
}

// An incomplete or malformed measurement must never create a misleading line
// segment or break nearest-point lookups. The chart plots *recorded* values only.
function measuredSeries(nodes) {
 return nodes.map(node => ({...node, points: (node.points || [])
   .filter(p => Number.isFinite(p.time_s) && Number.isFinite(metricValue(p))
     && p.time_s >= 0 && metricValue(p) >= 0 && metricValue(p) <= 1)
   .sort((a,b) => a.time_s - b.time_s)}))
   .filter(node => node.points.length > 0);
}
function render(){
 const nodes=selectedNodes();
 const latestPerNode=nodes.map(lastValidMeasurement).filter(Boolean);
 const meanLatest=latestPerNode.length?latestPerNode.reduce((sum,p)=>sum+metricValue(p),0)/latestPerNode.length:null;
 $('sampledAccuracy').textContent=meanLatest===null?'Not recorded':pct(meanLatest);
 $('metricSummaryLabel').textContent=`Mean latest ${metrics[selectedMetric()].toLowerCase()}`;
 $('chartHeading').textContent=`MNIST ${metrics[selectedMetric()]} by simulation time`;
 // Summary figures follow only the master CH selector.
 const countOperations=(counterName,eventName)=>nodes.reduce((total,node)=>{
  const counter=node.counters?.[counterName];
  return total+(Number.isSafeInteger(counter)&&counter>=0?counter:nodePoints(node).filter(p=>p.cause===eventName).length);
 },0);
 const training=countOperations('trained','local_training');
 const aggregations=countOperations('aggregated','peer_aggregation');
 $('updateCount').textContent=String(training+aggregations);
 $('trainCount').textContent=String(training);
 $('aggregateCount').textContent=String(aggregations);
 renderQuality(panelNodesFor('qualityCHSelect'));
 renderHistory(panelNodesFor('detailsCHSelect'));
 renderReceivedDigits(specificNodeFor('distributionCHSelect'));
 renderPerClass(panelNodesFor('detailsCHSelect'));
 renderExamples(panelNodesFor('detailsCHSelect'));
 draw(panelNodesFor('chartCHSelect'));
}
function renderQuality(nodes){
 const sorted=[...nodes],sort=$('qualitySort').value;
 const latestValue=n=>metricValue(lastValidMeasurement(n));
 sorted.sort((a,b)=>{
  if(sort==='metric')return (latestValue(b)??-1)-(latestValue(a)??-1)||compareCHNames(a,b);
  if(sort==='revision')return (Number(b.model_revision)||0)-(Number(a.model_revision)||0)||compareCHNames(a,b);
  return compareCHNames(a,b);
 });
 $('comparison').innerHTML=sorted.map(n=>{
  const latest=nodePoints(n).at(-1),sample=lastValidMeasurement(n),latestMetric=metricValue(sample);
  const finalMetric=n.final?.status==='ok'?n.final?.[selectedMetric()]:null;
  const sampleOrigin=sample?`<small class="metric-provenance">rev ${esc(sample.revision??'—')} · ${Number(sample.time_s).toFixed(3)}s</small>`:'';
  return `<tr><td>${chMark(n.name)}</td><td>${esc(latest?.revision??n.model_revision??'—')}</td><td>${pct(latestMetric)}${sampleOrigin}</td><td>${pct(finalMetric)}</td><td>${latest?`${Number(latest.time_s).toFixed(3)}s · ${latest.cause==='local_training'?'Train':'Aggregate'}`:'—'}</td></tr>`;
 }).join('')||'<tr><td colspan="5">No Cluster Heads</td></tr>';
}
function revisionEvidence(point) {
 if (point.cause==='local_training') {
  const counts=point.digit_counts_before_training;
  if (!Array.isArray(counts)||counts.length!==10||!counts.every(n=>Number.isSafeInteger(n)&&n>=0))
   return '<span class="revision-evidence-muted">Digit snapshot not recorded in this run</span>';
  const received=counts.reduce((a,b)=>a+b,0);
  const digits=counts.map((n,i)=>n?`${i} × ${n}`:null).filter(Boolean);
  const newCounts=point.digit_counts_since_previous_training;
  const fresh=Array.isArray(newCounts)&&newCounts.length===10&&newCounts.every(n=>Number.isSafeInteger(n)&&n>=0)
    ?newCounts.reduce((a,b)=>a+b,0):null;
  const used=point.digit_counts_in_training;
  const usedValid=Array.isArray(used)&&used.length===10&&used.every(n=>Number.isSafeInteger(n)&&n>=0);
  const batch=usedValid?used.map((n,i)=>n?`${i} × ${n}`:null).filter(Boolean):[];
  const batchLine=usedValid?`<small>Used for this training step only: ${esc(batch.join(' · ')||'No labeled digits')}</small>`:'';
  return `<span class="revision-evidence-title">At revision: ${received} received so far (cumulative)${fresh===null?'':` · ${fresh} new since last training`}</span><small>Lifetime received up to here: ${esc(digits.join(' · ')||'No labeled digits')}</small>${batchLine}`;
 }
 if (point.cause==='peer_aggregation') {
  const from=point.trigger_peer||((Array.isArray(point.trigger_peer_overlay)&&point.trigger_peer_overlay.length===2)?point.trigger_peer_overlay.join(':'):null);
  const id=Number.isInteger(point.trigger_update_id)?` · update ID ${point.trigger_update_id}`:'';
  return `<span class="revision-evidence-title">Received MODEL_UPDATE${from?` from ${esc(from)}`:''}${id}</span><small>Applied peer aggregation; no new local training</small>`;
 }
 return '<span class="revision-evidence-muted">Revision source not recorded</span>';
}
function sameRevision(point,selection){
 return !!(point&&selection&&point.node===selection.node&&point.time_s===selection.time_s&&point.revision===selection.revision&&point.cause===selection.cause);
}
function renderHistory(nodes){
 const points=nodes.flatMap(n=>{
  const all=nodePoints(n);
  let previousMeasured=null;
  return all.map(p=>{
   const value=metricValue(p),metricDelta=value!==null&&previousMeasured!==null?value-previousMeasured:null;
   if(value!==null)previousMeasured=value;
   return {...p,node:n.name,metricDelta};
  });
 });
 const sort=$('historySort').value;
 points.sort((a,b)=>sort==='oldest'?a.time_s-b.time_s:b.time_s-a.time_s);
 visibleHistoryRows=points.slice(0,fullHistory?2000:12);
 $('history').innerHTML=visibleHistoryRows.map((p,index)=>`<tr class="revision-history-row${sameRevision(p,activeRevision)?' is-selected':''}" data-revision-index="${index}"><td data-label="Time"><button type="button" class="revision-inspect-button" data-revision-index="${index}" aria-pressed="${sameRevision(p,activeRevision)?'true':'false'}" aria-label="Show ${esc(p.node)} revision ${esc(p.revision??'unknown')} digits in chart">${Number(p.time_s).toFixed(3)}s</button></td><td data-label="CH">${chMark(p.node)}</td><td data-label="Action">${p.cause==='local_training'?'Local training':'Peer aggregation'}</td><td data-label="Revision">${esc(p.revision??'—')}</td><td data-label="Evidence" class="revision-evidence">${revisionEvidence(p)}</td><td data-label="Metric">${pct(metricValue(p))}</td><td data-label="Δ">${p.metricDelta==null?'—':(p.metricDelta>=0?'+':'')+(p.metricDelta*100).toFixed(2)+' pts'}</td></tr>`).join('')||'<tr><td colspan="7">No learning history recorded</td></tr>';
}
function syncRevisionPicker(node){
 const picker=$('distributionRevisionSelect');
 if(!node){picker.innerHTML='<option value="">Choose a CH first</option>';picker.disabled=true;return;}
 const points=nodePoints(node);
 const selected=activeRevision&&activeRevision.node===node.name
   ?points.findIndex(p=>sameRevision({...p,node:node.name},activeRevision)):-1;
 picker.innerHTML='<option value="">Full-run totals</option>'+points.map((p,i)=>
   `<option value="${i}">Rev ${esc(p.revision??'—')} · ${p.cause==='local_training'?'Training':'Peer aggregation'} · ${Number(p.time_s).toFixed(3)}s</option>`).join('');
 picker.disabled=!points.length;
 picker.value=selected<0?'':String(selected);
}
function selectRevision(row){
 if(!row)return;
 activeRevision={node:row.node,time_s:row.time_s,revision:row.revision,cause:row.cause};
 // Pin only the digit panel. The chart, quality panel, and other filters stay independent.
 const selection=$('distributionCHSelect');
 if(Array.from(selection.options).some(option=>option.value===row.node))selection.value=row.node;
 $('revisionDigitMode').value='used';
 renderHistory(panelNodesFor('detailsCHSelect'));
 renderReceivedDigits(specificNodeFor('distributionCHSelect'));
}
// CH-side lifetime counts of unique, accepted SENSOR_DATA messages.
// The comparison ranks recorded digits; never infer received data from model predictions.
function digitRecordFromCounts(counts,unknown=0){
 if(!Array.isArray(counts)||counts.length!==10||!counts.every(v=>Number.isSafeInteger(v)&&v>=0))return null;
 const classified=counts.reduce((sum,count)=>sum+count,0);
 const missing=Number.isSafeInteger(unknown)&&unknown>=0?unknown:0;
 const ranked=counts.map((count,digit)=>({digit,count,share:classified?count/classified:0}))
   .filter(item=>item.count>0).sort((a,b)=>b.count-a.count||a.digit-b.digit);
 return {counts,classified,unknown:missing,total:classified+missing,ranked,dominance:ranked[0]?.share??0};
}
function distributionRecord(node){
 const source=node.received_digit_distribution;
 return digitRecordFromCounts(source?.counts,source?.unclassified);
}
function compareCHNames(a,b){
 const ai=/^CH[-_ ]*0*([1-9]\d*)$/i.exec(a.name||'');
 const bi=/^CH[-_ ]*0*([1-9]\d*)$/i.exec(b.name||'');
 if(ai&&bi)return Number(ai[1])-Number(bi[1]);
 if(ai)return -1;
 if(bi)return 1;
 return String(a.name||'').localeCompare(String(b.name||''),undefined,{numeric:true});
}
// Single-CH distribution: one shared x-axis, ten comparable bars, local sorting.
function renderReceivedDigits(node) {
 const root=$('receivedDigitRows'),controls=$('revisionChartControls'),modeControl=$('revisionDigitModeControl');
 const heading=$('distributionHeading');
 let point=null;
 if(node&&activeRevision&&activeRevision.node===node.name){
  point=nodePoints(node).find(p=>sameRevision({...p,node:node.name},activeRevision))||null;
 }
 controls.hidden=!node;
 syncRevisionPicker(node);
 modeControl.hidden=!point||point.cause!=='local_training';
 heading.textContent=point?.cause==='local_training'?'Training digit snapshot':point?.cause==='peer_aggregation'?'Received model update':'Digits received';
 if(!node){
  const all=modelData?.nodes||[];
  $('distributionContext').textContent=all.length?'Choose a Cluster Head':'No Cluster Heads';
  $('distributionExplainer').textContent='Choose a CH in this panel to inspect its received digits and recorded revisions.';
  root.innerHTML=all.length?'<p class="muted">Select a CH in this panel (or choose a specific Master CH) to inspect its digit distribution.</p>':'<p class="muted">No Cluster Head to display.</p>';
  return;
 }
 const color=chColor(node.name);
 if(point?.cause==='peer_aggregation'){
  const from=point.trigger_peer||((Array.isArray(point.trigger_peer_overlay)&&point.trigger_peer_overlay.length===2)?point.trigger_peer_overlay.join(':'):null);
  const id=Number.isInteger(point.trigger_update_id)?` · Update ID ${point.trigger_update_id}`:'';
  $('distributionExplainer').textContent='This revision received and aggregated a peer model. It did not consume a new digit batch.';
  $('distributionContext').innerHTML=`<span class="distribution-current" style="--distribution-ch-color:${color}"><span class="ch-swatch" aria-hidden="true"></span>${esc(node.name)}</span><strong>Revision ${esc(point.revision??'—')} · ${Number(point.time_s).toFixed(3)}s</strong><span>Peer aggregation</span>`;
  root.innerHTML=`<div class="revision-peer-message"><strong>Received MODEL_UPDATE${from?` from ${esc(from)}`:''}${id}</strong><span>The received peer model was aggregated. No new local training occurred in this revision; no digit batch was consumed by aggregation.</span></div>`;
  return;
 }
 const mode=$('revisionDigitMode').value;
 $('distributionExplainer').textContent=point ? (mode==='used'?'The digits consumed by this training step, not the CH lifetime total.':mode==='new'?'New observations received since the previous local training step.':'Cumulative observations accepted by this CH up to the selected revision, including earlier steps.') : 'Cumulative unique digit observations received over the entire run. Select a revision to see one training batch.';
 let record=point?digitRecordFromCounts(mode==='before'?point.digit_counts_before_training:mode==='new'?point.digit_counts_since_previous_training:point.digit_counts_in_training):distributionRecord(node);
 if(!record){
  $('distributionContext').textContent=point?`${node.name} · Revision ${point.revision??'—'} · ${Number(point.time_s).toFixed(3)}s`:node.name;
  root.innerHTML=`<div class="distribution-single-empty" style="--distribution-ch-color:${color}"><strong>${chMark(node.name)}</strong><span>${point?'Digit snapshot not recorded for this revision':modelData?.status==='running'?'Available after the run finishes':'Not recorded for this run'}</span></div>`;
  return;
 }
 const top=record.ranked[0];
 if(point){
  const before=digitRecordFromCounts(point.digit_counts_before_training);
  const fresh=digitRecordFromCounts(point.digit_counts_since_previous_training);
  const modeDescription=mode==='before'?'received so far (cumulative)':mode==='new'?'new since previous training':'used in this training step';
  $('distributionContext').innerHTML=`<span class="distribution-current" style="--distribution-ch-color:${color}"><span class="ch-swatch" aria-hidden="true"></span>${esc(node.name)}</span><strong>Revision ${esc(point.revision??'—')} · ${Number(point.time_s).toFixed(3)}s</strong><span>${record.total.toLocaleString('en-US')} digits ${modeDescription}</span>${before&&mode==='used'?`<span>${before.total} cumulative received${fresh?` · ${fresh.total} new`:''}</span>`:''}`;
 }else{
  $('distributionContext').innerHTML=`<span class="distribution-current" style="--distribution-ch-color:${color}"><span class="ch-swatch" aria-hidden="true"></span>${esc(node.name)}</span><span>${record.total.toLocaleString('en-US')} received over full run</span>${top?`<span>Top digit <strong>${top.digit}</strong> · ${(top.share*100).toFixed(0)}%</span>`:''}${record.unknown?`<span>${record.unknown} unclassified</span>`:''}`;
 }
 const ranked=record.counts.map((count,digit)=>({digit,count,share:record.classified?count/record.classified:0}));
 const sort=$('distributionSort').value;
 if(sort==='most')ranked.sort((a,b)=>b.count-a.count||a.digit-b.digit);
 if(sort==='least')ranked.sort((a,b)=>a.count-b.count||a.digit-b.digit);
 const max=Math.max(1,...record.counts);
 root.innerHTML=`<div class="single-digit-bars" style="--distribution-ch-color:${color}" role="list" aria-label="${point?`Revision ${esc(point.revision??'—')} ${esc(mode)}`:'Full-run'} digit counts for ${esc(node.name)}, sorted by ${esc(sort)}">
  ${ranked.map(item=>`<div class="single-digit-row${top&&item.digit===top.digit?' is-dominant':''}" role="listitem" aria-label="Digit ${item.digit}: ${item.count}, ${(item.share*100).toFixed(1)} percent"><span class="single-digit-label">${item.digit}</span><div class="single-digit-track"><span class="single-digit-fill" style="width:${(item.count/max*100).toFixed(4)}%"></span></div><strong class="single-digit-count">${item.count.toLocaleString('en-US')}</strong><small>${(item.share*100).toFixed(0)}%</small></div>`).join('')}
 </div>`;
}

function renderPerClass(nodes) {
 const body=$('perClassRows'), matrixBody=$('confusionRows');
 const target=detailNode(nodes);
 $('digitContext').textContent=nodes.length>1 && target ? `${target.name} preview` : (target?.name||'');
 const last=nodePoints(target).at(-1);
 const perClass=(Array.isArray(last?.per_class)&&last.per_class.length?last.per_class:null) || target?.final?.per_class;
 if (!perClass?.length) {
   body.innerHTML='<tr><td colspan="5">No per-digit metrics recorded for this Cluster Head.</td></tr>';
   matrixBody.innerHTML='<tr><td colspan="11">No confusion matrix recorded.</td></tr>';
   return;
 }
 const sorted=[...perClass];
 const sort=$('perDigitSort').value;
 if(sort==='f1_high')sorted.sort((a,b)=>(Number(b.f1)||0)-(Number(a.f1)||0)||Number(a.digit)-Number(b.digit));
 else if(sort==='f1_low')sorted.sort((a,b)=>(Number(a.f1)||0)-(Number(b.f1)||0)||Number(a.digit)-Number(b.digit));
 else sorted.sort((a,b)=>Number(a.digit)-Number(b.digit));
 body.innerHTML=sorted.map(d=>`<tr><td>${esc(d.digit)}</td><td>${esc(d.support)}</td><td>${pct(d.precision)}</td><td>${pct(d.recall)}</td><td>${pct(d.f1)}</td></tr>`).join('');
 const matrix=last?.confusion_matrix || target?.final?.confusion_matrix;
 matrixBody.innerHTML=Array.isArray(matrix)&&matrix.length===10&&matrix.every(row=>Array.isArray(row)&&row.length===10)
  ? matrix.map((row,i)=>`<tr><th>${i}</th>${row.map((value,j)=>`<td class="${i===j?'matrix-correct':''}">${Number.isInteger(value)&&value>=0?value:'—'}</td>`).join('')}</tr>`).join('')
  : '<tr><td colspan="11">No confusion matrix available.</td></tr>';
}
function renderExamples(nodes) {
 const target=detailNode(nodes);
 $('predictionContext').textContent=nodes.length>1 && target ? `${target.name} preview` : (target?.name||'');
 const samples=nodePoints(target).at(-1)?.example_confidence || target?.final?.example_confidence;
 const digit=Number($('exampleDigit').value);
 const summary=$('exampleSummary'), bars=$('exampleConfidence');
 if (!Array.isArray(samples) || !Number.isInteger(digit) || digit<0 || digit>9) {
  summary.textContent='No example predictions recorded for this Cluster Head.';
  bars.innerHTML='';
  return;
 }
 const example=samples.find(row=>row.true_digit===digit);
 if (!example || !Array.isArray(example.confidence) || example.confidence.length!==10) {
  summary.textContent='No prediction available for this digit.'; bars.innerHTML='';return;
 }
 const p=example.confidence.map(Number);
 if (!p.every(v=>Number.isFinite(v)&&v>=0&&v<=1)) {
  summary.textContent='Invalid model confidence data.';bars.innerHTML='';return;
 }
 const choice=Number(example.predicted_digit);
 if(!Number.isInteger(choice)||choice<0||choice>9||Math.abs(p.reduce((a,b)=>a+b,0)-1)>0.02){summary.textContent='Invalid model prediction data.';bars.innerHTML='';return;}
 const correct=choice===digit;
 summary.textContent=`True ${digit} → Predicted ${choice} · ${(p[choice]*100).toFixed(2)}% (${correct?'correct':'incorrect'})`;
 bars.innerHTML=p.map((prob,i)=>`<div class="confidence-row${i===choice?' top-choice':''}" aria-label="Digit ${i}: ${(prob*100).toFixed(2)} percent model confidence">
  <span class="digit-label">${i}</span><span class="confidence-track"><span class="confidence-fill" style="width:${(prob*100).toFixed(4)}%"></span></span>
  <span class="confidence-value">${(prob*100).toFixed(2)}%</span></div>`).join('');
}
function draw(nodes){
 if(!modelData)return;
 const ready=measuredSeries(nodes);
 // Without metrics, keep the chart in its primary position but use a compact
 // honest empty state. Never replace its slot with a secondary heatmap.
 document.querySelector('.learning-grid')?.classList.toggle('no-quality-metrics',!ready.length);
 const canvas=$('chart'),el=canvas.parentNode,rect=el.getBoundingClientRect(); const w=Math.max(280,rect.width),h=Math.max(120,rect.height);
 const dpr=Math.max(1,window.devicePixelRatio||1);
 const pixelWidth=Math.round(w*dpr),pixelHeight=Math.round(h*dpr);
 if(canvas.width!==pixelWidth || canvas.height!==pixelHeight){
  canvas.width=pixelWidth;canvas.height=pixelHeight;
 }
 const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
 ctx.clearRect(0,0,w,h);
 $('chartEmpty').hidden=Boolean(ready.length);
 $('legend').innerHTML=ready.map(n=>`<button type="button" class="ch-legend-item" data-focus-ch="${esc(n.name)}" title="${esc($('chartCHSelect').value===n.name?'Follow master':`Focus chart on ${n.name}`)}" aria-pressed="${$('chartCHSelect').value===n.name?'true':'false'}" style="--ch-color:${chColor(n.name)}"><i></i>${esc(n.name)}</button>`).join('');
 if(!ready.length){$('chartTooltip').hidden=true;return}
 const l=54,r=18,t=20,b=43,maxT=Math.max(1,modelData.duration_s,...ready.flatMap(n=>n.points.map(p=>p.time_s)));
 const maximum=Math.max(...ready.flatMap(n=>n.points.map(p=>metricValue(p)??0)));
 const axisMax=Math.min(1,Math.max(.2,Math.ceil(maximum*1.2*5)/5));
 const X=x=>l+(w-l-r)*x/maxT,Y=y=>t+(h-t-b)*(1-y/axisMax);
 ctx.font='12px system-ui';ctx.strokeStyle=window.CLIVETheme?.canvas('#334455','#d6e0ea')||'#334455';ctx.fillStyle=window.CLIVETheme?.canvas('#b2c0d0','#455c73')||'#b2c0d0';ctx.lineWidth=1;
 for(let step=0;step<=4;step++){const y=step*axisMax/4,yy=Y(y);ctx.beginPath();ctx.moveTo(l,yy);ctx.lineTo(w-r,yy);ctx.stroke();ctx.fillText(`${Math.round(y*100)}%`,7,yy+4)}
 for(let step=0;step<=4;step++){const sec=maxT*step/4,xx=X(sec);ctx.fillText(`${sec.toFixed(0)}s`,xx-12,h-12)}
 ready.forEach((n)=>{ctx.strokeStyle=chColor(n.name);ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2.5;ctx.beginPath();n.points.forEach((p,j)=>{const x=X(p.time_s),y=Y(metricValue(p));if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke();n.points.forEach(p=>{ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),3,0,2*Math.PI);ctx.fill()})});
 // Hover is a cross-section by simulation time. Show the NEAREST MEASURED
 // point for each CH; never interpolate or invent an accuracy value.
 const tooltip=$('chartTooltip');
 if(hoverTime===null || hoverTime<0 || hoverTime>maxT){tooltip.hidden=true;return}
 const xHover=X(hoverTime);
 ctx.save();ctx.strokeStyle=window.CLIVETheme?.canvas('#e9f4ff80','#38597777')||'#e9f4ff80';ctx.lineWidth=1;ctx.setLineDash([4,4]);
 ctx.beginPath();ctx.moveTo(xHover,t);ctx.lineTo(xHover,h-b);ctx.stroke();ctx.restore();
 const rows=[];
 ready.forEach((n)=>{
  const points=n.points; let lo=0,hi=points.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(points[mid].time_s<hoverTime)lo=mid+1;else hi=mid}
  const choices=[points[Math.min(lo,points.length-1)],points[Math.max(0,lo-1)]];
  const p=choices.reduce((a,b)=>Math.abs(a.time_s-hoverTime)<=Math.abs(b.time_s-hoverTime)?a:b);
  const color=chColor(n.name);
  ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),6.5,0,2*Math.PI);
  ctx.strokeStyle=window.CLIVETheme?.canvas('#edf5ff','#ffffff')||'#edf5ff';ctx.lineWidth=2;ctx.stroke();
  ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),4,0,2*Math.PI);
  ctx.fillStyle=color;ctx.fill();
  const cause=p.cause==='local_training'?'Training':p.cause==='peer_aggregation'?'Aggregation':p.cause||'Revision';
  const provenance=p.cause==='local_training'&&Array.isArray(p.digit_counts_before_training)
    ?` · ${p.digit_counts_before_training.reduce((a,b)=>a+b,0)} received digits before training`
    :p.cause==='peer_aggregation'?` · received peer MODEL_UPDATE${p.trigger_peer?` from ${esc(p.trigger_peer)}`:''}`:'';
  rows.push(`<div class="hover-row"><span class="hover-marker" style="background:${color}"></span><strong>${esc(n.name)}: ${pct(metricValue(p))}</strong><div class="hover-detail">Actual update at ${Number(p.time_s).toFixed(6)} s · revision #${esc(p.revision??'—')} · ${esc(cause)}${provenance}</div></div>`);
 });
 tooltip.innerHTML=`<div class="hover-heading">Cursor: ${hoverTime.toFixed(6)} s · nearest recorded updates</div>${rows.join('')}`;
 tooltip.hidden=false;
 const desired=xHover+14;
 tooltip.style.left=`${Math.max(6,Math.min(desired,w-292))}px`;
 tooltip.style.top=`${Math.max(6,Math.min(t+11,h-150))}px`;
}
// A single detail area avoids three large mostly-empty panels. Keep accessible
// tab selection explicit, including ArrowLeft/ArrowRight keyboard navigation.
const detailTabs=Array.from(document.querySelectorAll('.learning-detail-tabs [role="tab"]'));
function setDetailTab(next){
 detailTabs.forEach(tab=>{
  const selected=tab===next;
  tab.setAttribute('aria-selected',selected?'true':'false');
  tab.tabIndex=selected?0:-1;
  const panel=$(tab.getAttribute('aria-controls'));
  if(panel)panel.hidden=!selected;
 });
}
detailTabs.forEach((tab,i)=>{
 tab.addEventListener('click',()=>setDetailTab(tab));
 tab.addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  e.preventDefault();
  let next=e.key==='Home'?0:e.key==='End'?detailTabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+detailTabs.length)%detailTabs.length;
  setDetailTab(detailTabs[next]);detailTabs[next].focus();
 });
});
$('runSelect').addEventListener('change',e=>{selectedRun=e.target.value;window.CLIVERunSelection?.write(selectedRun);modelData=null;hoverTime=null;clearLearning();syncNavigation();history.replaceState(null,'',`?run=${encodeURIComponent(selectedRun)}`);load()});
$('nodeSelect').addEventListener('change',()=>{hoverTime=null;activeRevision=null;render()});
// Local CH filters are independent. "Follow master" is the only coupled mode.
for(const id of ['chartCHSelect','distributionCHSelect','qualityCHSelect','detailsCHSelect']) {
 $(id).addEventListener('change',()=>{if(id==='chartCHSelect')hoverTime=null;if(id==='distributionCHSelect'||id==='detailsCHSelect')activeRevision=null;render()});
}
$('distributionSort').addEventListener('change',()=>renderReceivedDigits(specificNodeFor('distributionCHSelect')));
$('revisionDigitMode').addEventListener('change',()=>renderReceivedDigits(specificNodeFor('distributionCHSelect')));
$('clearRevision').addEventListener('click',()=>{activeRevision=null;renderHistory(panelNodesFor('detailsCHSelect'));renderReceivedDigits(specificNodeFor('distributionCHSelect'))});
$('history').addEventListener('click',event=>{
 const row=event.target.closest('tr[data-revision-index]');
 if(row)selectRevision(visibleHistoryRows[Number(row.dataset.revisionIndex)]);
});
$('qualitySort').addEventListener('change',()=>renderQuality(panelNodesFor('qualityCHSelect')));
$('historySort').addEventListener('change',()=>renderHistory(panelNodesFor('detailsCHSelect')));
$('showRevisionEvidence').addEventListener('change',event=>{document.querySelector('.learning-history-panel').classList.toggle('compact-evidence',!event.target.checked)});
$('distributionRevisionSelect').addEventListener('change',event=>{
 const node=specificNodeFor('distributionCHSelect');
 const index=event.target.value;
 if(!node||index===''){activeRevision=null;}
 else {const p=nodePoints(node)[Number(index)];activeRevision=p?{node:node.name,time_s:p.time_s,revision:p.revision,cause:p.cause}:null;}
 renderHistory(panelNodesFor('detailsCHSelect'));renderReceivedDigits(node);
});
$('perDigitSort').addEventListener('change',()=>renderPerClass(panelNodesFor('detailsCHSelect')));
// Chart legends focus ONLY the chart, not the rest of the dashboard.
$('legend').addEventListener('click',event=>{
 const target=event.target.closest('[data-focus-ch]');if(!target)return;
 const name=target.getAttribute('data-focus-ch');
 const select=$('chartCHSelect');
 select.value=select.value===name?'follow':name;
 hoverTime=null;render();
});
$('metricSelect').addEventListener('change',()=>{hoverTime=null;render()});
$('exampleDigit').addEventListener('change',()=>renderExamples(panelNodesFor('detailsCHSelect')));
$('refresh').addEventListener('click',load);
$('toggleHistory').addEventListener('click',()=>{fullHistory=!fullHistory;$('toggleHistory').textContent=fullHistory?'Show latest 12 updates':'Show up to 2,000 recent updates';renderHistory(panelNodesFor('detailsCHSelect'))});
window.addEventListener('resize',()=>modelData&&render());
load();setInterval(()=>{if(modelData?.status==='running'&&!document.hidden)load()},3000);

// Inspect any x position without snapping the cursor to a fabricated measurement.
$('chart').addEventListener('pointermove',event=>{
 if(!modelData)return;
 const rect=$('chart').getBoundingClientRect(),l=54,r=18;
 const x=event.clientX-rect.left;
 if(x<l || x>rect.width-r){hoverTime=null;$('chartTooltip').hidden=true;return}
 const nodes=measuredSeries(panelNodesFor('chartCHSelect'));
 if(!nodes.length)return;
 const maxT=Math.max(1,modelData.duration_s,...nodes.flatMap(n=>n.points.map(p=>p.time_s)));
 hoverTime=Math.max(0,Math.min(maxT,(x-l)/(rect.width-l-r)*maxT));
 // One expensive canvas redraw per animation frame, even with high-rate mice.
 if(hoverFrame===null){
  hoverFrame=requestAnimationFrame(()=>{
   hoverFrame=null;
   if(modelData)draw(panelNodesFor('chartCHSelect'));
  });
 }
});
$('chart').addEventListener('pointerleave',()=>{
 hoverTime=null;$('chartTooltip').hidden=true;
 if(hoverFrame!==null){cancelAnimationFrame(hoverFrame);hoverFrame=null}
 modelData&&draw(panelNodesFor('chartCHSelect'));
});

window.addEventListener("clive-theme-change", () => { if (modelData) render(); });
