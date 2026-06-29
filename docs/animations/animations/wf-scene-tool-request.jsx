// Scene 2 — Agent Requests a New Tool.
// An agent is blocked by a missing capability, files a tool request that's
// approved up the chain, the tool appears in its toolkit, and it unblocks.
(function () {
  const K = window.WFKit;
  const { C, MONO, SANS, useTime, appear, clamp01, eoc, eob, lerp, hexA,
    Root, Wires, NodeBox, AgentNode, Card, TaskChip, Pill, Captions, flight, centered } = K;

  const P = { human:{x:300,y:108}, vp:{x:300,y:300}, agent:{x:300,y:548} };

  function Scene(){
    const t = useTime();
    const wires = [ { a:P.human, b:P.vp, app:0.2, op:0.5 }, { a:P.vp, b:P.agent, app:0.2, op:0.5 } ];
    const status = t<1.1?'working' : t<6.6?'blocked' : t<7.4?'running':'completed';
    const rq1 = flight(t, {x:430,y:470}, P.vp, { start:3.2, travel:1.0, hold:0.2 });
    const rq2 = flight(t, P.vp, P.human, { start:4.3, travel:0.9, hold:0.3 });
    const approveVP = t>4.3 && t<5.5;
    const approveHu = t>5.2 && t<6.6;
    const toolIn = appear(t,5.6,0.6);

    return React.createElement(Root, { t, chip:{ state:'Self-service tools' },
        title:{ kicker:'Capability', title:'Requesting a new tool' } },
      React.createElement(Wires,{ t, links:wires }),
      React.createElement(NodeBox,{ cx:P.human.x, cy:P.human.y, op:appear(t,0.2,0.5),
        title:'Human', sub:'FINAL APPROVER', kind:'human', glow:approveHu, accent:C.green, w:150 }),
      React.createElement(NodeBox,{ cx:P.vp.x, cy:P.vp.y, op:appear(t,0.2,0.5),
        title:'VP Engineering', sub:'REVIEW', w:150, glow:approveVP, accent:C.green }),
      React.createElement(AgentNode,{ cx:P.agent.x, cy:P.agent.y, op:appear(t,0.2,0.5),
        name:'QA Agent', sub:'Task · run browser tests', icon:0, status,
        glow:status==='working'||status==='running' }),
      // blocked banner
      (t>1.1 && t<3.4) && React.createElement(Card,{ cx:300, cy:418,
        op:Math.min(appear(t,1.2,0.4),clamp01((3.6-t)/0.4)), w:236, accent:C.red, title:'Blocked' },
        React.createElement('div',{ style:{ fontSize:12, color:C.ink, lineHeight:1.45 } },
          'Browser-automation tool not available in toolkit')),
      // request card near agent then travels
      (t>=1.7 && t<3.2) && React.createElement(Card,{ cx:560, cy:430,
        op:Math.min(appear(t,1.8,0.4),clamp01((3.3-t)/0.3)), w:248, accent:C.gold, title:'Tool Request' },
        React.createElement('div',{ style:{ fontSize:11.5, color:C.ink, lineHeight:1.8 } },
          React.createElement('div',null, React.createElement('b',{ style:{color:C.sub,fontWeight:600} },'Tool'), '  Browser Automation'),
          React.createElement('div',null, React.createElement('b',{ style:{color:C.sub,fontWeight:600} },'Reason'), '  End-to-end validation'),
          React.createElement('div',null, React.createElement('b',{ style:{color:C.sub,fontWeight:600} },'Scope'), '  Read-only, this task'))),
      rq1 && React.createElement(TaskChip,{ cx:rq1.x, cy:rq1.y, op:rq1.op, label:'Request · Browser Automation', accent:C.gold }),
      rq2 && React.createElement(TaskChip,{ cx:rq2.x, cy:rq2.y, op:rq2.op, label:'Request · Browser Automation', accent:C.gold }),
      // toolkit panel
      React.createElement(Card,{ cx:910, cy:392, op:appear(t,2.3,0.5), w:268, accent:C.blue, title:'QA Agent · Toolkit' },
        ['Shell','HTTP fetch','Unit runner'].map((tn,i)=>
          React.createElement('div',{ key:i, style:{ display:'flex', alignItems:'center', gap:8,
            padding:'5px 0', fontSize:12.5, color:C.ink } },
            React.createElement('span',{ style:{ color:C.green, fontFamily:MONO, fontSize:12 } },'✓'), tn)),
        React.createElement('div',{ style:{ display:'flex', alignItems:'center', gap:8, padding:'7px 0 1px',
          fontSize:12.5, color:C.ink, opacity:toolIn, marginTop:4, borderTop:`1px solid ${C.cardBorder}`,
          transform:`translateX(${(1-eoc(toolIn))*-12}px)` } },
          React.createElement('span',{ style:{ color:C.green, fontFamily:MONO, fontSize:12 } },'✓'),
          React.createElement('b',{ style:{ fontWeight:680 } },'Browser Automation'),
          React.createElement(Pill,{ kind:'approved', label:'granted', style:{ marginLeft:'auto' } }))),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.1, text:'An agent works on its task' },
        { s:1.3, e:3.2, text:'It hits a missing capability and files a request' },
        { s:3.4, e:5.2, text:'Reviewed and approved up the chain' },
        { s:5.4, e:6.6, text:'The tool appears in the agent\u2019s toolkit' },
        { s:6.8, e:8.2, text:'Blocked \u2192 Running \u2192 Completed' },
        { s:8.5, e:10, text:'Agents request new capabilities as they work.', final:true },
      ] })
    );
  }

  K.makeAnim('ToolRequestAnim', Scene, 10);
})();
