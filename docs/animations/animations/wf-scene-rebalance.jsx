// Scene 5 — Manager Reviews & Rebalances.
// A manager watches its agents' queues. One overloads; the manager detects it
// and moves queued work to an idle agent, restoring balance.
(function () {
  const K = window.WFKit;
  const { C, MONO, SANS, useTime, appear, clamp01, lerp, eio, eob, eoc, hexA,
    Root, Wires, NodeBox, AgentNode, Pill, Captions, flight, centered } = K;

  const mgr = { x:640, y:132 };
  const AG = [
    { x:255, y:430, name:'Search Agent', icon:1 },
    { x:510, y:430, name:'Data Agent', icon:0 },
    { x:770, y:430, name:'Doc Agent', icon:1 },
    { x:1025, y:430, name:'Idle Agent', icon:0 },
  ];

  // queue count per agent over time → drives the story
  function depth(i, t){
    if (i===1){ // overloads then drains
      if (t<2.2) return 2 + Math.floor((t-0.6)*1.2);
      if (t<4.6) return Math.min(7, 2 + Math.floor((t-0.6)*1.4));
      return Math.max(3, 7 - Math.floor((t-4.6)*2.2)); // drained by rebalance
    }
    if (i===3){ // idle, then receives
      if (t<5.0) return 0;
      return Math.min(3, Math.floor((t-5.0)*2.0));
    }
    return [2,0,2,1][i] || 2;
  }
  function statusFor(i, t){
    if (i===1){ if (t>=2.6 && t<5.2) return 'overloaded'; if (t>=5.2) return 'rebalanced'; return 'working'; }
    if (i===3){ if (t<5.0) return 'idle'; return 'working'; }
    return 'working';
  }

  function Queue({ x, y, n, accent, t, appearAt }){
    const op = appear(t,appearAt,0.5);
    const bars = [];
    for (let k=0;k<n;k++) bars.push(k);
    return React.createElement('div',{ style:{ position:'absolute', left:x-30, top:y, width:60,
      display:'flex', flexDirection:'column-reverse', gap:3, alignItems:'center', opacity:op } },
      bars.map(k=>React.createElement('div',{ key:k, style:{ width:54, height:7, borderRadius:2,
        background:hexA(accent, k>=5?0.95:0.55+k*0.06), border:`1px solid ${hexA(accent,0.5)}` } })));
  }

  function Scene(){
    const t = useTime();
    const wires = AG.map((a,i)=>({ a:mgr, b:{x:a.x,y:a.y}, app:1.3+i*0.08 }));
    const detect = t>2.8 && t<5.4;

    return React.createElement(Root, { t, chip:{ state:'Load balancing' },
        title:{ kicker:'Capability', title:'Manager reviews & rebalances' } },
      React.createElement(Wires,{ t, links:wires }),
      React.createElement(NodeBox,{ cx:mgr.x, cy:mgr.y, op:appear(t,0,0.5),
        s:lerp(0.85,1,eob(appear(t,0,0.5))), title:'Engineering Manager', sub:'OVERSEES 4 AGENTS',
        w:200, accent:C.gold, glow:detect }),
      // manager "review" badge
      detect && React.createElement('div',{ style:{ ...centered(mgr.x, mgr.y+52, 1,
        Math.min(appear(t,2.9,0.3),clamp01((5.4-t)/0.3))) } },
        React.createElement(Pill,{ kind:'overloaded', label:'rebalancing queues' })),
      AG.map((a,i)=>{ const n=Math.max(0,depth(i,t)); const st=statusFor(i,t);
        const col = st==='overloaded'?C.red : st==='rebalanced'?C.green : st==='idle'?C.muted:C.gold;
        return React.createElement('div',{ key:i },
          React.createElement(Queue,{ x:a.x, y:a.y+62, n, accent:col, t, appearAt:1.3+i*0.08 }),
          React.createElement(AgentNode,{ cx:a.x, cy:a.y, op:appear(t,1.3+i*0.08,0.5),
            name:a.name, icon:a.icon, status:st, glow:st==='overloaded'||st==='rebalanced' }),
          // count badge
          n>0 && React.createElement('div',{ style:{ ...centered(a.x+36, a.y-22, 1, appear(t,1.5,0.4)),
            minWidth:20, padding:'1px 6px', borderRadius:999, background:col, color:'#fff',
            fontFamily:MONO, fontSize:10.5, fontWeight:700, textAlign:'center' } }, n)); }),
      // tasks flying from overloaded (idx1) to idle (idx3)
      [0,1,2].map(k=>{ const f=flight(t,{x:AG[1].x,y:AG[1].y+30},{x:AG[3].x,y:AG[3].y+30},
          {start:5.0+k*0.35,travel:0.9,hold:0.1});
        if(!f) return null;
        return React.createElement('div',{ key:k, style:{ ...centered(f.x,f.y,1,f.op), display:'flex',
          alignItems:'center', gap:7, padding:'6px 11px', borderRadius:8, background:C.cardBg,
          border:`1px solid ${C.cardBorder}`, boxShadow:'0 3px 12px rgba(28,27,25,.14)',
          fontFamily:SANS, whiteSpace:'nowrap' } },
          React.createElement('span',{ style:{ width:7, height:7, borderRadius:2, background:C.green } }),
          React.createElement('span',{ style:{ fontSize:11.5, fontWeight:600, color:C.ink } }, 'reassigned task')); }),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.4, text:'A manager oversees its team of agents' },
        { s:1.6, e:2.8, text:'Work flows in \u2014 queues stay healthy' },
        { s:3.0, e:4.8, text:'One agent overloads while another sits idle' },
        { s:5.0, e:7.0, text:'The manager moves queued work to free capacity' },
        { s:7.2, e:8.4, text:'Balance restored \u2014 no human in the loop' },
        { s:8.6, e:9.6, text:'Managers keep the org load-balanced.', final:true },
      ] })
    );
  }

  K.makeAnim('RebalanceAnim', Scene, 9.6);
})();
