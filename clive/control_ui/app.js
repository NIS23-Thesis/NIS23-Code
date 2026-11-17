'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const title = s => String(s).replaceAll('_', ' ').replace(/\b\w/g, c=>c.toUpperCase());
const fmt = x => Number(x||0).toLocaleString(undefined,{maximumFractionDigits:1});
let presets = [], defaults = {}, examples = {}, config = {}, selectedId = 'demos/flame-synthetic-transport-and-aggregation-smoke.json';
let currentTab = 'overview', currentView = 'design', currentStatus = {}, visualizer = null;
let toastTimer, statusLast = '', originalScenario = null;
const DRAFT_KEY='clive-studio-session-draft-v1';
function saveDraft(){
 try { if(typeof sessionStorage!=='undefined')sessionStorage.setItem(DRAFT_KEY,JSON.stringify({selectedId,config})); }catch(_){ /* Session storage may be disabled. */ }
}
function readDraft(){
 try { if(typeof sessionStorage!=='undefined')return JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'null'); }catch(_){ /* Unavailable storage. */ }
 return null;
}

const enums = {
 'runtime.radio_execution':['process','thread'],
 'mobility.model':['gauss_markov','random_waypoint','static'],
 'mobility.initial_topology':['random','line','star','grid','manual'],
 'mobility.pause_trigger':['zero_speed','periodic','random'],
 'link.range_mode':['rssi','explicit'],
 'flame.sample_source':['mnist','synthetic'],
 'flame.trainer':['mnist','mean_pixel']
};
const descriptions = {
 'runtime.duration_s':'Length of the emulation in seconds',
 'runtime.radio_execution':'Radio backend: process (maximum timing isolation) or thread (lower memory for many nodes)',
 'runtime.seed':'Deterministic topology and sample seed',
 'runtime.startup_delay_s':'Lead time for worker startup and synchronization',
 'runtime.io_poll_interval_s':'Emulator socket poll interval (seconds)',
 'runtime.readiness_timeout_s':'Maximum time to wait for virtual node readiness',
 'runtime.start_tolerance_s':'Allowed difference between worker start times',
 'mobility.model':'Position evolution model',
 'mobility.initial_topology':'Initial arrangement of the generated nodes',
 'mobility.tick_interval':'Seconds between movement states',
 'mobility.area_width_m':'Width of the simulated area (meters)',
 'mobility.area_height_m':'Height of the simulated area (meters)',
 'mobility.alpha':'Gauss–Markov movement memory (0–1)',
 'mobility.pause_trigger':'When nodes can temporarily pause',
 'mobility.manual_positions':'Exact per-node [x, y] coordinates; required for manual topology',
 'mobility.node_order':'Optional order for line, grid or star layouts',
 'link.range_mode':'Derived RSSI limit or user-defined maximum radio range',
 'link.max_range_m':'Maximum sender transmission distance (meters)',
 'link.max_data_rate_bps':'Upper bound on link throughput (bits per second)',
 'link.ipdv_enabled':'Enable simulated packet-delay variation',
 'flame.hello_interval_s':'Seconds between Cluster Head HELLO broadcasts',
 'flame.sample_interval_s':'Seconds between new sensor observations',
 'flame.service_interval_s':'Seconds between FLAME reliability/timer servicing',
 'flame.expected_observations':'Expected samples for the local training trigger',
 'flame.training_threshold':'Fraction of expected observations needed to train',
 'flame.aggregation_threshold':'Peer model updates required for buffered aggregation',
 'flame.ack_timeout':'Seconds to wait for fragment acknowledgement',
 'flame.ack_delay':'Delay before sending a batched ACK (seconds)',
 'flame.max_retries':'Maximum window retransmission attempts',
 'flame.initial_window_size':'Starting number of unacknowledged fragments in flight',
 'flame.max_window_size':'Maximum adaptive send window size',
 'flame.sample_source':'MNIST dataset or synthetic transport-test samples',
 'flame.trainer':'mnist = REAL PyTorch neural-network training; mean_pixel = NON-ML PROXY for communication capacity only',
 'flame.repeat_sensor_samples':'Repeat small sensor sample pools only when deliberately stress-testing transport',
 'flame.model_retry_limit':'Integration-level reattempts after FLAME reports a model transfer failed (does not fix FLAME ACK bugs)',
 'flame.model_retry_backoff_s':'Initial seconds before adapter reattempts a failed peer model transfer',
 'logging.packet_events':'Write verbose packet traces to per-node files (can be large)',
 'logging.protocol_events':'Write detailed node/protocol events',
 'logging.mobility_tsv':'Write a human-readable mobility trace'
};
const groupNames = {
 runtime:['Experiment runtime','Node processes, duration, and execution limits','◷'],
 mobility:['Mobility model & topology','Movement parameters and initial node placement','⌖'],
 link:['Radio link characteristics','Distance, bandwidth, signal loss, and energy model','◉'],
 flame:['FLAME integration settings','Workload and adapter parameters; core flame/ code is unchanged','⥈'],
 logging:['Debug & diagnostic output','Enable detailed events for thesis evaluation','▤']
};
const capacityFields = {
  sn: [
    ["sn_send_buffer_capacity", "Send buffer", "Queued FLAME messages (entries)", "send_buffer_capacity"],
    ["sn_datagram_buffer_capacity", "Datagram buffer", "Incoming datagrams (entries; FIFO drops oldest)", "receive_buffer_capacity"],
    ["sn_sample_buffer_capacity", "Sensing sample buffer", "Pending sensor observations (entries)", null],
    ["sn_offline_buffer_capacity", "Offline sample buffer", "Unsent observations while disconnected (entries)", "max_offline_samples"],
    ["sn_radio_queue_packets", "NodeSocket TX queue", "Maximum pending radio packets", "runtime.max_queue_packets"],
    ["sn_radio_queue_bytes", "NodeSocket TX bytes", "Maximum queued radio bytes", "runtime.max_queue_bytes"],
  ],
  ch: [
    ["ch_send_buffer_capacity", "Send buffer", "Queued FLAME messages (entries)", "send_buffer_capacity"],
    ["ch_datagram_buffer_capacity", "Datagram buffer", "Incoming datagrams (entries; FIFO drops oldest)", "receive_buffer_capacity"],
    ["ch_dataset_capacity", "Dataset storage", "Stored sensor observations (entries)", "dataset_capacity"],
    ["ch_radio_queue_packets", "NodeSocket TX queue", "Maximum pending radio packets", "runtime.max_queue_packets"],
    ["ch_radio_queue_bytes", "NodeSocket TX bytes", "Maximum queued radio bytes", "runtime.max_queue_bytes"],
  ]
};
const ignored = new Set(['mobility.manual_positions','mobility.node_order','mobility.center_node']);
function toast(message, error=false) {
 const node=$('toast'); node.textContent=message; node.className='visible'+(error?' error':'');
 clearTimeout(toastTimer); toastTimer=setTimeout(()=>node.className='',5500);
}
async function api(path, method='GET', data) {
 const init={method, headers:{}};
 if(data!==undefined){init.headers['Content-Type']='application/json';init.body=JSON.stringify(data)}
 const res=await fetch(path,init); const body=await res.json();
 if(!res.ok) throw new Error(body.error||'Request failed'); return body;
}
function merge(defaultObj, valueObj){
 if(Array.isArray(defaultObj)) return Array.isArray(valueObj)?structuredClone(valueObj):structuredClone(defaultObj);
 if(defaultObj && typeof defaultObj==='object'){
  const result=structuredClone(defaultObj); if(valueObj&&typeof valueObj==='object')
   for(const [k,v] of Object.entries(valueObj)) result[k]=(k in defaultObj)?merge(defaultObj[k],v):structuredClone(v);
  return result;
 }
 return valueObj===undefined?defaultObj:valueObj;
}
function get(group,key){return config[group]?.[key]}
function set(group,key,value){config[group][key]=value;updateSummary()}
function updateSummary(){
 $('summaryCH').textContent=Number.isSafeInteger(Number(config.num_ch))&&Number(config.num_ch)>=0?String(config.num_ch):'—';
 $('summarySN').textContent=Number.isSafeInteger(Number(config.num_sn))&&Number(config.num_sn)>=0?String(config.num_sn):'—';
 $('summaryDuration').textContent=(config.runtime?.duration_s??'—')+' s';
 $('summaryMobility').textContent=title(config.mobility?.model||'—');
 saveDraft();
}
function textInput(value,kind,handler,placeholder=''){
 const input=document.createElement('input'); input.type=kind;
 input.value=value===undefined||value===null?'':value;
 input.placeholder=placeholder;
 input.addEventListener('change', e=>handler(kind==='number'?parseFloat(e.target.value):e.target.value));
 return input;
}
function appendField(parent, label, desc, control, klass=''){
 const box=document.createElement('div'); box.className='field '+klass;
 const name=document.createElement('div'); name.className='field-label';name.textContent=title(label);
 const help=document.createElement('div');help.className='field-note';help.textContent=desc||'';
 box.append(name,control,help);parent.appendChild(box);return box;
}
function optionalBox(current,change){
 const label=document.createElement('label');label.className='optional';
 const cb=document.createElement('input');cb.type='checkbox'; cb.checked=current!==null&&current!==undefined;
 cb.addEventListener('change',()=>change(cb.checked));label.append(cb,' Enable');return label;
}
function fieldFor(group,key,value,container) {
 const path=group+'.'+key, desc=descriptions[path]||'';
 const base=defaults[group]?.[key];
 const isOptional=base===null||base===undefined;
 const example=examples[group]?.[key];
 const sample=base??example??value;
 if(key==='manual_positions' || key==='node_max_range_m' || key==='role_max_range_m'){
   const box=document.createElement('div');box.className='field complex-field';
   const header=document.createElement('div');header.className='complex-header';
   const label=document.createElement('span');label.className='field-label';label.textContent=title(key);
   const add=document.createElement('button');add.className='add-btn';add.type='button';add.textContent='+ Add row';
   const editor=document.createElement('div');editor.className='complex-editor';
   function renderMap(){
     editor.replaceChildren();const entries=Object.entries(get(group,key)||{});
     entries.forEach(([name,val])=>{
       const r=document.createElement('div');r.className='map-row '+(key==='manual_positions'?'':'single');
       const keyEl=textInput(name,'text',newName=>{
         if(!newName||newName===name)return;
         const copied={...get(group,key)};delete copied[name];copied[newName]=val;set(group,key,copied);renderMap();
       },'Node name');r.appendChild(keyEl);
       const vals=key==='manual_positions'?(Array.isArray(val)?val:[0,0]):[val];
       vals.forEach((v,i)=>r.appendChild(textInput(v,'number',newVal=>{
         const copied={...get(group,key)};
         if(key==='manual_positions'){const xy=[...copied[name]];xy[i]=newVal;copied[name]=xy;}
         else copied[name]=newVal;
         set(group,key,copied);
       },key==='manual_positions'?(i===0?'X':'Y'):'Range (m)')));
       const remove=document.createElement('button');remove.className='remove-btn';remove.textContent='×';remove.title='Remove row';remove.onclick=()=>{const copied={...get(group,key)};delete copied[name];set(group,key,copied);renderMap();};r.append(remove);editor.append(r);
     });
   }
   add.onclick=()=>{
     const obj={...get(group,key)};let baseName=key==='role_max_range_m'?'cluster_head':'CH-1';let name=baseName;
     let ix=2;while(Object.hasOwn(obj,name)){if(key==='role_max_range_m'){name='sensor_node';if(Object.hasOwn(obj,name)){toast('Both role ranges already exist.',true);return;}}else name='SN-'+ix++;}
     obj[name]=key==='manual_positions'?[0,0]:60;set(group,key,obj);renderMap();
   };
   header.append(label,add);box.append(header,editor);if(desc){let note=document.createElement('small');note.className='field-note';note.textContent=desc;box.append(note)}
   container.append(box);renderMap();return;
 }
 if(key==='node_order'||Array.isArray(sample)&&(!Array.isArray(sample)||sample.length!==2||typeof sample[0]!=='number')){
   const wrap=document.createElement('div');wrap.className='complex-field field';
   const head=document.createElement('div');head.className='complex-header';head.innerHTML='<span class="field-label">'+esc(title(key))+'</span>';
   const add=document.createElement('button');add.className='add-btn';add.textContent='+ Add item';head.append(add);
   if(isOptional)head.append(optionalBox(value,enable=>{set(group,key,enable?[]:null);renderList()}));
   const editor=document.createElement('div');editor.className='complex-editor';
   function renderList(){editor.replaceChildren();let values=get(group,key);if(values===null)return;
     (values||[]).forEach((item,i)=>{
       const row=document.createElement('div');row.className='list-row';
       const input=textInput(item,'text',next=>{const arr=[...get(group,key)];arr[i]=next;set(group,key,arr)});
       const rem=document.createElement('button');rem.textContent='×';rem.className='remove-btn';rem.onclick=()=>{let arr=[...get(group,key)];arr.splice(i,1);set(group,key,arr);renderList()};row.append(input,rem);editor.append(row);
     });
   }
   add.onclick=()=>{let arr=[...(get(group,key)||[])];arr.push('');set(group,key,arr);renderList()};
   wrap.append(head,editor);container.append(wrap);renderList();return;
 }
 if(Array.isArray(sample)){
   const wrap=document.createElement('div');wrap.className='pair-row';
   const vals=Array.isArray(value)?value:[0,0];
   for(let i=0;i<2;i++)wrap.append(textInput(vals[i],'number',next=>{
     const arr=Array.isArray(get(group,key))?[...get(group,key)]:[0,0];arr[i]=next;set(group,key,arr);
   },i===0?'Min':'Max'));
   const box=appendField(container,key,desc,wrap);
   if(isOptional){const opt=optionalBox(value,enable=>{set(group,key,enable?[0,0]:null);renderSettings()});box.insertBefore(opt,wrap)}
   return;
 }
 if(sample && typeof sample==='object')return;
 if(typeof value==='boolean'||typeof sample==='boolean'){
   const label=document.createElement('label');label.className='toggle';
   const check=document.createElement('input');check.type='checkbox';check.checked=Boolean(value);
   check.onchange=()=>set(group,key,check.checked);label.append(check,check.checked?'Enabled':'Disabled');
   check.addEventListener('change',()=>label.lastChild.textContent=check.checked?'Enabled':'Disabled');
   appendField(container,key,desc,label,'checkbox-field');return;
 }
 const holder=document.createElement('div');
 let el;
 if(typeof sample === 'boolean'){
   el=document.createElement('input');el.type='checkbox';el.checked=Boolean(value);
   el.onchange=()=>set(group,key,el.checked);
 }else if(enums[path]){
   el=document.createElement('select');
   enums[path].forEach(v=>{const opt=document.createElement('option');opt.value=v;opt.textContent=title(v);el.append(opt)});
   el.value=value??enums[path][0];el.onchange=()=>{set(group,key,el.value);if(path==='mobility.initial_topology')renderSettings()};
 }else{
   let kind=typeof sample==='number'?'number':'text';
   el=textInput(value,kind,next=>set(group,key,next));if(kind==='number'){el.step='any'; if(Number.isInteger(sample))el.step='1'}
 }
 holder.append(el);
 const box=appendField(container,key,desc,holder);
 if(isOptional){const opt=optionalBox(value,enabled=>{set(group,key,enabled?(example??(typeof sample==='number'?0:'')):null);renderSettings()});box.insertBefore(opt,holder);el.disabled=value===null;}
}
const primaryFields = {
  runtime:['duration_s','seed','radio_execution'],
  mobility:['model','initial_topology','area_width_m','area_height_m','tick_interval','manual_positions'],
  link:['range_mode','max_range_m','max_data_rate_bps','ipdv_enabled'],
  flame:['sample_source','trainer','sample_interval_s','expected_observations','training_threshold','aggregation_threshold'],
  logging:['packet_events','protocol_events','mobility_tsv']
};
function renderGroup(group, keys, target) {
 const [name,desc,ico]=groupNames[group];
 const card=document.createElement('div');card.className='section-card';
 card.innerHTML='<div class="section-head"><div class="section-icon">'+esc(ico)+'</div><div><strong>'+esc(name)+'</strong><small>'+esc(desc)+'</small></div></div>';
 const keyset=new Set(primaryFields[group]||[]);
 const primary=keys.filter(key=>keyset.has(key));
 const advanced=keys.filter(key=>!keyset.has(key));
 const grid=document.createElement('div');grid.className='field-grid';
 primary.forEach(key=>fieldFor(group,key,get(group,key),grid));card.append(grid);
 if(advanced.length){
   const details=document.createElement('details');details.className='advanced-settings';
   const summary=document.createElement('summary');summary.textContent='Advanced settings ('+advanced.length+')';
   const more=document.createElement('div');more.className='field-grid';
   advanced.forEach(key=>fieldFor(group,key,get(group,key),more));
   details.append(summary,more);card.append(details);
 }
 target.append(card);
}
function renderCapacities(target){
  const note=document.createElement("div");note.className="workload-notice";
  note.textContent="Limits are per node, not total machine RAM. Use positive integers; blank = inherit existing setting. FLAME receive histories and reliability/reassembly memory are managed by FLAME and cannot be resized here.";
  target.append(note);
  for(const [role, rows] of Object.entries(capacityFields)){
    const card=document.createElement("div");card.className="section-card";
    const head=document.createElement("div");head.className="section-head";
    head.innerHTML=`<div class="section-icon">${role==="sn"?"◌":"⬡"}</div><div><strong>${role==="sn"?"Sensor Node (SN)":"Cluster Head (CH)"}</strong><small>Optional per-role overrides; blank inherits the standard configuration</small></div>`;card.append(head);
    const grid=document.createElement("div");grid.className="field-grid";
    rows.forEach(([key,label,desc,fallback])=>{
      const raw=config.flame?.[key];
      let inherited=fallback==null?100:
        fallback.startsWith("runtime.")?config.runtime[fallback.split(".")[1]]:config.flame[fallback];
      const input=textInput(raw,"number",next=>{
        if(Number.isInteger(next)&&next>0){set("flame",key,next)}
        else{toast("Capacity must be a positive whole number; use Inherit to reset.",true);input.value=config.flame[key]??"";}
      },String(inherited));input.step="1";input.min="1";
      const field=appendField(grid,label,desc+" · Inherited: "+inherited,input);
      const reset=document.createElement("button");reset.type="button";reset.className="add-btn";reset.textContent="Inherit default";
      reset.onclick=()=>{set("flame",key,null);input.value=""};field.append(reset);
    });
    card.append(grid);target.append(card);
  }
}
function renderSettings(){
 const target=$('settingsPanel');target.replaceChildren();
 if(currentTab==='overview'){
   const card=document.createElement('div');card.className='section-card';
   card.innerHTML='<div class="section-head"><div class="section-icon">⬡</div><div><strong>Experiment scale</strong><small>Define the number of virtual CLIVE processes</small></div></div>';
   const grid=document.createElement('div');grid.className='field-grid';
   for(const [key,label,desc] of [['num_ch','Cluster Heads','Nodes that train and exchange models'],['num_sn','Sensor Nodes','Nodes that collect and send observations']]){
     const el=textInput(config[key],'number',next=>{
       const min=key==='num_ch'?1:0;
       if(!Number.isSafeInteger(next)||next<min||next>200){
         toast(label+' must be a whole number between '+min+' and 200.',true);
         el.value=String(config[key]);return;
       }
       config[key]=next;updateSummary();
     });el.min=key==='num_ch'?'1':'0';el.max='200';el.step='1';appendField(grid,label,desc,el);
   }
   card.append(grid);target.append(card);
   renderGroup('runtime',Object.keys(defaults.runtime),target);
 }else if(currentTab==='capacity'){
   renderCapacities(target);
 }else{
   let group=currentTab;
   let keys=Object.keys(defaults[group]);
   if(group==='flame')keys=keys.filter(k=>!Object.hasOwn(capacityFields.sn.concat(capacityFields.ch).reduce((a,r)=>(a[r[0]]=true,a),{}),k));
   if(group==='mobility')keys=keys.filter(k=>!(config.mobility.initial_topology!=='manual'&&k==='manual_positions'));
   renderGroup(group,keys,target);
   if(group==='mobility'&&config.mobility.initial_topology==='manual'){
     const make=document.createElement('button');make.className='outlined';make.textContent='◎ Auto-populate all node coordinates';make.style.marginBottom='15px';
     make.onclick=()=>{
       const x=Number(config.mobility.area_width_m)/2,y=Number(config.mobility.area_height_m)/2;
       const ids=[...Array.from({length:config.num_ch},(_,i)=>'CH-'+(i+1)),...Array.from({length:config.num_sn},(_,i)=>'SN-'+(i+1))];
       const positions={};ids.forEach((id,i)=>{let a=2*Math.PI*i/Math.max(1,ids.length);positions[id]=[Math.round(x+Math.min(x,y)*.45*Math.cos(a)),Math.round(y+Math.min(x,y)*.45*Math.sin(a))]});
       set('mobility','manual_positions',positions);renderSettings();
     };target.prepend(make);
   }
 }
}
function renderPresets(){
 const list=$('presetList');list.replaceChildren();
 const bundled=presets.filter(p=>p.id.startsWith('demos/panel-'));
 const custom=presets.filter(p=>p.custom);
 function addButton(p,parent){
   const btn=document.createElement('button');btn.className='preset-item'+(p.id===selectedId?' active':'');
   btn.textContent=p.name;btn.onclick=()=>loadPreset(p.id);parent.append(btn);
 }
 const title=document.createElement('div');title.className='preset-group';title.textContent='FIVE PANEL DEMOS · REAL MNIST';list.append(title);
 bundled.forEach(p=>addButton(p,list));
 if(custom.length){
   const advanced=document.createElement('details');advanced.className='advanced-presets';
   const heading=document.createElement('summary');heading.textContent='Saved custom scenarios (advanced)';advanced.append(heading);
   custom.forEach(p=>addButton(p,advanced));list.append(advanced);
 }
}
async function loadPreset(id){
 try{
   const d=await api('/api/scenario/'+encodeURIComponent(id));selectedId=id;
   config=merge(defaults,d.scenario);originalScenario=structuredClone(d.scenario);
   const item=presets.find(p=>p.id===id);$('editingName').textContent=item?.name||id;
   $('saveName').value=item?.custom?item.name:id.split('/').pop().replace(/\.json$/,'').replaceAll('_',' ')+' copy';
   $('unsavedLabel').textContent=item?.custom?'Custom preset — Save will update it.':'Bundled preset — Save creates a new custom copy.';
   renderPresets();renderSettings();updateSummary();toast('Loaded '+(item?.name||id));
 }catch(e){toast(e.message,true)}
}
function selectView(view){
 if(!['design','history'].includes(view)) view='design';
 currentView=view;
 for(const name of ['design','history'])$(name+'View').classList.toggle('hidden',name!==view);
 document.querySelectorAll('[data-view]').forEach(el=>{
  const active=el.dataset.view===view;
  el.classList.toggle('selected',active);
  el.classList.toggle('active',active);
  if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');
 });
 document.title='CLIVE · '+({design:'Experiment Setup',history:'Run History'})[view];
 if(view==='history')loadHistory();
}
function setTab(tab){currentTab=tab;document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.section===tab));renderSettings()}
async function savePreset(){
 try{
   const data=await api('/api/save','POST',{name:$('saveName').value,scenario:config});
   presets=data.presets;selectedId=data.id;originalScenario=structuredClone(config);renderPresets();
   $('editingName').textContent=$('saveName').value;toast('Preset saved: '+data.id);
 }catch(e){toast('Could not save: '+e.message,true)}
}
async function startExperiment(){
 if(config.flame?.trainer==='mean_pixel' && !confirm(
   'This preset uses mean_pixel (NON-ML PROXY). It does not train a PyTorch neural network or establish FL accuracy. Continue as a communication/capacity test?')) return;
 try{
   const status=await api('/api/start','POST',{scenario:config,name:$('editingName').textContent});
   updateStatus(status);selectView('design');toast('Experiment started. Use View topology to watch it.');
 }catch(e){toast('Cannot start: '+e.message,true)}
}
async function stopExperiment(){
 if(!confirm('Stop this experiment and its node/radio child processes?'))return;
 try{updateStatus(await api('/api/stop','POST',{}));toast('Stop signal sent to experiment group.')}catch(e){toast(e.message,true)}
}

