// Scene 7 — Governor Detects a Bottleneck.
// A governor watches org throughput. A queue spikes past threshold; the governor
// detects the bottleneck, scales the team, and throughput recovers.
(function () {
  const K = window.WFKit;
  const { C, MONO, SANS, useTime, appear, clamp01, lerp, eob, eoc, hexA,
    Root, NodeBox, AgentNode, Pill, Captions, centered } = K;

  const gov = { x:640, y:120 };
  // queue depth as a function of timeline τ — climbs, crosses threshold, recovers
  function q(tau){
    if (tau < 1.2) return 22 + tau*4;
    if (tau < 3.6) return 27 + (tau-1.2)*24;       // climb into bottleneck
    if (tau < 5.0) return 84 - (tau-3.6)*4;        // plateau while detected
    return Math.max(24, 78 - (tau-5.0)*22);        // recover after scaling
  }
  const THRESH = 70;
  // panel geometry
  const PX=360, PY=210, PW=560, PH=200, TMAX=8;

  function Scene(){
    const t = useTime();
    const overload = q(t) >= THRESH;
    const detected = t>=3.6;
    const scaling = t>=3.9 && t<5.4;
    const recovered = t>=5.6 && q(t) < THRESH;

    // build the revealed line up to current t
    const pts = [];
    const N = 80;
    for (let k=0;k<=N;k++){ const tau=(k/N)*TMAX; if (tau>t) break;
      const x = PX + (tau/TMAX)*PW;
      const y = PY+PH - (clamp01(q(tau)/100))*PH;
      pts.push([x,y]); }
    const line = pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
    const head = pts.length?pts[pts.length-1]:null;
    const threshY = PY+PH - (THRESH/100)*PH;
    const lineCol = overload?C.red : recovered?C.green : C.gold;

    const team = [
      { x:300, base:true }, { x:430, base:true }, { x:560, base:true },
      { x:760, base:false, app:4.0 }, { x:890, base:false, app:4.2 },
    ];

    return React.createElement(Root, { t, chip:{ state:'Governing', color:overload?C.red:C.green },
        title:{ kicker:'Capability', title:'Governor detects a bottleneck' } },
      React.createElement(NodeBox,{ cx:gov.x, cy:gov.y, op:appear(t,0,0.5),
        s:lerp(0.85,1,eob(appear(t,0,0.5))), title:'Governor', sub:'AUTOSCALER · MONITOR',
        kind:'ceo', w:170, accent:overload?C.red:C.green, glow:detected && t<5.6 }),
      // governor status pill
      React.createElement('div',{ style:{ ...centered(gov.x, gov.y+52, 1,
        appear(t, detected?3.7:0.4, 0.3)) } },
        React.createElement(Pill,{ kind: recovered?'healthy':(overload?'overloaded':'healthy'),
          label: recovered?'throughput recovered' : overload?'bottleneck detected':'monitoring' })),
      // metrics panel
      React.createElement('div',{ style:{ position:'absolute', left:PX-24, top:PY-46, width:PW+48,
        opacity:appear(t,0.5,0.5), fontFamily:SANS } },
        React.createElement('div',{ style:{ display:'flex', alignItems:'baseline', gap:10, marginBottom:6 } },
          React.createElement('span',{ style:{ fontSize:11, fontWeight:700, letterSpacing:'.1em',
            textTransform:'uppercase', color:C.muted } }, 'Search team · queue depth'),
          React.createElement('span',{ style:{ marginLeft:'auto', fontSize:26, fontWeight:720,
            color:lineCol, fontFamily:SANS, fontVariantNumeric:'tabular-nums' } }, Math.round(q(t))),
          React.createElement('span',{ style:{ fontSize:11, color:C.muted, fontFamily:MONO } }, '/ 100'))),
      React.createElement('svg',{ width:1280, height:720, viewBox:'0 0 1280 720',
        style:{ position:'absolute', inset:0, pointerEvents:'none', opacity:appear(t,0.6,0.5) } },
        // panel bg
        React.createElement('rect',{ x:PX-24, y:PY-8, width:PW+48, height:PH+40, rx:14,
          fill:C.cardBg, stroke:C.cardBorder, strokeWidth:1 }),
        // threshold line
        React.createElement('line',{ x1:PX, y1:threshY, x2:PX+PW, y2:threshY,
          stroke:hexA(C.red,0.5), strokeWidth:1.4, strokeDasharray:'5 5' }),
        React.createElement('text',{ x:PX+PW, y:threshY-6, textAnchor:'end', fontFamily:MONO,
          fontSize:10, fill:C.red, opacity:0.8 }, 'threshold'),
        // area + line
        head && React.createElement('path',{ d:`${line} L ${head[0]} ${PY+PH} L ${PX} ${PY+PH} Z`,
          fill:hexA(lineCol,0.10) }),
        head && React.createElement('path',{ d:line, fill:'none', stroke:lineCol, strokeWidth:2.6,
          strokeLinecap:'round', strokeLinejoin:'round' }),
        head && React.createElement('circle',{ cx:head[0], cy:head[1], r:4.5, fill:lineCol }),
        head && overload && React.createElement('circle',{ cx:head[0], cy:head[1], r:9,
          fill:'none', stroke:lineCol, strokeWidth:2, opacity:0.4 })),
      // scaling banner
      scaling && React.createElement('div',{ style:{ ...centered(640, PY+PH+86, 1,
        Math.min(appear(t,4.0,0.3),clamp01((5.4-t)/0.3))), display:'flex', alignItems:'center', gap:9,
        padding:'8px 16px', borderRadius:10, background:hexA(C.green,0.10), border:`1px solid ${hexA(C.green,0.4)}`,
        fontFamily:SANS, whiteSpace:'nowrap' } },
        React.createElement('svg',{ width:15, height:15, viewBox:'0 0 16 16', fill:'none' },
          React.createElement('path',{ d:'M8 2v12M2 8h12', stroke:C.green, strokeWidth:1.8, strokeLinecap:'round' })),
        React.createElement('span',{ style:{ fontSize:13, fontWeight:650, color:C.green } }, 'Scaling Search team  +2 agents')),
      // team row
      team.map((a,i)=>{ const op = a.base?appear(t,0.8+i*0.08,0.5):appear(t,a.app,0.5);
        if(op<=0.02) return null;
        const st = a.base?(overload && a.x===560?'overloaded':'working'):'working';
        return React.createElement(AgentNode,{ key:i, cx:a.x, cy:560, op, size:40,
          name:a.base?null:'new agent', icon:i%2, status:st, glow:!a.base && t<6.0 }); }),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.4, text:'The governor continuously watches throughput' },
        { s:1.6, e:3.5, text:'A queue climbs toward its threshold' },
        { s:3.7, e:5.2, text:'Bottleneck detected \u2014 the governor scales the team' },
        { s:5.4, e:7.0, text:'Throughput recovers below threshold' },
        { s:7.3, e:9, text:'The org self-corrects before work stalls.', final:true },
      ] })
    );
  }

  K.makeAnim('GovernorAnim', Scene, 9.2);
})();
