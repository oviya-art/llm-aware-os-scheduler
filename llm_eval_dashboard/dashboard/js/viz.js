/* Canvas renderers for replay data (shared by the dashboard preview and the live arena). */
const Viz=(()=>{
  let pal=null,palTheme="";
  function P(){const th=document.documentElement.dataset.theme;if(pal&&palTheme===th)return pal;palTheme=th;
    pal={high:"#8B7CFF",normal:"#36D6C3",low:"#E39BFF",bg:th==="light"?"#C9D1E8":"#2A3558",bgHi:th==="light"?"#B4BEDC":"#36436E",lane:th==="light"?"#E6EAF6":"#0F1630",
      grid:th==="light"?"#D5DCEE":"#1B2442",txt:th==="light"?"#5A6685":"#8D99BD",ok:"#3DDC97",late:"#FF5C6C",now:th==="light"?"#0D1530":"#EAF0FF"};return pal}
  const bs=(arr,t,k)=>{let lo=0,hi=arr.length;while(lo<hi){const m=(lo+hi)>>1;if(arr[m][k]<=t)lo=m+1;else hi=m}return lo};
  function prep(R,key){const run=R.runs[key];if(!run._seg){run._seg=run.segments.slice().sort((a,b)=>a[2]-b[2]);run._tok=run.tokens;
      run._per=R.requests.map(()=>[]);run.tokens.forEach(t=>run._per[t[0]].push(t))}return run}
  function ctx(c,hgt){const r=devicePixelRatio||1,w=c.clientWidth,h=hgt||c.clientHeight||+c.getAttribute("height");
    if(c.width!==Math.round(w*r)||c.height!==Math.round(h*r)){c.width=Math.round(w*r);c.height=Math.round(h*r)}
    c.style.height=h+"px";const x=c.getContext("2d");x.setTransform(r,0,0,r,0,0);return[x,w,h]}
  const segColor=(R,s)=>s[1]<R.requests.length?(P()[R.requests[s[1]].prio]||P().normal):P().bg;

  function timeline(c,R,key,t,{win=6000,rowH=26,labels=true}={}){
    const run=prep(R,key),pl=P(),cores=R.meta.cores,top=8,L=labels?46:8,h=top*2+cores*rowH+22,[x,w]=ctx(c,h);
    const t0=Math.max(0,t-win),t1=Math.max(win,t),X=lin(t0,t1,L,w-8);
    x.clearRect(0,0,w,h);x.font="600 11px Figtree,system-ui,sans-serif";x.textBaseline="middle";
    for(let k=0;k<cores;k++){x.fillStyle=pl.lane;rr(x,L,top+k*rowH+2,w-L-8,rowH-4,6);x.fill();if(labels){x.fillStyle=pl.txt;x.textAlign="right";x.fillText("core "+k,L-8,top+k*rowH+rowH/2)}}
    const step=niceTicks(t0/1000,t1/1000,7);x.textAlign="center";step.forEach(s=>{const px=X(s*1000);x.fillStyle=pl.txt;x.fillText(s+"s",px,top+cores*rowH+12);x.strokeStyle=pl.grid;x.beginPath();x.moveTo(px,top);x.lineTo(px,top+cores*rowH);x.stroke()});
    const n=bs(run._seg,t,2);
    for(let i=bs(run._seg,t0-60000,2);i<n;i++){const s=run._seg[i];if(s[3]<t0)continue;const a=X(Math.max(s[2],t0)),b=X(Math.min(s[3],t)),bw=Math.max(b-a,1),inf=s[1]<R.requests.length;
      x.fillStyle=segColor(R,s);rr(x,a,top+s[0]*rowH+3,bw,rowH-6,bw>6?5:1);x.fill();
      if(bw>30&&inf){x.fillStyle="#08101f";x.textAlign="left";x.font="800 11px Figtree,system-ui,sans-serif";x.fillText(String(s[1]+1),a+6,top+s[0]*rowH+rowH/2);x.font="600 11px Figtree,system-ui,sans-serif"}}
    const px=X(t);x.strokeStyle=pl.now;x.lineWidth=2;x.beginPath();x.moveTo(px,top-4);x.lineTo(px,top+cores*rowH+2);x.stroke();x.lineWidth=1;
    c._geo={X,t0,t1,top,rowH,L,cores,run,R,n};
  }
  function rr(x,a,b,w,h,r){r=Math.min(r,w/2,h/2);x.beginPath();x.moveTo(a+r,b);x.arcTo(a+w,b,a+w,b+h,r);x.arcTo(a+w,b+h,a,b+h,r);x.arcTo(a,b+h,a,b,r);x.arcTo(a,b,a+w,b,r);x.closePath()}
  function hitTimeline(c,ev){const g=c._geo;if(!g)return null;const b=c.getBoundingClientRect(),mx=ev.clientX-b.left,my=ev.clientY-b.top;
    const core=Math.floor((my-g.top)/g.rowH);if(core<0||core>=g.cores||mx<g.L)return null;const tm=g.X.inv(mx);
    for(let i=g.n-1;i>=Math.max(0,g.n-400);i--){const s=g.run._seg[i];if(s[0]===core&&s[2]<=tm&&tm<=s[3]){
      const inf=s[1]<g.R.requests.length,r=inf?g.R.requests[s[1]]:g.R.background[s[1]-g.R.requests.length];
      return `<b>${esc(r.id)}</b> on core ${core}<br>${inf?esc(r.type)+" request, "+esc(r.prio)+" priority":"background job"}<br>ran ${fmt(s[3]-s[2],1)} ms, ${esc(g.run.reasons[s[4]]||"").replace(/_/g," ")}`}}return null}

  function scatter(c,R,key,t,{win=40000,h=170}={}){
    const run=prep(R,key),pl=P(),[x,w]=ctx(c,h),L=34,B=20,t0=Math.max(0,t-win),t1=Math.max(win,t),X=lin(t0,t1,L,w-8),Y=lin(0,3,h-B,8);
    x.clearRect(0,0,w,h);x.font="600 11px Figtree,system-ui,sans-serif";x.textBaseline="middle";x.textAlign="right";
    [0,1,2,3].forEach(v=>{x.fillStyle=pl.txt;x.fillText(v+"x",L-7,Y(v));x.strokeStyle=pl.grid;x.beginPath();x.moveTo(L,Y(v));x.lineTo(w-8,Y(v));x.stroke()});
    x.fillStyle="rgba(255,92,108,.07)";x.fillRect(L,8,w-L-8,Y(1)-8);
    x.setLineDash([5,4]);x.strokeStyle=pl.late;x.lineWidth=1.5;x.beginPath();x.moveTo(L,Y(1));x.lineTo(w-8,Y(1));x.stroke();x.setLineDash([]);x.lineWidth=1;
    const n=bs(run._tok,t,1);
    for(let i=bs(run._tok,t0,1);i<n;i++){const k=run._tok[i],r=k[2]/k[3],px=X(k[1]),py=Y(Math.min(r,3));x.fillStyle=k[4]?pl.late:"rgba(61,220,151,.6)";x.beginPath();
      if(k[5]===0)rr(x,px-3,py-3,6,6,1.5);else x.arc(px,py,k[4]?2.8:1.9,0,6.3);x.fill()}
    const px=X(t);x.strokeStyle=pl.now;x.lineWidth=2;x.beginPath();x.moveTo(px,6);x.lineTo(px,h-B);x.stroke();x.lineWidth=1;
  }
  function counts(R,key,t){const run=prep(R,key),n=bs(run._tok,t,1);let late=0;for(let i=0;i<n;i++)late+=run._tok[i][4];return{n,late,pct:n?100*late/n:0,disp:bs(run._seg,t,2),run}}
  return{prep,timeline,scatter,hitTimeline,counts,bs,P}
})();
