// Workforce Runtime — system explainer animation.
// Deterministic function of timeline time `t` so scrubbing + video export work.
// One agent → a few independent → organize into a tree → it GROWS into a full,
// real-world company org (integrated, not a flat grid). Lower agents use the two
// supplied app icons. Discuss = comet streaks; assign/report = up/down pulses.

const { Stage, useTime, interpolate, Easing, clamp } = window;

const W = 1280, H = 720;
const lerp = (a, b, t) => a + (b - a) * t;
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const eio = Easing.easeInOutCubic, eob = Easing.easeOutBack;
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}

const SANS = '-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif';
const ICON = ['uploads/codex-rounded.png', 'uploads/electron-256.png'];

// ── Org definition (real-world, asymmetric C-suite) ─────────────────────────
const EXECS = [
  { id:'eng',  title:'Engineering',   sub:'CTO', initial:true  },
  { id:'prod', title:'Product',       sub:'CPO', initial:true  },
  { id:'ops',  title:'Operations',    sub:'COO', initial:true  },
  { id:'gtm',  title:'Go-to-Market',  sub:'CRO', initial:false },
  { id:'data', title:'Data',          sub:'CDO', initial:false },
];
const LEADS = [
  { id:'l_plat',  exec:'eng',  label:'Platform',    initial:true,  fan:9  },
  { id:'l_peng',  exec:'eng',  label:'Product Eng', initial:true,  fan:10 },
  { id:'l_infra', exec:'eng',  label:'Infra',       initial:true,  fan:7  },
  { id:'l_sec',   exec:'eng',  label:'Security',    initial:false, fan:6  },
  { id:'l_design',exec:'prod', label:'Design',      initial:true,  fan:7  },
  { id:'l_pm',    exec:'prod', label:'PM',          initial:true,  fan:8  },
  { id:'l_web',   exec:'prod', label:'Web',         initial:true,  fan:6  },
  { id:'l_fin',   exec:'ops',  label:'Finance',     initial:true,  fan:6  },
  { id:'l_ppl',   exec:'ops',  label:'People',      initial:true,  fan:7  },
  { id:'l_sup',   exec:'ops',  label:'Support',     initial:true,  fan:8  },
  { id:'l_sales', exec:'gtm',  label:'Sales',       initial:false, fan:9  },
  { id:'l_mkt',   exec:'gtm',  label:'Marketing',   initial:false, fan:8  },
  { id:'l_suc',   exec:'gtm',  label:'Success',     initial:false, fan:7  },
  { id:'l_res',   exec:'data', label:'Research',    initial:false, fan:7  },
  { id:'l_ana',   exec:'data', label:'Analytics',   initial:false, fan:6  },
];
const _ri = mulberry32(11);
LEADS.forEach(l => { l.icon = _ri() < 0.5 ? 0 : 1; l.bob = _ri()*6.28; l.yoff = Math.round((_ri()*2-1)*58 + (_ri()<0.3 ? 120 : 0)); });
const AGENTS = [];
LEADS.forEach(l => { for (let k=0;k<l.fan;k++) AGENTS.push({ id:l.id+'_a'+k, lead:l.id, exec:l.exec, icon:_ri()<0.5?0:1, bob:_ri()*6.28, yj:Math.round((_ri()*2-1)*14) }); });

const Y = { human:-300, ceo:-150, exec:14, lead:212, agent:470 };
const S_FULL = 22, LEAD_GAP = 18, EXEC_GAP = 58, S_INIT = 120;
const FULL_ORDER = ['gtm','eng','prod','ops','data'];

// ── FULL layout (leaves = agents; parents centered over children) ───────────
const FULLP = {};
{
  let x = 0; const leaves = [];
  FULL_ORDER.forEach((eid, ei) => {
    if (ei > 0) x += EXEC_GAP;
    LEADS.filter(l => l.exec === eid).forEach((l, lj) => {
      if (lj > 0) x += LEAD_GAP;
      AGENTS.filter(a => a.lead === l.id).forEach(a => { a._x = x; leaves.push(a); x += S_FULL; });
    });
  });
  const mean = leaves.reduce((s,a)=>s+a._x,0)/leaves.length;
  leaves.forEach((a,i) => { const L = LEADS.find(x=>x.id===a.lead); FULLP[a.id] = { x:a._x-mean, y:Y.agent + L.yoff + a.yj }; a.appear = 16.4 + (i/leaves.length)*5.2; });
  LEADS.forEach(l => { const ag = AGENTS.filter(a=>a.lead===l.id); FULLP[l.id] = { x:ag.reduce((s,a)=>s+FULLP[a.id].x,0)/ag.length, y:Y.lead }; });
  EXECS.forEach(e => { const ld = LEADS.filter(l=>l.exec===e.id); FULLP[e.id] = { x:ld.reduce((s,l)=>s+FULLP[l.id].x,0)/ld.length, y:Y.exec }; });
  FULLP.ceo = { x:0, y:Y.ceo }; FULLP.human = { x:0, y:Y.human };
}

