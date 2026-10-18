/* Dependency-free SVG charts, themed through CSS variables. */
const lin=(d0,d1,r0,r1)=>{const k=(r1-r0)/((d1-d0)||1);const f=x=>r0+(x-d0)*k;f.inv=y=>d0+(y-r0)/k;return f};
function niceTicks(a,b,n=5){const span=b-a||1,step0=span/n,mag=Math.pow(10,Math.floor(Math.log10(step0))),err=step0/mag;
  const step=(err>=5?10:err>=2?5:err>=1?2:1)*mag;const out=[];for(let v=Math.ceil(a/step)*step;v<=b+1e-9;v+=step)out.push(+v.toFixed(10));return out}
function svgWrap(node,w,h,inner){node.innerHTML=`<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" style="display:block;height:auto;overflow:visible">${inner}</svg>`;return node.firstChild}
const GRID="var(--line)";
const axisX=(x,ticks,y,f=v=>v)=>ticks.map(t=>`<text x="${x(t)}" y="${y+18}" text-anchor="middle" font-size="12">${f(t)}</text>`).join("");
const axisY=(y,ticks,x,w,f=v=>v)=>ticks.map(t=>`<line x1="${x}" x2="${x+w}" y1="${y(t)}" y2="${y(t)}" stroke="${GRID}" stroke-dasharray="3 5"/><text x="${x-9}" y="${y(t)+4}" text-anchor="end" font-size="12">${f(t)}</text>`).join("");

