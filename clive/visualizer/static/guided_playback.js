"use strict";
/**
 * Guided playback increases PRESENTATION time at protocol events; it never
 * changes the recorded simulation timestamp or the order of the log.
 * Repeated timestamps are stepped individually and presented as simultaneous.
 */
const CliveGuidedPlayback = (() => {
  const HOLD_MS = 850;
  function indexAtOrBefore(events, t) {
    let lo=0, hi=events.length;
    while(lo<hi) {const m=(lo+hi)>>>1; if(Number(events[m].time_s)<=t) lo=m+1; else hi=m;}
    return lo-1;
  }
  function create() {return {time:0, cursor:-1, focus:null, holdUntil:0, anchorWall:0, anchorTime:0};}
  function seek(events,t,wall=0) {
    const time=Math.max(0,Number(t)||0), cursor=time === 0 ? -1 : indexAtOrBefore(events,time);
    return {time, cursor, focus:cursor>=0?events[cursor]:null, holdUntil:0, anchorWall:wall, anchorTime:time};
  }
  function resume(current,wall) {
    return {...current,anchorWall:wall,anchorTime:current.time,holdUntil:0};
  }
  function next(current,events) {
    const idx=current.cursor;
    if(idx+1>=events.length) return {...current,holdUntil:0};
    const event=events[idx+1];
    return {time:Number(event.time_s), cursor:idx+1, focus:event,holdUntil:0,anchorWall:0,anchorTime:Number(event.time_s)};
  }
  function advance(current,wall,speed,duration,events,holdMs=HOLD_MS) {
    if(current.holdUntil>wall) return current;
    if(current.holdUntil) return resume(current,wall);
    const target=Math.min(duration, current.anchorTime + Math.max(0,wall-current.anchorWall)/1000*speed);
    const nextRow=events[current.cursor+1];
    if(nextRow && Number(nextRow.time_s)<=target+1e-9 && Number(nextRow.time_s)<=duration) {
      const time=Number(nextRow.time_s);
      return {time,cursor:current.cursor+1,focus:nextRow,holdUntil:wall+holdMs,anchorWall:wall,anchorTime:time};
    }
    return {...current,time:target};
  }
  return Object.freeze({HOLD_MS,create,seek,resume,next,advance,indexAtOrBefore});
})();
if(typeof module!=='undefined' && module.exports) module.exports=CliveGuidedPlayback;