// ── INIT layout (the clean 1→3 tree; leaves = initial leads) ────────────────
const INITP = {};
const INIT_ORDER = ['eng','prod','ops'];
const initLeads = [];
INIT_ORDER.forEach(eid => LEADS.filter(l=>l.exec===eid && l.initial).forEach(l=>initLeads.push(l)));
{
  initLeads.forEach((l,i)=>l._ix = i*S_INIT);
  const mean = initLeads.reduce((s,l)=>s+l._ix,0)/initLeads.length;
  initLeads.forEach(l => INITP[l.id] = { x:l._ix-mean, y:Y.lead });
  INIT_ORDER.forEach(eid => { const ld = LEADS.filter(l=>l.exec===eid && l.initial); INITP[eid] = { x:ld.reduce((s,l)=>s+INITP[l.id].x,0)/ld.length, y:Y.exec }; });
  INITP.ceo = { x:0, y:Y.ceo }; INITP.human = { x:0, y:Y.human };
}
const CENTER_LEAD = initLeads[Math.floor(initLeads.length/2)].id;
const _rs = mulberry32(7);
initLeads.forEach((l,i) => {
  const center = l.id === CENTER_LEAD;
  const ang = _rs()*6.28, rad = 55 + _rs()*150;
  l.scatter = center ? { x:0, y:Y.lead } : { x:Math.cos(ang)*rad, y:Y.lead + Math.sin(ang)*rad*0.7 };
  l.bAppear = center ? -1 : 3.05 + i*0.16;
});

// ── appear times ────────────────────────────────────────────────────────────
function appearT(id){
  if (id === 'ceo') return 6.6;
  if (id === 'human') return 7.4;
  const e = EXECS.find(x=>x.id===id); if (e) return e.initial ? 6.95 : 15.8;
  const l = LEADS.find(x=>x.id===id); if (l) return l.initial ? (l.bAppear < 0 ? 0 : l.bAppear) : 16.2;
  const a = AGENTS.find(x=>x.id===id); if (a) return a.appear;
  return 0;
}
function nodeOp(id, t, dur){ return clamp01((t - appearT(id)) / (dur || 0.6)); }

// ── position of any node at time t ──────────────────────────────────────────
function pos(id, t){
  const f = FULLP[id], ini = INITP[id];
  if (ini && f){
    if (t < 15.5){
      const lead = LEADS.find(l=>l.id===id);
      if (lead && lead.initial){
        const e = eio(clamp01((t - 6.2)/2.4));
        return { x:lerp(lead.scatter.x, ini.x, e), y:lerp(lead.scatter.y, ini.y, e) };
      }
      return ini;
    }
    const sc = eio(clamp01((t - 15.5)/4.2));
    return { x:lerp(ini.x, f.x, sc), y:lerp(ini.y, f.y, sc) };
  }
  return f;
}

// ── edges ───────────────────────────────────────────────────────────────────
const EDGES = [{ a:'human', b:'ceo', app:6.7, w:1.7 }];
EXECS.forEach(e => EDGES.push({ a:'ceo', b:e.id, app:e.initial?7.0:15.9, w:1.6 }));
LEADS.forEach(l => EDGES.push({ a:l.exec, b:l.id, app:l.initial?7.4:16.3, w:1.3 }));
AGENTS.forEach(a => EDGES.push({ a:a.lead, b:a.id, app:a.appear, w:0.9 }));
const PULSE_EDGES = EDGES.filter(e => (e.a==='ceo') || EXECS.some(x=>x.id===e.a));

