// Scene 6 — Sandboxed Tool Execution.
// An agent's tool call runs inside an isolated sandbox with resource caps.
// Allowed operations execute; a disallowed one is denied at the boundary;
// the result returns to the agent.
(function () {
  const K = window.WFKit;
  const { C, MONO, SANS, useTime, appear, clamp01, lerp, eio, eob, eoc, hexA,
    Root, AgentNode, Pill, Captions, flight, centered } = K;

  const agent = { x:210, y:360 };
  const box = { x:540, y:150, w:660, h:430 }; // sandbox top-left + size
  const port = { x:box.x, y:360 }; // entry point on sandbox wall

  function meter(label, cap, val, t, accent){
    const v = clamp01(val);
    return React.createElement('div',{ style:{ marginBottom:9 } },
      React.createElement('div',{ style:{ display:'flex', justifyContent:'space-between',
        fontSize:10, fontFamily:MONO, color:C.muted, marginBottom:3, letterSpacing:'.03em' } },
        React.createElement('span',null, label),
        React.createElement('span',null, cap)),
      React.createElement('div',{ style:{ height:6, borderRadius:3, background:'#eceae5', overflow:'hidden' } },
        React.createElement('div',{ style:{ height:'100%', width:(v*100)+'%', background:accent,
          borderRadius:3, transition:'none' } })));
  }

  function Scene(){
    const t = useTime();
    const callIn = flight(t, {x:agent.x+50,y:agent.y}, port, { start:1.6, travel:1.0, hold:0.2 });
    const running = t>2.8;
    const denyFlash = t>4.8 && t<6.2 ? Math.max(0, Math.sin((t-4.8)*6))*clamp01((6.2-t)/0.4) : 0;
    const result = flight(t, port, {x:agent.x+50,y:agent.y}, { start:6.4, travel:1.0, hold:0.3 });
    const boxOp = appear(t,0.6,0.5);
    const cx = box.x + box.w/2;

    const logs = [
      { s:3.0, txt:'launch headless browser', ok:true },
      { s:3.5, txt:'GET app.openforge.dev/login', ok:true },
      { s:4.0, txt:'assert dashboard renders', ok:true },
      { s:4.9, txt:'write /etc/hosts', ok:false },
      { s:6.4, txt:'return test report', ok:true },
    ];

    return React.createElement(Root, { t, chip:{ state:'Sandboxed execution', color:C.violet },
        title:{ kicker:'Capability', title:'Sandboxed tool execution' } },
      // agent
      React.createElement(AgentNode,{ cx:agent.x, cy:agent.y, op:appear(t,0.2,0.5),
        name:'QA Agent', sub:t>6.6?'result received':'awaiting result', icon:0,
        status:t>7.0?'completed':(running?'running':'working'), glow:running && t<6.6 }),
      // sandbox container
      React.createElement('div',{ style:{ position:'absolute', left:box.x, top:box.y, width:box.w,
        height:box.h, borderRadius:18, background:hexA(C.violet,0.04),
        border:`2px dashed ${hexA(C.violet, denyFlash>0?0.9:0.5)}`,
        boxShadow:`0 0 ${20+denyFlash*30}px ${hexA(C.red,denyFlash*0.5)}`, opacity:boxOp } }),
      // sandbox header
      React.createElement('div',{ style:{ position:'absolute', left:box.x+22, top:box.y+18, opacity:boxOp,
        display:'flex', alignItems:'center', gap:9, fontFamily:SANS } },
        React.createElement('svg',{ width:18, height:18, viewBox:'0 0 16 16', fill:'none' },
          React.createElement('path',{ d:'M8 1.5 13.5 4v4.5c0 3.4-2.4 5.4-5.5 6.5C4.9 13.9 2.5 11.9 2.5 8.5V4z',
            stroke:C.violet, strokeWidth:1.4, strokeLinejoin:'round' })),
        React.createElement('span',{ style:{ fontSize:14, fontWeight:680, color:C.ink } }, 'Sandbox'),
        React.createElement(Pill,{ kind:'sandboxed', label:'isolated' })),
      // resource meters
      React.createElement('div',{ style:{ position:'absolute', left:box.x+box.w-184, top:box.y+52,
        width:160, opacity:boxOp } },
        meter('CPU', '1 vCPU', running?Math.min(0.62, (t-2.8)*0.5):0, t, C.violet),
        meter('MEMORY', '512 MB', running?Math.min(0.48,(t-2.8)*0.4):0, t, C.violet),
        meter('NETWORK', 'allowlist', running?Math.min(0.7,(t-2.8)*0.55):0, t, C.blue)),
      // execution log
      React.createElement('div',{ style:{ position:'absolute', left:box.x+22, top:box.y+86,
        width:box.w-220, opacity:boxOp, fontFamily:MONO, fontSize:11.5, lineHeight:1.7 } },
        logs.map((l,i)=>{ const op=appear(t,l.s,0.35); if(op<=0.02) return null;
          const col = l.ok?C.green:C.red;
          return React.createElement('div',{ key:i, style:{ display:'flex', alignItems:'center', gap:8,
            opacity:op, color:l.ok?'#46443e':C.red } },
            React.createElement('span',{ style:{ color:col, width:14 } }, l.ok?'✓':'✕'),
            React.createElement('span',null, l.txt),
            !l.ok && React.createElement('span',{ style:{ marginLeft:6, color:C.red, fontWeight:700,
              fontSize:9.5, letterSpacing:'.06em' } }, 'DENIED · OUT OF SANDBOX')); })),
      // tool call chip entering
      callIn && React.createElement('div',{ style:{ ...centered(callIn.x,callIn.y,1,callIn.op),
        display:'flex', alignItems:'center', gap:8, padding:'8px 13px', borderRadius:9, background:C.cardBg,
        border:`1px solid ${C.cardBorder}`, boxShadow:'0 3px 12px rgba(28,27,25,.14)',
        fontFamily:SANS, whiteSpace:'nowrap' } },
        React.createElement('span',{ style:{ width:8, height:8, borderRadius:2, background:C.violet } }),
        React.createElement('span',{ style:{ fontSize:12, fontWeight:600, color:C.ink } }, 'call · Browser Automation')),
      // result chip returning
      result && React.createElement('div',{ style:{ ...centered(result.x,result.y,1,result.op),
        display:'flex', alignItems:'center', gap:8, padding:'8px 13px', borderRadius:9, background:C.cardBg,
        border:`1px solid ${C.cardBorder}`, boxShadow:'0 3px 12px rgba(28,27,25,.14)',
        fontFamily:SANS, whiteSpace:'nowrap' } },
        React.createElement('span',{ style:{ width:8, height:8, borderRadius:2, background:C.green } }),
        React.createElement('span',{ style:{ fontSize:12, fontWeight:600, color:C.ink } }, 'result · tests passed')),
      React.createElement(Captions,{ t, items:[
        { s:0.4, e:1.6, text:'An agent calls a tool' },
        { s:1.8, e:2.9, text:'Execution happens inside an isolated sandbox' },
        { s:3.1, e:4.7, text:'Allowed operations run under strict resource caps' },
        { s:4.9, e:6.2, text:'Anything outside the boundary is denied' },
        { s:6.4, e:7.6, text:'Only the result crosses back to the agent' },
        { s:7.9, e:9.2, text:'Powerful tools, safely contained.', final:true },
      ] })
    );
  }

  K.makeAnim('SandboxAnim', Scene, 9.4);
})();
