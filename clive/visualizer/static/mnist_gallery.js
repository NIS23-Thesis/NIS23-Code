"use strict";
window.CliveMnistGallery = {
 escape: v => String(v??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
 card(r) {
  const e=this.escape, receipt=r.receipts?.[0], used=r.used_in||[];
  const detail = `Digit ${r.digit} · ${r.source} · collected ${r.collected_s == null ? 'unknown' : Number(r.collected_s).toFixed(3)+'s'} · ${receipt?'received by '+receipt.ch+' at '+Number(receipt.time_s).toFixed(3)+'s':'receipt unconfirmed'}${used.length?' · trained in '+used.map(x=>x.ch+' revision '+x.revision).join(', '):''}`;
  const data=typeof r.pixels_b64==='string'&&/^[A-Za-z0-9+/=]+$/.test(r.pixels_b64)?r.pixels_b64:'';
  return `<article class="mnist-tile" tabindex="0" title="${e(detail)}" aria-label="${e(detail)}"><canvas width="28" height="28" data-pixels="${data}" aria-hidden="true"></canvas><div class="mnist-hover">${e(detail)}</div></article>`;
 },
 paint(root){for(const canvas of root.querySelectorAll('canvas[data-pixels]:not([data-painted])')){
  canvas.dataset.painted='1';try{const raw=atob(canvas.dataset.pixels);if(raw.length!==784)throw Error('bad sample');
  const ctx=canvas.getContext('2d'),im=ctx.createImageData(28,28);
  for(let i=0;i<784;i++){const v=raw.charCodeAt(i),j=4*i;im.data[j]=v;im.data[j+1]=v;im.data[j+2]=v;im.data[j+3]=255}ctx.putImageData(im,0,0);
  }catch(_){canvas.parentElement.title='Sample pixels unavailable'}}},
 counts(data, top, more, toggle){const ranked=data.digit_counts||[];
  const make = xs=>xs.map(({digit,count})=>`<div class="digit-count" title="Digit ${digit}: ${count} samples"><strong>${digit}</strong><span>${count} ${count===1?'image':'images'}</span></div>`).join('');
  top.innerHTML=make(ranked.slice(0,3));more.innerHTML=make(ranked.slice(3));toggle.hidden=ranked.length<=3;
 }
};