// ── comet routes (discuss) ──────────────────────────────────────────────────
const _rc = mulberry32(53);
function crossPair(pool, exclSame){
  let i = pool[Math.floor(_rc()*pool.length)], j = pool[Math.floor(_rc()*pool.length)];
  let guard = 0;
  while ((exclSame && i.exec === j.exec) || i.id === j.id){
    j = pool[Math.floor(_rc()*pool.length)];
    if (++guard > 40) break;
  }
  return { a:i.id, b:j.id, period:2.6 + _rc()*2.4, phase:_rc()*6.0, dur:0.55 + _rc()*0.3 };
}
const LEAD_COMETS = []; for (let k=0;k<6;k++) LEAD_COMETS.push(crossPair(initLeads, true));
const AGENT_COMETS = []; for (let k=0;k<26;k++) AGENT_COMETS.push(crossPair(AGENTS, true));

// ── shared docs / task records (independent of the hierarchy) ───────────────
const DOCS = { x:-540, y:-262, appear:17.4 };
const _rd = mulberry32(321);
const DOC_ROUTES = [];
for (let k=0;k<6;k++){
  const ag = AGENTS[Math.floor(_rd()*AGENTS.length)];
  const period = 3.4 + _rd()*2.4, phase = _rd()*6, dur = 0.85;
  DOC_ROUTES.push({ a:ag.id, b:'__docs', period, phase, dur });            // query out
  DOC_ROUTES.push({ a:'__docs', b:ag.id, period, phase:phase+dur+0.15, dur }); // context back
}

// ── small helpers ───────────────────────────────────────────────────────────
const CKEY = [0, 3, 6, 10.5, 15, 22.5, 28];
const camZoom = interpolate(CKEY, [2.5, 2.3, 1.38, 1.04, 1.0, 0.40, 0.385], eio);
const camCy   = interpolate(CKEY, [212, 196, 44, -44, -40, 82, 86], eio);

function elbow(A, B, drawP){
  const ex = lerp(A.x, B.x, drawP), ey = lerp(A.y, B.y, drawP);
  const my = (A.y + B.y) / 2;
  return `M ${A.x} ${A.y} C ${A.x} ${my}, ${ex} ${my}, ${ex} ${ey}`;
}
// cubic point on an elbow connector — assign/report ride this centerline
function cbz(P0,P1,P2,P3,u){ const v=1-u; return {
  x:v*v*v*P0.x+3*v*v*u*P1.x+3*v*u*u*P2.x+u*u*u*P3.x,
  y:v*v*v*P0.y+3*v*v*u*P1.y+3*v*u*u*P2.y+u*u*u*P3.y }; }
function edgePt(A,B,u){ const my=(A.y+B.y)/2; return cbz(A,{x:A.x,y:my},{x:B.x,y:my},B,u); }
// quadratic arc point — comets fly over on a curve, not straight across
function qpt(A,C,B,u){ const v=1-u; return { x:v*v*A.x+2*v*u*C.x+u*u*B.x, y:v*v*A.y+2*v*u*C.y+u*u*B.y }; }
function arcCtrl(A,B){ const d=Math.hypot(B.x-A.x,B.y-A.y); return { x:(A.x+B.x)/2 - (B.y-A.y)*0.05, y:(A.y+B.y)/2 - (46 + d*0.20) }; }

function comet(route, t, posFn, color, gate){
  const cyc = (t + route.phase) % route.period;
  if (cyc > route.dur) return null;
  const u = cyc / route.dur;
  const A = posFn(route.a, t), B = posFn(route.b, t);
  if (!A || !B) return null;
  const alpha = Math.sin(Math.PI * u) * gate;
  if (alpha <= 0.02) return null;
  const C = arcCtrl(A, B);
  const u0 = Math.max(0, u - 0.22);
  let d = ''; const N = 6;
  for (let k=0;k<=N;k++){ const uu = u0 + (u-u0)*(k/N); const p = qpt(A,C,B,uu); d += (k===0?'M ':'L ') + p.x.toFixed(1) + ' ' + p.y.toFixed(1); }
  const head = qpt(A,C,B,u);
  return { d, hx:head.x, hy:head.y, alpha, color, key: route.a+route.b };
}