async function refreshHeaderRuns(preferred){
 const select=$('runSelect');
 if(!select) return;
 try {
   const data=await api('/api/history?limit=all');
   const runs=data.runs||[];
   const prior=preferred||select.value||readSelectedRun()||currentStatus.run;
   select.replaceChildren();
   if(!runs.length){const opt=new Option('No runs yet','');select.add(opt);select.disabled=true;return;}
   select.disabled=false;
   for(const run of runs){select.add(new Option(`${run.name}`,run.name));}
   select.value=runs.some(r=>r.name===prior)?prior:runs[0].name;
   writeSelectedRun(select.value);
   updateRunNavigation(select.value);
 } catch(_){/* Keep editing available even if run-history fetch fails. */}
}
function readSelectedRun(){try{return sessionStorage.getItem('clive-selected-run-v1')||''}catch(_){return ''}}
function writeSelectedRun(run){try{if(run)sessionStorage.setItem('clive-selected-run-v1',run)}catch(_){}}
function updateRunNavigation(run){
 for(const [id,path] of [['topologyNavLink','/topology/'],['studioLearningLink','/topology/learning.html'],['studioEventsLink','/topology/events.html']]){
   const node=$(id);if(!node)continue;
   const url=new URL(path,location.origin);
   if(run)url.searchParams.set('run',run);
   node.href=url.pathname+url.search;
 }
}