/* horizontal bars with optional CI whiskers: rows [{label,color,v,lo,hi,bold}] */
function hbars(node,rows,{unit="",max,w=560,rowH=34,left=76,dec=1}={}){
  const m=max||Math.max(...rows.map(r=>(r.hi??r.v)||0))*1.12||1,x=lin(0,m,left,w-64),h=rows.length*rowH+8;let s="";
  niceTicks(0,m,4).forEach(t=>{s+=`<line x1="${x(t)}" x2="${x(t)}" y1="0" y2="${h-6}" stroke="${GRID}" stroke-dasharray="3 5"/>`});
  rows.forEach((r,i)=>{const y=i*rowH+4,mid=y+(rowH-8)/2;
    s+=`<text x="${left-12}" y="${mid+4}" text-anchor="end" font-size="14" font-weight="${r.bold?800:600}" style="fill:${r.bold?"var(--text)":"var(--mute)"}">${esc(r.label)}</text>
    <rect x="${left}" y="${y+3}" width="${Math.max(2,x(r.v)-left)}" height="${rowH-14}" rx="5" fill="${r.color}" opacity="${r.bold?1:.8}" data-i="${i}"/>`;
    if(r.lo!=null&&r.hi!=null)s+=`<path d="M${x(r.lo)} ${mid}H${x(r.hi)}M${x(r.lo)} ${mid-5}v10M${x(r.hi)} ${mid-5}v10" stroke="var(--text)" stroke-opacity=".7" stroke-width="1.5" fill="none"/>`;
    s+=`<text x="${x(r.hi??r.v)+10}" y="${mid+4}" font-size="13" font-weight="800" style="fill:var(--text)">${fmt(r.v,dec)}${unit}</text>`});
  const sv=svgWrap(node,w,h,s);
  onHover(sv,e=>{const t=e.target.closest("[data-i]");if(!t)return null;const r=rows[+t.dataset.i];return `<b>${esc(r.label)}</b><br>${fmt(r.v,dec+1)}${unit}${r.lo!=null?`<br>95% CI ${fmt(r.lo,dec+1)} to ${fmt(r.hi,dec+1)}`:""}`});
}
/* tiny trend line: pts numbers, highlight index */
function spark(node,pts,color,{w=200,h=40,hi=-1}={}){
  const x=lin(0,pts.length-1,4,w-4),mx=Math.max(...pts),mn=Math.min(...pts),y=lin(mn,mx===mn?mn+1:mx,h-6,6);
  const d=pts.map((v,i)=>(i?"L":"M")+x(i)+" "+y(v)).join("");
  svgWrap(node,w,h,`<defs><linearGradient id="sg${color.replace(/\W/g,"")}" x1="0" y1="0" x2="0" y2="1"><stop stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
   <path d="${d}L${x(pts.length-1)} ${h}L${x(0)} ${h}Z" fill="url(#sg${color.replace(/\W/g,"")})"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`+
   pts.map((v,i)=>`<circle cx="${x(i)}" cy="${y(v)}" r="${i===hi?4.5:2.5}" fill="${color}"/>`).join(""));
}
/* heat matrix: rows schedulers, cols scenarios, value request_miss_rate */
function heat(node,metric="request_miss_rate",{w=560,onCell}={}){
  const ks=SCN(),L=70,cw=(w-L)/ks.length,rh=40,h=rh*SCHED.length+30;let s="";
  ks.forEach((k,j)=>{s+=`<text x="${L+cw*j+cw/2}" y="16" text-anchor="middle" font-size="12" font-weight="700">${scnShort[k]}</text>`});
  SCHED.forEach((sc,i)=>{s+=`<text x="${L-10}" y="${30+i*rh+rh/2+4}" text-anchor="end" font-size="13" font-weight="${sc==="token_laxity"?800:600}" style="fill:${sc==="token_laxity"?"var(--text)":"var(--mute)"}">${SHORT[sc]}</text>`;
    ks.forEach((k,j)=>{const v=S(k,sc,metric),t=Math.min(1,v/100);
      s+=`<rect x="${L+cw*j+2}" y="${24+i*rh+2}" width="${cw-4}" height="${rh-4}" rx="8" fill="${t<.2?"var(--ok)":t<.5?"var(--warn)":"var(--late)"}" fill-opacity="${.16+t*.6}" data-t="${esc(LABEL[sc]+" in "+scnName(k)+": "+fmt(v,1)+"% of requests violated their SLA")}" ${sc==="token_laxity"?'stroke="var(--brand)" stroke-width="2"':""}/>
      <text x="${L+cw*j+cw/2}" y="${24+i*rh+rh/2+5}" text-anchor="middle" font-size="14" font-weight="800" style="fill:var(--text)" pointer-events="none">${fmt(v,0)}</text>`})});
  const sv=svgWrap(node,w,h,s);onHover(sv,e=>e.target.dataset.t?esc(e.target.dataset.t):null);
}
/* line chart: series [{pts:[[x,y]],color}] */
function lineChart(node,series,{w=520,h=230,xt,yfmt=v=>v,ymax,xfmt=v=>v,markers=true}={}){
  const L=46,B=30,xs=series.flatMap(s=>s.pts.map(p=>p[0])),ys=series.flatMap(s=>s.pts.map(p=>p[1])),X=lin(Math.min(...xs),Math.max(...xs),L+10,w-16),Y=lin(0,ymax||Math.max(...ys)*1.1,h-B,10);
  let s=axisY(Y,niceTicks(0,ymax||Math.max(...ys)*1.1,4),L,w-L-10,yfmt)+axisX(X,xt||[...new Set(xs)],h-B,xfmt);
  series.forEach(sr=>{s+=`<path d="${sr.pts.map((p,i)=>(i?"L":"M")+X(p[0])+" "+Y(p[1])).join("")}" fill="none" stroke="${sr.color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
    if(markers)s+=sr.pts.map(p=>`<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="5" fill="${sr.color}" stroke="var(--surface)" stroke-width="2" data-t="${esc(xfmt(p[0])+": "+fmt(p[1],2))}"/>`).join("")});
  const sv=svgWrap(node,w,h,s);onHover(sv,e=>e.target.dataset.t?esc(e.target.dataset.t):null);
}
/* legend with toggles */
function legend(node,items,state,onchange){node.className="legend";node.innerHTML="";
  items.forEach(it=>{const b=el("button",{"aria-pressed":state[it.k]!==false},`<i class="sw" style="background:${it.color}"></i>${esc(it.label)}`);
    b.onclick=()=>{state[it.k]=state[it.k]===false;b.setAttribute("aria-pressed",state[it.k]!==false);onchange()};node.appendChild(b)})}
const q=(a,p)=>{if(!a.length)return 0;const s=[...a].sort((x,y)=>x-y),k=(s.length-1)*p,f=Math.floor(k),c=Math.ceil(k);return s[f]+(s[c]-s[f])*(k-f)};