function WorkforceScene(){
  const t = useTime();
  const rootRef = React.useRef(null);
  React.useEffect(() => { if (rootRef.current) rootRef.current.setAttribute('data-screen-label', 't=' + Math.floor(t) + 's'); }, [t]);

  const zoom = camZoom(t), cy = camCy(t);
  const tr = `translate(${W/2},${H/2}) scale(${zoom}) translate(0,${-cy})`;
  const chipF = 0.88 / zoom; // keep top chips a near-constant screen size so they stay legible

  // ── edges ──
  const edgeEls = EDGES.map((e, i) => {
    const draw = clamp01((t - e.app) / 0.6);
    if (draw <= 0.02) return null;
    const A = pos(e.a, t), B = pos(e.b, t);
    return <path key={'e'+i} d={elbow(A, B, draw)} fill="none" stroke="#d0cbc2"
                 strokeWidth={e.w} vectorEffect="non-scaling-stroke" strokeLinecap="round" opacity={0.92}/>;
  });

  // ── assign (down, gold) + report (up, green) pulses ──
  const pulseEls = [];
  if (t > 10.6){
    PULSE_EDGES.forEach((e, i) => {
      const vis = Math.min(nodeOp(e.a, t), nodeOp(e.b, t));
      if (vis <= 0.05) return;
      const A = pos(e.a, t), B = pos(e.b, t);
      const dn = (t*0.5 + i*0.13) % 1; const pd = edgePt(A, B, dn);
      pulseEls.push(<circle key={'pd'+i} cx={pd.x} cy={pd.y} r={4.6} fill="#c08a32" opacity={0.9*vis}/>);
      const up = (t*0.4 + i*0.27 + 0.5) % 1; const pu = edgePt(B, A, up);
      pulseEls.push(<circle key={'pu'+i} cx={pu.x} cy={pu.y} r={4.2} fill="#4a8b63" opacity={0.82*vis}/>);
    });
  }

  // ── comets (discuss) ──
  const cometEls = [];
  const pushComet = (c) => {
    if (!c) return;
    cometEls.push(
      <g key={'c'+c.key} opacity={c.alpha}>
        <path d={c.d} fill="none" stroke={c.color} strokeWidth={2.6}
              vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" opacity={0.72}/>
        <circle cx={c.hx} cy={c.hy} r={9} fill={c.color} opacity={0.16}/>
        <circle cx={c.hx} cy={c.hy} r={3.3} fill="#f3faff"/>
      </g>
    );
  };
  const docPos = (id) => id === '__docs' ? DOCS : FULLP[id];
  if (t > 10.8) LEAD_COMETS.forEach(r => pushComet(comet(r, t, pos, '#6fa6c4', clamp01((t-10.8)/1.5))));
  if (t > 17.0) AGENT_COMETS.forEach(r => pushComet(comet(r, t, (id)=>FULLP[id], '#6fa6c4',
      clamp01((t-17.0)/2) * Math.min(nodeOp(r.a, t), nodeOp(r.b, t)))));
  if (t > DOCS.appear + 0.4) DOC_ROUTES.forEach(r => pushComet(comet(r, t, docPos, '#b39a6a',
      clamp01((t-DOCS.appear-0.4)/1.2) * (r.a==='__docs'?nodeOp(r.b,t):nodeOp(r.a,t)))));

  // ── exec / ceo / human chips ──
  const chips = [];
  // human
  {
    const op = nodeOp('human', t, 0.55);
    if (op > 0.02){ const p = pos('human', t); const s = lerp(0.8,1,eob(op));
      chips.push(
        <g key="hu" transform={`translate(${p.x},${p.y}) scale(${s*chipF})`} opacity={op}>
          <rect x={-56} y={-23} width={112} height={46} rx={10} fill="#ffffff" stroke="#d8d4cc" strokeWidth={1} vectorEffect="non-scaling-stroke"/>
          <circle cx={-38} cy={0} r={4.5} fill="#1c1b19"/>
          <text x={-26} y={-4} dominantBaseline="central" fontFamily={SANS} fontSize={14} fontWeight={650} fill="#1c1b19" letterSpacing="-0.01em">Human</text>
          <text x={-26} y={9} dominantBaseline="central" fontFamily={SANS} fontSize={8} fontWeight={650} fill="#a39e95" letterSpacing="0.13em">SETS THE GOAL</text>
        </g>);
    }
  }
  // ceo
  {
    const op = nodeOp('ceo', t, 0.55);
    if (op > 0.02){ const p = pos('ceo', t); const s = lerp(0.8,1,eob(op));
      chips.push(
        <g key="ceo" transform={`translate(${p.x},${p.y}) scale(${s*chipF})`} opacity={op}>
          <rect x={-46} y={-22} width={92} height={44} rx={10} fill="#201f1c"/>
          <text x={0} y={-4} textAnchor="middle" dominantBaseline="central" fontFamily={SANS} fontSize={15} fontWeight={680} fill="#f3f1ec">CEO</text>
          <text x={0} y={9} textAnchor="middle" dominantBaseline="central" fontFamily={SANS} fontSize={7.5} fontWeight={650} fill="#8f8b83" letterSpacing="0.14em">CHIEF AGENT</text>
        </g>);
    }
  }
  // execs
  EXECS.forEach(e => {
    const op = nodeOp(e.id, t, 0.55);
    if (op <= 0.02) return;
    const p = pos(e.id, t); const s = lerp(0.82,1,eob(op));
    chips.push(
      <g key={e.id} transform={`translate(${p.x},${p.y}) scale(${s*chipF})`} opacity={op}>
        <rect x={-66} y={-22} width={132} height={44} rx={9} fill="#262420"/>
        <text x={0} y={-6} textAnchor="middle" dominantBaseline="central" fontFamily={SANS} fontSize={8} fontWeight={700} fill="#c79a5a" letterSpacing="0.14em">{e.sub}</text>
        <text x={0} y={7} textAnchor="middle" dominantBaseline="central" fontFamily={SANS} fontSize={13.5} fontWeight={650} fill="#ece9e3" letterSpacing="-0.01em">{e.title}</text>
      </g>);
  });

  // shared docs / task records — independent node off to the side
  {
    const op = clamp01((t - DOCS.appear)/0.6);
    if (op > 0.02){ const s = lerp(0.82,1,eob(op));
      chips.push(
        <g key="docs" transform={`translate(${DOCS.x},${DOCS.y}) scale(${s*chipF})`} opacity={op}>
          <rect x={-100} y={-31} width={200} height={62} rx={13} fill="#ffffff" stroke="#ddd9d2" strokeWidth={1} vectorEffect="non-scaling-stroke"/>
          <rect x={-86} y={-15} width={26} height={32} rx={3} fill="#f1efea" stroke="#cfcabf" strokeWidth={1} vectorEffect="non-scaling-stroke"/>
          <rect x={-80} y={-20} width={26} height={32} rx={3} fill="#ffffff" stroke="#bdb8ae" strokeWidth={1.2} vectorEffect="non-scaling-stroke"/>
          <line x1={-75} y1={-12} x2={-60} y2={-12} stroke="#cfcabf" strokeWidth={1.4} vectorEffect="non-scaling-stroke" strokeLinecap="round"/>
          <line x1={-75} y1={-5} x2={-60} y2={-5} stroke="#cfcabf" strokeWidth={1.4} vectorEffect="non-scaling-stroke" strokeLinecap="round"/>
          <line x1={-75} y1={2} x2={-64} y2={2} stroke="#cfcabf" strokeWidth={1.4} vectorEffect="non-scaling-stroke" strokeLinecap="round"/>
          <text x={-44} y={-7} textAnchor="start" dominantBaseline="central" fontFamily={SANS} fontSize={12.5} fontWeight={650} fill="#2a2823">Shared Context</text>
          <text x={-44} y={8} textAnchor="start" dominantBaseline="central" fontFamily={SANS} fontSize={7} fontWeight={650} fill="#a39e95" letterSpacing="0.1em">DOCS · TASK RECORDS</text>
        </g>);
    }
  }

  // ── lead + agent icons (HTML <img> in a camera-transformed layer) ──
  const LEAD_SZ = 44, AGENT_SZ = 20;
  const labelOp = clamp01((t-7.6)/0.8) * (1 - clamp01((t-15.3)/1.6));
  const htmlIcons = [];
  const labelEls = [];
  LEADS.forEach(l => {
    const op = nodeOp(l.id, t, 0.6);
    if (op <= 0.02) return;
    const p = pos(l.id, t);
    const sc = lerp(0.45, 1, eob(op));
    const bob = Math.sin(t*1.3 + l.bob) * 2.2;
    const s = LEAD_SZ * sc;
    htmlIcons.push(<img key={l.id} src={ICON[l.icon]} draggable="false"
      style={{ position:'absolute', left:p.x - s/2, top:p.y - s/2 + bob, width:s, height:s, opacity:op }}/>);
    if (labelOp > 0.03 && l.initial)
      labelEls.push(<text key={'lb'+l.id} x={p.x} y={p.y + LEAD_SZ/2 + 13 + bob} textAnchor="middle" dominantBaseline="central"
            fontFamily={SANS} fontSize={11} fontWeight={600} fill="#76726b" opacity={labelOp}>{l.label}</text>);
  });
  AGENTS.forEach(a => {
    const op = nodeOp(a.id, t, 0.55);
    if (op <= 0.02) return;
    const p = pos(a.id, t);
    const sc = lerp(0.4, 1, eob(op));
    const bob = Math.sin(t*1.5 + a.bob) * 1.6;
    const s = AGENT_SZ * sc;
    htmlIcons.push(<img key={a.id} src={ICON[a.icon]} draggable="false"
      style={{ position:'absolute', left:p.x - s/2, top:p.y - s/2 + bob, width:s, height:s, opacity:op }}/>);
  });

  // ── captions ──
  const cap = (s, e) => Math.min(clamp01((t - s)/0.45), clamp01((e - t)/0.45));
  const captions = [
    { s:0.3,  e:3.0,  text:'A single agent' },
    { s:3.0,  e:6.0,  text:'Spin up more — each works on its own' },
    { s:6.0,  e:10.5, text:'They organize into a clear hierarchy' },
    { s:10.5, e:15.0, text:'Delegating down · reporting up · discussing across' },
    { s:15.2, e:18.6, text:'The organization grows into a whole company' },
    { s:18.8, e:23.4, text:'Agents pull shared context & task records' },
  ];
  const ctxOp = clamp01((t - 6.0)/0.8);

  return (
    <div ref={rootRef} style={{ position:'absolute', inset:0, overflow:'hidden', background:'#f4f3f0', fontFamily:SANS }}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position:'absolute', inset:0, display:'block' }}>
        <g transform={tr}>
          {edgeEls}
          {pulseEls}
          {chips}
          {labelEls}
        </g>
      </svg>

      {/* icon layer — HTML <img>, same camera transform as the svg <g> */}
      <div style={{ position:'absolute', left:0, top:0, width:W, height:H, pointerEvents:'none',
              transformOrigin:'0 0', transform:`translate(${W/2}px,${H/2}px) scale(${zoom}) translate(0px,${-cy}px)` }}>
        {htmlIcons}
      </div>

      {/* comet (discuss) layer — above icons so streaks read */}
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position:'absolute', inset:0, display:'block', pointerEvents:'none' }}>
        <g transform={tr}>{cometEls}</g>
      </svg>

      {/* top-left product context chip */}
      <div style={{ position:'absolute', left:26, top:24, display:'flex', alignItems:'center', gap:9,
                    background:'#ffffff', border:'1px solid #e6e3de', borderRadius:11, padding:'8px 14px',
                    boxShadow:'0 1px 2px rgba(28,27,25,.04)', opacity:ctxOp }}>
        <span style={{ width:8, height:8, borderRadius:'50%', background:'#4a8b63', boxShadow:'0 0 0 4px rgba(74,139,99,.16)' }}></span>
        <span style={{ fontSize:13, fontWeight:650, color:'#2a2823' }}>Workforce Runtime</span>
        <span style={{ width:3, height:3, borderRadius:'50%', background:'#cfcabf' }}></span>
        <span style={{ fontSize:11, letterSpacing:'.05em', textTransform:'uppercase', color:'#a39e95' }}>Operating</span>
      </div>

      {/* bottom caption */}
      {captions.map((c, i) => {
        const o = cap(c.s, c.e);
        if (o <= 0.01) return null;
        return (
          <div key={i} style={{ position:'absolute', left:'50%', bottom:58, transform:'translateX(-50%)',
                  opacity:o, display:'flex', alignItems:'center', gap:10, background:'rgba(255,255,255,.88)',
                  backdropFilter:'blur(6px)', border:'1px solid #e6e3de', borderRadius:11,
                  padding:'10px 18px', boxShadow:'0 4px 18px rgba(28,27,25,.08)', whiteSpace:'nowrap' }}>
            <span style={{ width:6, height:6, borderRadius:'50%', background:'#b07d2f' }}></span>
            <span style={{ fontSize:16, fontWeight:600, color:'#2a2823', letterSpacing:'-.01em' }}>{c.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function WorkforceAnim(){
  return (
    <div style={{ position:'fixed', inset:0, background:'#0a0a0a' }}>
      <Stage width={W} height={H} duration={28} background="#f4f3f0" persistKey="wfruntime-anim">
        <WorkforceScene/>
      </Stage>
    </div>
  );
}

window.WorkforceScene = WorkforceScene;
window.WorkforceAnim = WorkforceAnim;
