// Scene 1 — Hierarchical Task Delegation.
// One goal enters at the CEO, fans down to the C-suite, then to managers, then
// to agents. Gold task chips fly down each tier as the work decomposes.
(function () {
  const K = window.WFKit;
  const { C, useTime, appear, clamp01, lerp, eob, Root, Wires, FlowDots, NodeBox, AgentNode, TaskChip, Captions, flight } = K;

  const P = {
    ceo:{x:640,y:120},
    cto:{x:330,y:262}, cro:{x:640,y:262}, cpo:{x:950,y:262},
    mBack:{x:200,y:412}, mFront:{x:380,y:412}, mDep:{x:540,y:412}, mSec:{x:700,y:412}, mLaunch:{x:990,y:412},
    aBack:{x:200,y:566}, aFront:{x:380,y:566}, aDep:{x:540,y:566}, aSec:{x:700,y:566}, aLaunch:{x:990,y:566},
  };

  function Scene(){
    const t = useTime();
    const wires = [
      { a:P.ceo, b:P.cto, app:1.5 }, { a:P.ceo, b:P.cro, app:1.6 }, { a:P.ceo, b:P.cpo, app:1.7 },
      { a:P.cto, b:P.mBack, app:3.5 }, { a:P.cto, b:P.mFront, app:3.6 }, { a:P.cto, b:P.mDep, app:3.7 },
      { a:P.cro, b:P.mSec, app:3.6 }, { a:P.cpo, b:P.mLaunch, app:3.7 },
      { a:P.mBack, b:P.aBack, app:5.4 }, { a:P.mFront, b:P.aFront, app:5.5 }, { a:P.mDep, b:P.aDep, app:5.6 },
      { a:P.mSec, b:P.aSec, app:5.5 }, { a:P.mLaunch, b:P.aLaunch, app:5.6 },
    ];
    const deptF = [
      { from:P.ceo, to:P.cto, label:'Build the product', start:1.8, travel:1.1, hold:0.5 },
      { from:P.ceo, to:P.cro, label:'Validate security', start:2.0, travel:1.1, hold:0.5, accent:C.red },
      { from:P.ceo, to:P.cpo, label:'Prepare the launch', start:2.2, travel:1.1, hold:0.5 },
    ];
    const subF = [
      { from:P.cto, to:P.mBack, label:'Implement backend', start:3.8, travel:0.9, hold:0.4 },
      { from:P.cto, to:P.mFront, label:'Build frontend', start:4.0, travel:0.9, hold:0.4 },
      { from:P.cto, to:P.mDep, label:'Set up deploy', start:4.2, travel:0.9, hold:0.4 },
      { from:P.cro, to:P.mSec, label:'Pen-test & audit', start:4.0, travel:0.9, hold:0.4, accent:C.red },
      { from:P.cpo, to:P.mLaunch, label:'GTM rollout', start:4.2, travel:0.9, hold:0.4 },
    ];
    const agentF = [
      { p:P.aBack, name:'Claude Code', icon:0, start:6.0 },
      { p:P.aFront, name:'UI Agent', icon:1, start:6.15 },
      { p:P.aDep, name:'Release Agent', icon:1, start:6.3 },
      { p:P.aSec, name:'Codex', icon:0, start:6.15 },
      { p:P.aLaunch, name:'Launch Agent', icon:1, start:6.3 },
    ];
    const flows = [...deptF,...subF];
    const goalOp = appear(t,0.3,0.5) * clamp01((9.6-t)/0.6+1);

    return React.createElement(Root, { t, chip:{ state:'Delegating' },
        title:{ kicker:'Capability', title:'Hierarchical task delegation' } },
      React.createElement(Wires,{ t, links:wires }),
      React.createElement(FlowDots,{ t, links:[
        ...wires.slice(0,3).map(w=>({...w, from:2.6, to:4.0, color:C.gold, r:3.4})),
        ...wires.slice(3,8).map(w=>({...w, from:4.6, to:6.0, color:C.gold, r:3})),
      ] }),
      React.createElement(TaskChip,{ cx:640, cy:54, op:goalOp, label:'Launch the public beta', accent:C.gold, w:220 }),
      React.createElement(NodeBox,{ cx:P.ceo.x, cy:P.ceo.y, op:appear(t,0,0.5),
        s:lerp(0.85,1,eob(appear(t,0,0.5))), title:'CEO', sub:'CHIEF AGENT', kind:'ceo', w:92 }),
      React.createElement(NodeBox,{ cx:P.cto.x, cy:P.cto.y, op:appear(t,1.5,0.5), title:'Engineering', sub:'CTO' }),
      React.createElement(NodeBox,{ cx:P.cro.x, cy:P.cro.y, op:appear(t,1.6,0.5), title:'Risk', sub:'CRO', accent:C.red }),
      React.createElement(NodeBox,{ cx:P.cpo.x, cy:P.cpo.y, op:appear(t,1.7,0.5), title:'Product', sub:'CPO' }),
      [['mBack','Backend'],['mFront','Frontend'],['mDep','Deploy'],['mSec','Security'],['mLaunch','Launch']].map(([k,l],i)=>
        React.createElement(NodeBox,{ key:k, cx:P[k].x, cy:P[k].y, op:appear(t,3.5+i*0.05,0.5),
          title:l, sub:'MANAGER', w:108, accent:k==='mSec'?C.red:C.gold })),
      agentF.map((a,i)=>{ const working = t > a.start + 1.0;
        return React.createElement(AgentNode,{ key:i, cx:a.p.x, cy:a.p.y, op:appear(t,a.start-0.6,0.5),
          name:a.name, icon:a.icon, status:working?'working':'idle', glow:working }); }),
      flows.map((f,i)=>{ const r=flight(t,f.from,f.to,f); if(!r) return null;
        return React.createElement(TaskChip,{ key:i, cx:r.x, cy:r.y, op:r.op, label:f.label, accent:f.accent||C.gold }); }),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.6, text:'One high-level goal enters at the top' },
        { s:1.9, e:3.5, text:'The CEO delegates across the C-suite' },
        { s:3.9, e:5.7, text:'Each executive breaks the work down' },
        { s:6.0, e:8.4, text:'Managers hand tasks to the right agents' },
        { s:8.7, e:10, text:'One goal, distributed through the org.', final:true },
      ] })
    );
  }

  K.makeAnim('DelegationAnim', Scene, 10);
})();
