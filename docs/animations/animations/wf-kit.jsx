// Workforce Runtime — shared animation kit.
// Primitives + visual language shared by every capability scene. Reads the
// timeline globals (Stage, useTime, Easing…) off window — load AFTER animations.jsx.
(function () {
  const { Stage, useTime, Easing } = window;

  const W = 1280, H = 720;
  const SANS = '-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif';
  const MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace';

  const C = {
    bg:'#f4f3f0', ink:'#1c1b19', dark:'#262420', darker:'#201f1c',
    cardText:'#f3f1ec', sub:'#8f8b83', muted:'#a39e95',
    line:'#d0cbc2', cardBorder:'#e0dcd4', cardBg:'#ffffff', panel:'#faf9f7',
    gold:'#b07d2f', green:'#3f7d57', blue:'#5d93b3', red:'#b3524b', violet:'#7d6cae',
    goldText:'#7a5a1f',
  };
  const STATUS = {
    idle:C.muted, working:C.gold, blocked:C.red, running:C.blue, completed:C.green,
    approved:C.green, overloaded:C.red, active:C.blue, queued:C.muted, executing:C.violet,
    done:C.green, waiting:C.gold, rebalanced:C.green, sandboxed:C.violet, healthy:C.green,
  };

  const lerp = (a,b,t)=>a+(b-a)*t;
  const clamp01 = x=>x<0?0:x>1?1:x;
  const seg = (t,s,e)=> e<=s?(t>=e?1:0):clamp01((t-s)/(e-s));
  const appear = (t,s,d=0.5)=>clamp01((t-s)/d);
  const eio = Easing.easeInOutCubic, eob = Easing.easeOutBack, eoc = Easing.easeOutCubic;
  function hexA(h,a){const n=parseInt(h.slice(1),16);return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;}

  const ICON = [
    (window.__resources && window.__resources.codexIcon) || 'uploads/codex-rounded2.png',
    (window.__resources && window.__resources.electronIcon) || 'uploads/electron-256.png',
  ];

  const centered = (cx,cy,s,op)=>({ position:'absolute', left:cx, top:cy,
    transform:`translate(-50%,-50%) scale(${s})`, opacity:op });

  function Pill({ label, kind='idle', color, style }){
    const col = color || STATUS[kind] || C.muted;
    return React.createElement('span', { style:{ display:'inline-flex', alignItems:'center', gap:5,
      padding:'2px 8px', borderRadius:999, background:hexA(col,0.13), color:col, fontFamily:MONO,
      fontSize:9.5, fontWeight:700, letterSpacing:'0.06em', textTransform:'uppercase', whiteSpace:'nowrap', ...style } },
      React.createElement('span',{ style:{ width:5, height:5, borderRadius:'50%', background:col } }), label);
  }

  function NodeBox({ cx, cy, op=1, s=1, title, sub, accent=C.gold, kind='dark', w, glow }){
    const light = kind==='light'||kind==='human';
    const bg = kind==='ceo'?C.darker : kind==='human'?C.cardBg : light?C.cardBg : C.dark;
    const txt = light?C.ink:C.cardText;
    const subc = light?C.muted:accent;
    return React.createElement('div', { style:{ ...centered(cx,cy,s,op), minWidth:w||130,
      padding:'9px 16px', borderRadius:11, background:bg, textAlign:'center',
      border:light?`1px solid ${C.cardBorder}`:'none',
      boxShadow:glow?`0 0 0 5px ${hexA(accent,0.16)}`:'0 1px 2px rgba(28,27,25,.05)' } },
      sub && React.createElement('div',{ style:{ fontSize:8, fontWeight:700, letterSpacing:'0.14em',
        color:subc, marginBottom:3, fontFamily:SANS } }, sub),
      React.createElement('div',{ style:{ fontSize:13.5, fontWeight:660, color:txt,
        letterSpacing:'-0.01em', fontFamily:SANS } }, title));
  }

  function Card({ cx, cy, op=1, s=1, w=200, accent=C.gold, title, kind='light', children, pad='12px 14px', glow }){
    const bg = kind==='dark'?C.dark:C.cardBg;
    return React.createElement('div', { style:{ ...centered(cx,cy,s,op), width:w, padding:pad,
      borderRadius:12, background:bg, border:kind==='dark'?'none':`1px solid ${C.cardBorder}`,
      textAlign:'left', fontFamily:SANS,
      boxShadow:glow?`0 8px 26px ${hexA(accent,0.20)}`:'0 4px 16px rgba(28,27,25,.08)' } },
      title && React.createElement('div',{ style:{ display:'flex', alignItems:'center', gap:7, marginBottom:8 } },
        React.createElement('span',{ style:{ width:7, height:7, borderRadius:2, background:accent } }),
        React.createElement('span',{ style:{ fontSize:12.5, fontWeight:700,
          color:kind==='dark'?C.cardText:C.ink, letterSpacing:'-0.01em' } }, title)),
      children);
  }

  function TaskChip({ cx, cy, op=1, s=1, label, accent=C.gold, w }){
    return React.createElement('div', { style:{ ...centered(cx,cy,s,op), display:'flex',
      alignItems:'center', gap:8, width:w, padding:'8px 13px', borderRadius:9, background:C.cardBg,
      border:`1px solid ${C.cardBorder}`, boxShadow:'0 3px 12px rgba(28,27,25,.12)',
      fontFamily:SANS, whiteSpace:'nowrap' } },
      React.createElement('span',{ style:{ width:8, height:8, borderRadius:2, background:accent, flexShrink:0 } }),
      React.createElement('span',{ style:{ fontSize:12.5, fontWeight:600, color:C.ink, letterSpacing:'-0.01em' } }, label));
  }

  function AgentNode({ cx, cy, op=1, s=1, name, icon=0, status='idle', size=46, glow, sub }){
    const col = STATUS[status] || C.muted;
    return React.createElement('div', { style:{ ...centered(cx,cy,s,op), display:'flex',
      flexDirection:'column', alignItems:'center', gap:5, width:130 } },
      React.createElement('img',{ src:ICON[icon], draggable:false, style:{ width:size, height:size,
        borderRadius:size*0.24, boxShadow:glow?`0 0 0 5px ${hexA(col,0.20)}`:'0 2px 6px rgba(28,27,25,.12)' } }),
      name && React.createElement('div',{ style:{ fontSize:11, fontWeight:640, color:C.ink,
        fontFamily:SANS, whiteSpace:'nowrap' } }, name),
      sub && React.createElement('div',{ style:{ fontSize:9, fontWeight:600, color:C.muted,
        fontFamily:MONO, letterSpacing:'0.04em', whiteSpace:'nowrap' } }, sub),
      status && React.createElement(Pill,{ kind:status, label:status }));
  }

  const elbow = (a,b,p)=>{ const ex=lerp(a.x,b.x,p), ey=lerp(a.y,b.y,p), my=(a.y+b.y)/2;
    return `M ${a.x} ${a.y} C ${a.x} ${my}, ${ex} ${my}, ${ex} ${ey}`; };

  function Wires({ t, links }){
    return React.createElement('svg',{ width:W, height:H, viewBox:`0 0 ${W} ${H}`,
      style:{ position:'absolute', inset:0, pointerEvents:'none' } },
      links.map((l,i)=>{ const d=clamp01((t-(l.app||0))/0.6); if(d<=0.02) return null;
        return React.createElement('path',{ key:i, d:elbow(l.a,l.b,d), fill:'none',
          stroke:l.stroke||C.line, strokeWidth:l.w||1.6, strokeLinecap:'round',
          opacity:l.op!=null?l.op:0.95 }); }));
  }

  function bz(a,b,u){ const my=(a.y+b.y)/2, v=1-u;
    return { x:v*v*v*a.x+3*v*v*u*a.x+3*v*u*u*b.x+u*u*u*b.x,
             y:v*v*v*a.y+3*v*v*u*a.y+3*v*u*u*b.y+u*u*u*b.y }; }

  // pulses travelling along elbow wires (down=assign gold / up=report green)
  function FlowDots({ t, links }){
    return React.createElement('svg',{ width:W, height:H, viewBox:`0 0 ${W} ${H}`,
      style:{ position:'absolute', inset:0, pointerEvents:'none' } },
      links.map((l,i)=>{ if(t<(l.from||0)||t>(l.to!=null?l.to:1e9)) return null;
        const u=((t*(l.speed||0.5)+i*0.21)%1); const p=bz(l.a,l.b,u);
        return React.createElement('circle',{ key:i, cx:p.x, cy:p.y, r:l.r||4,
          fill:l.color||C.gold, opacity:(l.op||0.9) }); }));
  }

  function flight(t, from, to, o){
    const end = o.start + o.travel + (o.hold||0);
    if (t < o.start || t > end + 0.45) return null;
    const p = eio(clamp01((t-o.start)/o.travel));
    return { x:lerp(from.x,to.x,p), y:lerp(from.y,to.y,p), p,
      op: Math.min(clamp01((t-o.start)/0.25), clamp01((end-t)/0.32)) };
  }

  function ProductChip({ t, label='Workforce Runtime', state='Operating', color=C.green, appearAt=0.2 }){
    const op = appear(t,appearAt,0.6);
    return React.createElement('div',{ style:{ position:'absolute', left:26, top:24, display:'flex',
      alignItems:'center', gap:9, background:C.cardBg, border:'1px solid #e6e3de', borderRadius:11,
      padding:'8px 14px', boxShadow:'0 1px 2px rgba(28,27,25,.04)', opacity:op, fontFamily:SANS } },
      React.createElement('span',{ style:{ width:8, height:8, borderRadius:'50%', background:color,
        boxShadow:`0 0 0 4px ${hexA(color,0.16)}` } }),
      React.createElement('span',{ style:{ fontSize:13, fontWeight:650, color:C.ink } }, label),
      React.createElement('span',{ style:{ width:3, height:3, borderRadius:'50%', background:'#cfcabf' } }),
      React.createElement('span',{ style:{ fontSize:11, letterSpacing:'.05em', textTransform:'uppercase', color:C.muted } }, state));
  }

  function SceneTitle({ t, kicker, title, appearAt=0.1 }){
    const op = Math.min(appear(t,appearAt,0.5), 1);
    return React.createElement('div',{ style:{ position:'absolute', right:30, top:26, textAlign:'right',
      opacity:op, fontFamily:SANS, transform:`translateY(${(1-eoc(op))*-6}px)` } },
      React.createElement('div',{ style:{ fontSize:10, fontWeight:700, letterSpacing:'0.14em',
        textTransform:'uppercase', color:C.muted, marginBottom:3 } }, kicker),
      React.createElement('div',{ style:{ fontSize:15, fontWeight:680, color:C.ink, letterSpacing:'-0.01em' } }, title));
  }

  function Captions({ t, items }){
    return items.map((c,i)=>{
      const o = Math.min(clamp01((t-c.s)/0.4), clamp01((c.e-t)/0.4));
      if (o<=0.01) return null;
      if (c.final) return React.createElement('div',{ key:i, style:{ position:'absolute', left:'50%',
        bottom:54, transform:`translateX(-50%) translateY(${(1-eoc(clamp01((t-c.s)/0.5)))*10}px)`, opacity:o,
        display:'flex', alignItems:'center', gap:11, whiteSpace:'nowrap', fontFamily:SANS } },
        React.createElement('span',{ style:{ width:7, height:7, borderRadius:'50%', background:C.gold } }),
        React.createElement('span',{ style:{ fontSize:21, fontWeight:660, color:C.ink, letterSpacing:'-0.015em' } }, c.text));
      return React.createElement('div',{ key:i, style:{ position:'absolute', left:'50%', bottom:58,
        transform:'translateX(-50%)', opacity:o, display:'flex', alignItems:'center', gap:10,
        background:'rgba(255,255,255,.9)', backdropFilter:'blur(6px)', border:'1px solid #e6e3de',
        borderRadius:11, padding:'9px 18px', boxShadow:'0 4px 18px rgba(28,27,25,.08)',
        whiteSpace:'nowrap', fontFamily:SANS } },
        React.createElement('span',{ style:{ width:6, height:6, borderRadius:'50%', background:C.gold } }),
        React.createElement('span',{ style:{ fontSize:15.5, fontWeight:600, color:C.ink, letterSpacing:'-.01em' } }, c.text));
    });
  }

  function Root({ t, chip, title, children }){
    return React.createElement('div',{ style:{ position:'absolute', inset:0, background:C.bg,
      fontFamily:SANS, overflow:'hidden' } },
      children,
      React.createElement(ProductChip,{ t, ...(chip||{}) }),
      title && React.createElement(SceneTitle,{ t, ...title }));
  }

  // Wrap a scene component into a window-registered standalone <Stage> animation.
  function makeAnim(name, SceneComp, duration){
    function Anim(){
      return React.createElement('div',{ style:{ position:'fixed', inset:0, background:'#0a0a0a' } },
        React.createElement(Stage,{ width:W, height:H, duration, background:C.bg, persistKey:'wf-'+name },
          React.createElement(SceneComp,null)));
    }
    window[name] = Anim;
    return Anim;
  }

  window.WFKit = {
    W, H, SANS, MONO, C, STATUS, ICON,
    lerp, clamp01, seg, appear, eio, eob, eoc, hexA, centered,
    Pill, NodeBox, Card, TaskChip, AgentNode, Wires, FlowDots, flight,
    ProductChip, SceneTitle, Captions, Root, makeAnim, useTime,
  };
})();
