'use strict';
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search);
const palette=['#70dfc7','#f1c47a','#91a9ff','#e29cc9','#c2d99a','#ff9b8b',
 '#8bd4ff','#c9a9ff','#ffdb8b','#86d3b0','#d9a3c2','#aec9e9'];
let selectedRun=params.get('run')||'', modelData=null, fullHistory=false;
let hoverTime=null;
const metrics=Object.freeze({accuracy:'Accuracy',macro_f1:'Macro F1',weighted_f1:'Weighted F1',
 macro_precision:'Macro precision',macro_recall:'Macro recall'});
const selectedMetric=()=>document.getElementById('metricSelect').value;
const metricValue=(p)=>Number.isFinite(p?.[selectedMetric()])?p[selectedMetric()]:null;

let hoverFrame=null;
let loadingVersion=0;

function syncNavigation() {
 const suffix=selectedRun?`?run=${encodeURIComponent(selectedRun)}`:'';
 for(const a of document.querySelectorAll('.bar nav a')) {
  const base=a.getAttribute('href').split('?')[0];
  a.href=base+suffix;
 }
}
const pct=n=>Number.isFinite(n)?(n*100).toFixed(2)+'%':'—';
const esc=s=>String(s??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
async function api(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error(`HTTP ${r.status}`);return await r.json()}
async function refreshRuns(){
 const data=await api('/api/runs'),runs=data.runs||[];
 if(!runs.length){$('runSelect').innerHTML='<option>No runs yet</option>';return false}
 if(!runs.some(r=>r.name===selectedRun))selectedRun=runs[0].name;
 $('runSelect').innerHTML=runs.map(r=>`<option value="${esc(r.name)}" ${r.name===selectedRun?'selected':''}>${esc(r.name)}</option>`).join('');
 syncNavigation();
 return true;
}
async function load(){
 const version=++loadingVersion;
 try{
  if(!await refreshRuns()){$('runStatus').textContent='No experiment';return}
  const data=await api(`/api/learning?run=${encodeURIComponent(selectedRun)}`);
  if (version!==loadingVersion || selectedRun!==data.run && data.run) return;
  modelData=data; $('runStatus').textContent=(data.status||'unknown').toUpperCase();
  const names=data.nodes.map(n=>n.name);const previous=$('nodeSelect').value;
  $('nodeSelect').innerHTML='<option value="all">All CHs</option>'+names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  if(names.includes(previous))$('nodeSelect').value=previous;
  render();
 }catch(e){$('runStatus').textContent='Could not load · '+e.message}
}
function selectedNodes(){return(modelData?.nodes||[]).filter(n=>$('nodeSelect').value==='all'||n.name===$('nodeSelect').value)}

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
 const nodes=selectedNodes(); const all=nodes.flatMap(n=>(n.points||[]).map(p=>({...p,node:n.name})));
 const latest=all.length?all.reduce((a,b)=>b.time_s>=a.time_s?b:a):null;
 const latestPerNode=nodes.map(n=>n.points.at(-1)).filter(p=>metricValue(p)!==null);
 const meanLatest=latestPerNode.length?latestPerNode.reduce((sum,p)=>sum+metricValue(p),0)/latestPerNode.length:null;
 $('sampledAccuracy').textContent=meanLatest===null?'Not recorded':pct(meanLatest);
 $('metricSummaryLabel').textContent=`Mean latest ${metrics[selectedMetric()].toLowerCase()}`;
 $('chartHeading').textContent=`MNIST ${metrics[selectedMetric()].toLowerCase()} by simulation time`;
 $('updateCount').textContent=String(all.length);
 $('trainCount').textContent=String(all.filter(p=>p.cause==='local_training').length);
 $('aggregateCount').textContent=String(all.filter(p=>p.cause==='peer_aggregation').length);
 $('comparison').innerHTML=nodes.map(n=>{
  const last=n.points.at(-1),full=n.final?.status==='ok'?n.final.accuracy:null;
  const latestMetric=metricValue(last);
  const finalMetric=n.final?.status==='ok' ? n.final?.[selectedMetric()] : null;
  return `<tr><td><strong>${esc(n.name)}</strong></td><td>${last?.revision??n.model_revision??'—'}</td><td>${pct(latestMetric)}</td><td>${pct(finalMetric)}</td><td>${last?`${Number(last.time_s).toFixed(6)}s · ${last.cause==='local_training'?'Training':'Aggregation'}`:'—'}</td></tr>`
 }).join('')||'<tr><td colspan="5">No Cluster Heads</td></tr>';
 $('history').innerHTML=nodes.flatMap(n=>n.points.map((p,i)=>({...p,node:n.name,metricDelta:i&&Number.isFinite(p[selectedMetric()])&&Number.isFinite(n.points[i-1][selectedMetric()])?
   p[selectedMetric()]-n.points[i-1][selectedMetric()]:null})))
 .sort((a,b)=>a.time_s-b.time_s).slice(fullHistory?-2000:-12).reverse().map(p=>`<tr><td>${Number(p.time_s).toFixed(6)}s</td><td>${esc(p.node)}</td><td>${p.cause==='local_training'?'Local training':'Peer aggregation'}</td><td>${p.revision}</td><td>${pct(metricValue(p))}</td><td>${p.metricDelta==null?'—':(p.metricDelta>=0?'+':'')+(p.metricDelta*100).toFixed(2)+' pts'}</td></tr>`).join('')||'<tr><td colspan="6">No learning history recorded</td></tr>';
 renderPerClass(nodes);
 renderExamples(nodes);
 draw(nodes);
}
function renderPerClass(nodes) {
 const body=$('perClassRows'), matrixBody=$('confusionRows');
 const target=nodes.length===1 ? nodes[0] : null;
 const last=target?.points?.at(-1);
 const perClass=last?.per_class || target?.final?.per_class;
 if (!perClass?.length) {
   body.innerHTML='<tr><td colspan="5">Choose one Cluster Head with recorded MNIST metrics to inspect each digit.</td></tr>';
   matrixBody.innerHTML='<tr><td colspan="11">No confusion matrix recorded.</td></tr>';
   return;
 }
 body.innerHTML=perClass.map(d=>`<tr><td>${esc(d.digit)}</td><td>${esc(d.support)}</td><td>${pct(d.precision)}</td><td>${pct(d.recall)}</td><td>${pct(d.f1)}</td></tr>`).join('');
 const matrix=last?.confusion_matrix || target?.final?.confusion_matrix;
 matrixBody.innerHTML=Array.isArray(matrix)&&matrix.length===10&&matrix.every(row=>Array.isArray(row)&&row.length===10)
  ? matrix.map((row,i)=>`<tr><th>${i}</th>${row.map((value,j)=>`<td class="${i===j?'matrix-correct':''}">${Number.isInteger(value)&&value>=0?value:'—'}</td>`).join('')}</tr>`).join('')
  : '<tr><td colspan="11">No confusion matrix available.</td></tr>';
}
function renderExamples(nodes) {
 const target=nodes.length===1 ? nodes[0] : null;
 const samples=target?.points?.at(-1)?.example_confidence;
 const digit=Number($('exampleDigit').value);
 const summary=$('exampleSummary'), bars=$('exampleConfidence');
 if (!Array.isArray(samples) || !Number.isInteger(digit) || digit<0 || digit>9) {
  summary.textContent='Choose one Cluster Head with recorded example predictions. Earlier runs may not contain this metric.';
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
 const correct=choice===digit;
 summary.textContent=`${target.name} · True digit ${digit} · Model predicts ${choice} (${correct?'correct':'incorrect'}) · confidence ${(p[choice]*100).toFixed(2)}%. One fixed test image; not an average success rate.`;
 bars.innerHTML=p.map((prob,i)=>`<div class="confidence-row${i===choice?' top-choice':''}" aria-label="Digit ${i}: ${(prob*100).toFixed(2)} percent model confidence">
  <span class="digit-label">${i}</span><span class="confidence-track"><span class="confidence-fill" style="width:${(prob*100).toFixed(4)}%"></span></span>
  <span class="confidence-value">${(prob*100).toFixed(2)}%</span></div>`).join('');
}
function draw(nodes){
 if(!modelData)return;
 const canvas=$('chart'),el=canvas.parentNode,rect=el.getBoundingClientRect(); const w=Math.max(280,rect.width),h=Math.max(240,rect.height);
 const dpr=Math.max(1,window.devicePixelRatio||1);
 const pixelWidth=Math.round(w*dpr),pixelHeight=Math.round(h*dpr);
 if(canvas.width!==pixelWidth || canvas.height!==pixelHeight){
  canvas.width=pixelWidth;canvas.height=pixelHeight;
 }
 const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
 ctx.clearRect(0,0,w,h);
 const ready=measuredSeries(nodes);$('chartEmpty').hidden=Boolean(ready.length);
 $('legend').innerHTML=ready.map((n,i)=>`<span><i style="background:${palette[i%palette.length]}"></i>${esc(n.name)}</span>`).join('');
 if(!ready.length){$('chartTooltip').hidden=true;return}
 const l=54,r=18,t=20,b=43,maxT=Math.max(1,modelData.duration_s,...ready.flatMap(n=>n.points.map(p=>p.time_s)));
 const maximum=Math.max(...ready.flatMap(n=>n.points.map(p=>metricValue(p)??0)));
 const axisMax=Math.min(1,Math.max(.2,Math.ceil(maximum*1.2*5)/5));
 const X=x=>l+(w-l-r)*x/maxT,Y=y=>t+(h-t-b)*(1-y/axisMax);
 ctx.font='12px system-ui';ctx.strokeStyle='#334455';ctx.fillStyle='#b2c0d0';ctx.lineWidth=1;
 for(let step=0;step<=4;step++){const y=step*axisMax/4,yy=Y(y);ctx.beginPath();ctx.moveTo(l,yy);ctx.lineTo(w-r,yy);ctx.stroke();ctx.fillText(`${Math.round(y*100)}%`,7,yy+4)}
 for(let step=0;step<=4;step++){const sec=maxT*step/4,xx=X(sec);ctx.fillText(`${sec.toFixed(0)}s`,xx-12,h-12)}
 ready.forEach((n,i)=>{ctx.strokeStyle=palette[i%palette.length];ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2.5;ctx.beginPath();n.points.forEach((p,j)=>{const x=X(p.time_s),y=Y(metricValue(p));if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke();n.points.forEach(p=>{ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),3,0,2*Math.PI);ctx.fill()})});
 // Hover is a cross-section by simulation time. Show the NEAREST MEASURED
 // point for each CH; never interpolate or invent an accuracy value.
 const tooltip=$('chartTooltip');
 if(hoverTime===null || hoverTime<0 || hoverTime>maxT){tooltip.hidden=true;return}
 const xHover=X(hoverTime);
 ctx.save();ctx.strokeStyle='#e9f4ff80';ctx.lineWidth=1;ctx.setLineDash([4,4]);
 ctx.beginPath();ctx.moveTo(xHover,t);ctx.lineTo(xHover,h-b);ctx.stroke();ctx.restore();
 const rows=[];
 ready.forEach((n,i)=>{
  const points=n.points; let lo=0,hi=points.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(points[mid].time_s<hoverTime)lo=mid+1;else hi=mid}
  const choices=[points[Math.min(lo,points.length-1)],points[Math.max(0,lo-1)]];
  const p=choices.reduce((a,b)=>Math.abs(a.time_s-hoverTime)<=Math.abs(b.time_s-hoverTime)?a:b);
  const color=palette[i%palette.length];
  ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),6.5,0,2*Math.PI);
  ctx.strokeStyle='#edf5ff';ctx.lineWidth=2;ctx.stroke();
  ctx.beginPath();ctx.arc(X(p.time_s),Y(metricValue(p)),4,0,2*Math.PI);
  ctx.fillStyle=color;ctx.fill();
  const cause=p.cause==='local_training'?'Training':p.cause==='peer_aggregation'?'Aggregation':p.cause||'Revision';
  rows.push(`<div class="hover-row"><span class="hover-marker" style="background:${color}"></span><strong>${esc(n.name)}: ${pct(metricValue(p))}</strong><div class="hover-detail">Actual update at ${Number(p.time_s).toFixed(6)} s · revision #${esc(p.revision??'—')} · ${esc(cause)}</div></div>`);
 });
 tooltip.innerHTML=`<div class="hover-heading">Cursor: ${hoverTime.toFixed(6)} s · nearest recorded updates</div>${rows.join('')}`;
 tooltip.hidden=false;
 const desired=xHover+14;
 tooltip.style.left=`${Math.max(6,Math.min(desired,w-292))}px`;
 tooltip.style.top=`${Math.max(6,Math.min(t+11,h-150))}px`;
}
$('runSelect').addEventListener('change',e=>{selectedRun=e.target.value;modelData=null;hoverTime=null;syncNavigation();history.replaceState(null,'',`?run=${encodeURIComponent(selectedRun)}`);load()});
$('nodeSelect').addEventListener('change',()=>{hoverTime=null;render()});
$('metricSelect').addEventListener('change',()=>{hoverTime=null;render()});
$('exampleDigit').addEventListener('change',()=>renderExamples(selectedNodes()));
$('refresh').addEventListener('click',load);
$('toggleHistory').addEventListener('click',()=>{fullHistory=!fullHistory;$('toggleHistory').textContent=fullHistory?'Show latest 12 updates':'Show up to 2,000 recent updates';render()});
window.addEventListener('resize',()=>modelData&&render());
load();setInterval(()=>{if(modelData?.status==='running'&&!document.hidden)load()},3000);

// Inspect any x position without snapping the cursor to a fabricated measurement.
$('chart').addEventListener('pointermove',event=>{
 if(!modelData)return;
 const rect=$('chart').getBoundingClientRect(),l=54,r=18;
 const x=event.clientX-rect.left;
 if(x<l || x>rect.width-r){hoverTime=null;$('chartTooltip').hidden=true;return}
 const nodes=measuredSeries(selectedNodes());
 if(!nodes.length)return;
 const maxT=Math.max(1,modelData.duration_s,...nodes.flatMap(n=>n.points.map(p=>p.time_s)));
 hoverTime=Math.max(0,Math.min(maxT,(x-l)/(rect.width-l-r)*maxT));
 // One expensive canvas redraw per animation frame, even with high-rate mice.
 if(hoverFrame===null){
  hoverFrame=requestAnimationFrame(()=>{
   hoverFrame=null;
   if(modelData)draw(selectedNodes());
  });
 }
});
$('chart').addEventListener('pointerleave',()=>{
 hoverTime=null;$('chartTooltip').hidden=true;
 if(hoverFrame!==null){cancelAnimationFrame(hoverFrame);hoverFrame=null}
 modelData&&draw(selectedNodes());
});
