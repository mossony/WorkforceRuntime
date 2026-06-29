// Scene 3 — The CEO Summarizes the Organization for a Human.
// 100 agent results roll up into department summaries, the CEO compresses them
// into a single decision-ready report, delivered to one human.
(function () {
  const K = window.WFKit;
  const { C, MONO, SANS, useTime, appear, clamp01, seg, lerp, eio, eob, hexA,
    Root, Wires, NodeBox, Card, TaskChip, Captions, flight, centered } = K;

  function Scene(){
    const t = useTime();
    const mgrs = [
      { x:250, sub:'ENGINEERING', label:'Engineering', accent:C.gold },
      { x:520, sub:'SECURITY', label:'Security', accent:C.red },
      { x:790, sub:'PRODUCT', label:'Product', accent:C.blue },
      { x:1040, sub:'OPERATIONS', label:'Operations', accent:C.green },
    ];
    const ceo = { x:640, y:300 };
    const human = { x:640, y:108 };

    // worker dots rise from clusters into their manager
    const dots = []; let di=0;
    mgrs.forEach((m,ci)=>{ for(let k=0;k<12;k++){ const col=k%4, row=(k/4)|0;
      dots.push({ x:m.x-30+col*20, y0:602+row*15, mx:m.x, my:470, ci, d:di++ }); }});

    const stats = [
      { x:250, n:'100', l:'agent results' }, { x:475, n:'42', l:'reports' },
      { x:665, n:'18', l:'risks', c:C.red }, { x:845, n:'11', l:'decisions' }, { x:1035, n:'27', l:'artifacts' },
    ];
    const reportIn = appear(t,6.2,0.6);

    return React.createElement(Root, { t, chip:{ state:'Reporting up' },
        title:{ kicker:'Capability', title:'One report for the human' } },
      React.createElement(Wires,{ t, links:[
        { a:human, b:ceo, app:5.8 },
        ...mgrs.map(m=>({ a:ceo, b:{x:m.x,y:470}, app:4.1, op:0.95*clamp01((6.0-t)/0.6) })),
      ] }),
      // rising worker dots
      React.createElement('svg',{ width:1280, height:720, viewBox:'0 0 1280 720',
        style:{ position:'absolute', inset:0, pointerEvents:'none' } },
        dots.map(d=>{ const p=eio(seg(t, 2.0+d.ci*0.12+(d.d%12)*0.018, 3.9+d.ci*0.12));
          const op = Math.min(appear(t,0.3+(d.d%12)*0.03,0.4), clamp01((4.4-t)/0.5));
          if(op<=0.02) return null;
          return React.createElement('circle',{ key:d.d, cx:lerp(d.x,d.mx,p), cy:lerp(d.y0,d.my,p),
            r:3, fill:mgrs[d.ci].accent, opacity:op*0.85 }); })),
      // stat chips
      stats.map((s,i)=>{ const op=Math.min(appear(t,0.4+i*0.12,0.4),clamp01((2.4-t)/0.4));
        if(op<=0.02) return null;
        return React.createElement('div',{ key:i, style:{ ...centered(s.x,662,1,op), display:'flex',
          alignItems:'baseline', gap:6, whiteSpace:'nowrap' } },
          React.createElement('span',{ style:{ fontSize:22, fontWeight:720, color:s.c||C.ink, fontFamily:SANS } }, s.n),
          React.createElement('span',{ style:{ fontSize:10.5, color:C.muted, textTransform:'uppercase',
            letterSpacing:'.06em', fontFamily:MONO } }, s.l)); }),
      // department summaries
      mgrs.map((m,i)=>{ const op=Math.min(appear(t,3.5+i*0.1,0.5), clamp01((5.8-t)/0.5));
        if(op<=0.02) return null;
        return React.createElement(NodeBox,{ key:i, cx:m.x, cy:467, op,
          s:lerp(0.85,1,eob(appear(t,3.5+i*0.1,0.5))), title:m.label, sub:m.sub+' SUMMARY', w:170, accent:m.accent }); }),
      // summary chips fly into CEO
      mgrs.map((m,i)=>{ const r=flight(t,{x:m.x,y:467},ceo,{start:4.7+i*0.08,travel:1.0,hold:0.2});
        if(!r) return null;
        return React.createElement(TaskChip,{ key:i, cx:r.x, cy:r.y, op:r.op,
          label:m.label, accent:m.accent, w:118 }); }),
      React.createElement(NodeBox,{ cx:ceo.x, cy:ceo.y, op:appear(t,3.9,0.5),
        title:'CEO', sub:'CHIEF AGENT', kind:'ceo', w:92 }),
      React.createElement(NodeBox,{ cx:human.x, cy:human.y, op:appear(t,5.5,0.5),
        title:'Human', sub:'ONE DECISION', kind:'human', w:150, accent:C.green, glow:t>6.2 }),
      // final compressed report
      (t>6.0) && React.createElement(Card,{ cx:ceo.x, cy:430, op:reportIn,
        s:lerp(0.92,1,eob(reportIn)), w:430, accent:C.green, glow:true,
        title:'CEO Recommendation · Run a gated beta' },
        [['Outcome','Core flows validated across 100 agents'],
         ['Critical risk','3 tenant-isolation gaps under review'],
         ['Recommendation','Ship to 5% behind a feature gate'],
         ['Your call','Approve the gated rollout']].map(([k,v],i)=>
          React.createElement('div',{ key:i, style:{ display:'flex', gap:10, padding:'3px 0',
            opacity:appear(t,6.5+i*0.18,0.4) } },
            React.createElement('span',{ style:{ flex:'0 0 96px', fontSize:10, fontWeight:700,
              letterSpacing:'.04em', textTransform:'uppercase', color:C.muted, paddingTop:1 } }, k),
            React.createElement('span',{ style:{ fontSize:12.5, color:C.ink, lineHeight:1.4 } }, v)))),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.9, text:'Hundreds of agent results across every department' },
        { s:2.1, e:3.6, text:'Each department rolls its work into a summary' },
        { s:4.4, e:5.9, text:'The CEO compresses everything into one report' },
        { s:6.2, e:8.6, text:'Summary, risks, and a recommendation \u2014 for one decision' },
        { s:8.9, e:10.5, text:'A whole org, distilled to one human-ready report.', final:true },
      ] })
    );
  }

  K.makeAnim('ReportingAnim', Scene, 10.5);
})();