function runVisualizerUrl(runName){
 if(!visualizer || !/^run-[A-Za-z0-9_-]+$/.test(String(runName||''))) return null;
 try{
   const link=new URL(visualizer,location.href);
   if(link.protocol!=='http:' || !['127.0.0.1','localhost','::1'].includes(link.hostname)) return null;
   link.searchParams.set('run',runName);
   return link.href;
 }catch(_){return null}
}
function updateStatus(s){
 currentStatus=s;
 const state=s.status||'idle';
 const badge=$('stateBadge');badge.className='state-badge '+state;
 badge.innerHTML='<span></span> '+esc(s.running?'RUNNING':(state==='complete'?'LAST RUN: COMPLETE':state==='failed'?'LAST RUN: FAILED':state==='stopped'?'LAST RUN: STOPPED':'READY'));
 $('startButton').disabled=!!s.running;
 $('stopButton').disabled=!s.running;
 const visible=!!(s.run||s.running||s.started_at);
 $('runStatusStrip').classList.toggle('hidden',!visible);
 const outcome=state==='complete'?'Completed':state==='failed'?'Failed':state==='stopped'?'Stopped':s.running?'Running':'Experiment '+state;
 $('runStatusKind').textContent=s.running?'ACTIVE EXPERIMENT':'MOST RECENT EXPERIMENT';
 $('runStatusText').textContent=outcome+(s.run?' · '+s.run:'');
 $('runStatusName').textContent=s.scenario_name||s.run||'Unnamed experiment';
 $('runStatusStrip').classList.toggle('is-finished',!s.running);
 $('runID').textContent=s.run||'—';$('pid').textContent=s.pid??'—';$('exitCode').textContent=s.exit_code??'—';
 const elapsed=s.started_at?Math.max(0,(s.running?Date.now()/1000:(s.finished_at||Date.now()/1000))-s.started_at):0;
 $('elapsed').textContent=s.started_at?fmt(elapsed)+' s':'—';
 const link=runVisualizerUrl(s.run);const action=$('runTopologyLink');
 action.hidden=!link;if(link)action.href=link;
 $('console').textContent=s.log||'No experiment output yet.';
 // The selected scenario may have changed. Use the immutable snapshot from the running job.
 const duration=Number(s.duration_s);
 const startup=Number(s.startup_delay_s)||0;
 const total=Number.isFinite(duration)&&duration>0?duration+Math.max(0,startup):null;
 const percent=s.started_at?(s.running?(total?Math.min(100,100*elapsed/total):0):100):0;
 $('progressBar').style.width=percent+'%';
 const progress=$('runStatusStrip').querySelector('[role=progressbar]');
 progress.setAttribute('aria-valuenow',String(Math.round(percent)));
 progress.setAttribute('aria-valuetext',total?Math.round(percent)+'% of configured wall-clock estimate':'Run progress not available');
 if(!s.running&&s.run&&statusLast!=='finished:'+s.run){
   refreshHeaderRuns();
   statusLast='finished:'+s.run;
 }
}
async function refreshStatus(){try{updateStatus(await api('/api/status'))}catch(e){ /* server likely stopped */ }}
function metricsCards(data){
 const totals=data.metrics?.totals||{};
 const outcome=data.metrics?.flame_outcome || {};
 const delivery=data.metrics?.recipient_delivery || {};
 const energy=data.metrics?.energy_model || {};
 const stats={
    'Workload type':outcome.available?
     ((outcome.real_neural_network_trainer_configured ?? outcome.trainer === 'mnist')?
       (outcome.real_neural_network_training?
         'REAL MNIST/PyTorch — training executed ('+(outcome.local_training_events||0)+' events)':
         'REAL MNIST/PyTorch configured — NO TRAINING EXECUTED'):
       'PROXY — NO NEURAL-NETWORK TRAINING'):'Not reported',
   'Model delivery':outcome.model_transfers_started != null?
     (outcome.model_transfers_completed + '/' + outcome.model_transfers_started + ' completed'):'Not verified',
   'Unfinished model transfers':outcome.model_transfers_unfinished??'—',
   'Superseded model transfers':outcome.model_transfers_superseded??0,
   'Final MNIST test accuracy':Number.isFinite(outcome.final_mnist_accuracy_mean)?(100*outcome.final_mnist_accuracy_mean).toFixed(2)+'% (mean across CHs)':'Not measured',
   'TX packets':totals.packets_sent,'RX packets':totals.packets_received,
   'Recipient radio handoff rate':Number.isFinite(delivery.recipient_handoff_ratio)?
     (100*delivery.recipient_handoff_ratio).toFixed(2)+'% (kernel handoff; not FLAME ACK)':'Not measured',
   'Delivery drops':totals.delivery_drops,'Socket errors':totals.socket_errors,
   'Energy model validity':energy.amplifier_units_consistent_with_exponent===false?
      'INCONSISTENT UNITS':'Analytical estimate (100 pJ, d^n; uncalibrated)',
   'Worker execution completed':data.metrics?.execution_success?'YES':'NO',
   'Cluster Heads trained':Object.entries(data.flame||{}).filter(([k])=>k.startsWith('CH-')).reduce((a,[,v])=>a+(v.counters?.trained||0),0),
   'Model transfers failed':Object.values(data.flame||{}).reduce((a,v)=>a+(v.counters?.transfer_failed||0),0)
 };
 return Object.entries(stats).map(([k,v])=>'<div class="metric-box"><span>'+esc(k)+'</span><strong>'+esc(typeof v==='number'?fmt(v):v??'—')+'</strong></div>').join('');
}
async function loadRun(name,target='historyDetails'){
 try{
   const run=await api('/api/run/'+encodeURIComponent(name));
   $(target).innerHTML=metricsCards(run);
   if(target==='historyDetails')$('runDetail').classList.remove('hidden');
 }catch(e){$(target).textContent=e.message}
}
const HISTORY_PAGE_SIZE=30;
let historyOffset=0, historyRequest=0;
async function loadHistory(){
 const request=++historyRequest;
 const node=$('historyList');
 node.textContent='Loading run history…';
 $('historyPrevious').disabled=true;
 $('historyNext').disabled=true;
 try{
   const search=$('historySearch').value.trim();
   const query=new URLSearchParams({offset:String(historyOffset),limit:String(HISTORY_PAGE_SIZE),search});
   const data=await api('/api/history?'+query);
   if(request!==historyRequest)return;
   node.replaceChildren();
   const total=Number(data.total)||0;
   const visible=data.runs||[];
   $('historyCount').textContent=total?`${total} matching run${total===1?'':'s'}`:'No matching runs';
   $('historyPage').textContent=total?`${historyOffset+1}–${historyOffset+visible.length} of ${total}`:'0 of 0';
   $('historyPrevious').disabled=historyOffset===0;
   $('historyNext').disabled=!data.has_more;
   if(!visible.length){node.textContent=search?'No runs match this search.':'No saved experiments yet. Run a scenario from Experiment setup.';return;}
   for(const run of visible){
     const item=document.createElement('div');item.className='history-entry';
     const meta=document.createElement('div');meta.className='history-main';
     const name=document.createElement('strong');name.textContent=run.name;
     const date=document.createElement('small');date.textContent=run.started_at?new Date(run.started_at*1000).toLocaleString():'Date unavailable';
     meta.append(name,date);
     const duration=document.createElement('span');duration.className='history-duration';duration.textContent=run.duration_s!=null?fmt(run.duration_s)+' s':'—';
     const status=document.createElement('span');status.className='tag '+String(run.status||'unknown').replace(/[^a-z-]/g,'');
     status.textContent=String(run.status||'unknown').toUpperCase();
     const actions=document.createElement('div');actions.className='history-actions';
     const url=runVisualizerUrl(run.name);
     if(url){
       const open=document.createElement('a');open.className='outlined small history-open';open.textContent='View topology ↗';
       open.href=url;open.setAttribute('aria-label','View '+run.name+' in topology Visualizer');
       actions.append(open);
     }else{
       const disabled=document.createElement('span');disabled.className='history-no-view';disabled.textContent='Visualizer unavailable';actions.append(disabled);
     }
     const details=document.createElement('button');details.type='button';details.className='outlined small history-details';details.textContent='Metrics';
     details.setAttribute('aria-label','View metrics for '+run.name);
     details.onclick=async()=>{
       $('historyDetailTitle').textContent='Metrics · '+run.name;
       await loadRun(run.name);
       $('runDetail').scrollIntoView?.({behavior:'smooth',block:'nearest'});
     };
     actions.append(details);item.append(meta,duration,status,actions);node.append(item);
   }
 }catch(e){if(request===historyRequest){node.textContent='Run history unavailable. Retry with Refresh runs.';$('historyPage').textContent='—';$('historyCount').textContent='Unable to load runs';toast('Could not load run history: '+e.message,true)}}
}
async function initialize(){
 try{
   const bootstrap=await api('/api/bootstrap');presets=bootstrap.presets;defaults=bootstrap.defaults;examples=bootstrap.examples;
   config=structuredClone(defaults);visualizer=bootstrap.visualizer_url;
   const preservedDraft=readDraft();
   const link=$('topologyNavLink');
   if(visualizer){link.href=visualizer;link.hidden=false;
     for(const [id,path] of [['studioLearningLink','learning.html'],['studioEventsLink','events.html']]){
       const a=$(id);a.href=new URL(path,new URL(visualizer,location.href)).href;a.hidden=false;
     }
   }else{link.hidden=true;}
   const restoredId=preservedDraft?.selectedId;
   renderPresets();await loadPreset(presets.some(p=>p.id===restoredId)?restoredId:(presets.some(p=>p.id===selectedId)?selectedId:presets[0].id));
   if(preservedDraft&&preservedDraft.selectedId===selectedId&&preservedDraft.config&&typeof preservedDraft.config==='object'){
      config=merge(defaults,preservedDraft.config);renderSettings();updateSummary();
      $('unsavedLabel').textContent='Session draft restored · Save preset to keep changes permanently.';
   }
   if(new URLSearchParams(window.location.search).get("section")==="capacity") setTab("capacity");
   if(new URLSearchParams(window.location.search).get("view")==="history") selectView("history");
   updateStatus(bootstrap.status);await refreshHeaderRuns();setInterval(refreshStatus,1300);
   
 }catch(e){toast('Control server error: '+e.message,true)}
}
document.querySelectorAll('[data-view]').forEach(el=>el.onclick=(event)=>{event.preventDefault();selectView(el.dataset.view);history.replaceState(null,'',el.dataset.view==='history'?'?view=history':'/');});
$('runSelect').addEventListener('change',event=>{const run=event.target.value;if(run){writeSelectedRun(run);updateRunNavigation(run);location.href=runVisualizerUrl(run)||'/topology/?run='+encodeURIComponent(run)}});
document.querySelectorAll('.tab').forEach(el=>el.onclick=()=>setTab(el.dataset.section));
$('loadDefault').onclick=()=>loadPreset(selectedId);
$('togglePresetList').onclick=()=>{
 const aside=document.querySelector('.sidebar');
 const expanded=aside.classList.toggle('mobile-expanded');
 $('togglePresetList').setAttribute('aria-expanded',String(expanded));
 $('togglePresetList').textContent=expanded?'Hide scenarios ▴':'Choose scenario ▾';
};
$('refreshPresets').onclick=async()=>{try{const data=await api('/api/bootstrap');presets=data.presets;renderPresets();toast('Scenario library refreshed')}catch(e){toast(e.message,true)}};
$('saveButton').onclick=savePreset;$('startButton').onclick=startExperiment;$('stopButton').onclick=stopExperiment;
$('closeRunDetails').onclick=()=>$('runDetail').classList.add('hidden');
$('refreshHistory').onclick=()=>loadHistory();
$('historyPrevious').onclick=()=>{historyOffset=Math.max(0,historyOffset-HISTORY_PAGE_SIZE);loadHistory()};
$('historyNext').onclick=()=>{historyOffset+=HISTORY_PAGE_SIZE;loadHistory()};
let historySearchDebounce=null;
$('historySearch').addEventListener('input',()=>{
  clearTimeout(historySearchDebounce);
  historySearchDebounce=setTimeout(()=>{historyOffset=0;loadHistory()},220);
});
$('copyLog').onclick=()=>navigator.clipboard.writeText($('console').textContent).then(()=>toast('Console copied.')).catch(()=>toast('Could not copy console',true));
initialize();
