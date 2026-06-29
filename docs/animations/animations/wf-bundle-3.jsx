// @ds-adherence-ignore -- omelette starter scaffold (raw elements/hex/px by design)

/* BEGIN USAGE */
// animations.jsx — timeline engine. Exports (on window): Stage, Sprite,
//   TextSprite, ImageSprite, RectSprite, VideoSprite, PlaybackBar,
//   useTime, useTimeline, useSprite, Easing, interpolate, animate, clamp.
//
//   <Stage width={1280} height={720} duration={10} background="#f6f4ef">
//     <Sprite start={0} end={3}>
//       <TextSprite text="Hello" x={100} y={300} size={72} color="#111" />
//     </Sprite>
//     <Sprite start={2} end={8}>
//       <ImageSprite src="hero.png" x={200} y={120} width={640} height={360} kenBurns />
//     </Sprite>
//   </Stage>
//
// Stage({width,height,duration,background,fps,loop,autoplay}) — auto-scales to
//   viewport; scrubber + play/pause + ←/→ seek + space + 0-reset; persists
//   playhead. The canvas is an <svg><foreignObject>, export-ready: Share →
//   Export → Video (or the PlaybackBar's download button) renders it to .mp4.
//   Screenshot tools DOM-rerender (not pixel-capture) and unwrap this wrapper
//   so captures should work — but if one comes back black, that's a capture
//   artifact, not a render bug; trust the live preview.
// Sprite({start,end,keepMounted}) — mounts children only while playhead is in
//   [start,end]. Children read {localTime, progress, duration} via useSprite().
// useTime() → seconds; useTimeline() → {time,duration,playing,setTime,setPlaying}.
// TextSprite({text,x,y,size,color,font,weight,align,entryDur,exitDur}) — fades/scales in+out.
// ImageSprite({src,x,y,width,height,fit,radius,kenBurns,placeholder}) — same, with optional ken-burns.
// RectSprite({x,y,width,height,color,radius}) — solid box with entry/exit.
// VideoSprite({src,start,end,speed,style}) — looped <video> clip synced to the
//   timeline; its audio is mixed into the exported video.
// Easing.{linear,easeIn/Out/InOut Quad/Cubic/Quart/Quint/Expo/Back, …}
// interpolate([t0,t1,…],[v0,v1,…],ease?) → (t)=>v  — piecewise tween.
// animate({from,to,start,end,ease}) → (t)=>v  — single tween.
//
// Build scenes by composing Sprites inside Stage. Absolutely-position elements.
//
// In a .dc.html project, put your scene in a sibling my-scene.jsx (reading
// {Stage, Sprite, useTime, Easing, …} from window is safe) and mount BOTH:
//   <x-import component-from-global-scope="MyScene"
//             from="./animations.jsx ./my-scene.jsx"></x-import>
// The two files in from= load in order, so my-scene.jsx can use the globals
// animations.jsx set.
/* END USAGE */
// ─────────────────────────────────────────────────────────────────────────────

// ── Easing functions (hand-rolled, Popmotion-style) ─────────────────────────
// All easings take t ∈ [0,1] and return eased t ∈ [0,1] (may overshoot for back/elastic).
const Easing = {
  linear: (t) => t,

  // Quad
  easeInQuad:    (t) => t * t,
  easeOutQuad:   (t) => t * (2 - t),
  easeInOutQuad: (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),

  // Cubic
  easeInCubic:    (t) => t * t * t,
  easeOutCubic:   (t) => (--t) * t * t + 1,
  easeInOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),

  // Quart
  easeInQuart:    (t) => t * t * t * t,
  easeOutQuart:   (t) => 1 - (--t) * t * t * t,
  easeInOutQuart: (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - 8 * (--t) * t * t * t),

  // Expo
  easeInExpo:  (t) => (t === 0 ? 0 : Math.pow(2, 10 * (t - 1))),
  easeOutExpo: (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  easeInOutExpo: (t) => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    if (t < 0.5) return 0.5 * Math.pow(2, 20 * t - 10);
    return 1 - 0.5 * Math.pow(2, -20 * t + 10);
  },

  // Sine
  easeInSine:    (t) => 1 - Math.cos((t * Math.PI) / 2),
  easeOutSine:   (t) => Math.sin((t * Math.PI) / 2),
  easeInOutSine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,

  // Back (overshoot)
  easeOutBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
  },
  easeInBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return c3 * t * t * t - c1 * t * t;
  },
  easeInOutBack: (t) => {
    const c1 = 1.70158, c2 = c1 * 1.525;
    return t < 0.5
      ? (Math.pow(2 * t, 2) * ((c2 + 1) * 2 * t - c2)) / 2
      : (Math.pow(2 * t - 2, 2) * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2;
  },

  // Elastic
  easeOutElastic: (t) => {
    const c4 = (2 * Math.PI) / 3;
    if (t === 0) return 0;
    if (t === 1) return 1;
    return Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * c4) + 1;
  },
};

// ── Core interpolation helpers ──────────────────────────────────────────────

// Clamp a value to [min, max]
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

// interpolate([0, 0.5, 1], [0, 100, 50], ease?) -> fn(t)
// Popmotion-style: linearly maps t across input keyframes to output values,
// with optional easing per segment (single fn or array of fns).
function interpolate(input, output, ease = Easing.linear) {
  return (t) => {
    if (t <= input[0]) return output[0];
    if (t >= input[input.length - 1]) return output[output.length - 1];
    for (let i = 0; i < input.length - 1; i++) {
      if (t >= input[i] && t <= input[i + 1]) {
        const span = input[i + 1] - input[i];
        const local = span === 0 ? 0 : (t - input[i]) / span;
        const easeFn = Array.isArray(ease) ? (ease[i] || Easing.linear) : ease;
        const eased = easeFn(local);
        return output[i] + (output[i + 1] - output[i]) * eased;
      }
    }
    return output[output.length - 1];
  };
}

// animate({from, to, start, end, ease})(t) — simpler single-segment tween.
// Returns `from` before `start`, `to` after `end`.
function animate({ from = 0, to = 1, start = 0, end = 1, ease = Easing.easeInOutCubic }) {
  return (t) => {
    if (t <= start) return from;
    if (t >= end) return to;
    const local = (t - start) / (end - start);
    return from + (to - from) * ease(local);
  };
}

// ── Timeline context ────────────────────────────────────────────────────────

const TimelineContext = React.createContext({ time: 0, duration: 10, playing: false });

const useTime = () => React.useContext(TimelineContext).time;
const useTimeline = () => React.useContext(TimelineContext);

// ── Sprite ──────────────────────────────────────────────────────────────────
// Renders children only when the playhead is inside [start, end]. Provides
// a sub-context with `localTime` (seconds since start) and `progress` (0..1).
//
//   <Sprite start={2} end={5}>
//     {({ localTime, progress }) => <Thing x={progress * 100} />}
//   </Sprite>
//
// Or as a plain wrapper — children can call useSprite() themselves.

const SpriteContext = React.createContext({ localTime: 0, progress: 0, duration: 0 });
const useSprite = () => React.useContext(SpriteContext);

function Sprite({ start = 0, end = Infinity, children, keepMounted = false }) {
  const { time } = useTimeline();
  const visible = time >= start && time <= end;
  if (!visible && !keepMounted) return null;

  const duration = end - start;
  const localTime = Math.max(0, time - start);
  const progress = duration > 0 && isFinite(duration)
    ? clamp(localTime / duration, 0, 1)
    : 0;

  const value = { localTime, progress, duration, visible };

  return (
    <SpriteContext.Provider value={value}>
      {typeof children === 'function' ? children(value) : children}
    </SpriteContext.Provider>
  );
}

// ── Sample sprite components ────────────────────────────────────────────────

// TextSprite: fades/slides text in on entry, holds, then fades out on exit.
// Props: text, x, y, size, color, font, entryDur, exitDur, align
function TextSprite({
  text,
  x = 0, y = 0,
  size = 48,
  color = '#111',
  font = 'Inter, system-ui, sans-serif',
  weight = 600,
  entryDur = 0.45,
  exitDur = 0.35,
  entryEase = Easing.easeOutBack,
  exitEase = Easing.easeInCubic,
  align = 'left',
  letterSpacing = '-0.01em',
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let ty = 0;

  if (localTime < entryDur) {
    const t = entryEase(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    ty = (1 - t) * 16;
  } else if (localTime > exitStart) {
    const t = exitEase(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    ty = -t * 8;
  }

  const translateX = align === 'center' ? '-50%' : align === 'right' ? '-100%' : '0';

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      transform: `translate(${translateX}, ${ty}px)`,
      opacity,
      fontFamily: font,
      fontSize: size,
      fontWeight: weight,
      color,
      letterSpacing,
      whiteSpace: 'pre',
      lineHeight: 1.1,
      willChange: 'transform, opacity',
    }}>
      {text}
    </div>
  );
}

// ImageSprite: scales + fades in; optional Ken Burns drift during hold.
function ImageSprite({
  src,
  x = 0, y = 0,
  width = 400, height = 300,
  entryDur = 0.6,
  exitDur = 0.4,
  kenBurns = false,
  kenBurnsScale = 1.08,
  radius = 12,
  fit = 'cover',
  placeholder = null, // {label: string} for striped placeholder
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutCubic(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    scale = 0.96 + 0.04 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInCubic(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = (kenBurns ? kenBurnsScale : 1) + 0.02 * t;
  } else if (kenBurns) {
    const holdSpan = exitStart - entryDur;
    const holdT = holdSpan > 0 ? (localTime - entryDur) / holdSpan : 0;
    scale = 1 + (kenBurnsScale - 1) * holdT;
  }

  const content = placeholder ? (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'repeating-linear-gradient(135deg, #e9e6df 0 10px, #dcd8cf 10px 20px)',
      color: '#6b6458',
      fontFamily: 'JetBrains Mono, ui-monospace, monospace',
      fontSize: 13,
      letterSpacing: '0.04em',
      textTransform: 'uppercase',
    }}>
      {placeholder.label || 'image'}
    </div>
  ) : (
    <img src={src} alt="" style={{ width: '100%', height: '100%', objectFit: fit, display: 'block' }} />
  );

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      borderRadius: radius,
      overflow: 'hidden',
      willChange: 'transform, opacity',
    }}>
      {content}
    </div>
  );
}

// RectSprite: simple rectangle that animates position/size/color via props.
// Useful demo primitive — takes a `render` fn for per-frame customization.
function RectSprite({
  x = 0, y = 0,
  width = 100, height = 100,
  color = '#111',
  radius = 8,
  entryDur = 0.4,
  exitDur = 0.3,
  render, // optional: (ctx) => style overrides
}) {
  const spriteCtx = useSprite();
  const { localTime, duration } = spriteCtx;
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutBack(clamp(localTime / entryDur, 0, 1));
    opacity = clamp(localTime / entryDur, 0, 1);
    scale = 0.4 + 0.6 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInQuad(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = 1 - 0.15 * t;
  }

  const overrides = render ? render(spriteCtx) : {};

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      background: color,
      borderRadius: radius,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      willChange: 'transform, opacity',
      ...overrides,
    }} />
  );
}


// ── Font inlining ───────────────────────────────────────────────────────────
// Copy every @font-face rule from the page into a <style> inside the svg's
// foreignObject, with font URLs rewritten to data: URLs. Makes the svg
// self-describing so serializing it alone (video export fast path) still
// renders with the right fonts. Sets data-om-fonts-inlined on the svg when
// done so the exporter can wait for it.

function useInlineFontsInto(svgRef) {
  React.useEffect(() => {
    const svg = svgRef.current;
    const host = svg && svg.querySelector('foreignObject > div');
    if (!svg || !host) return;
    let cancelled = false;
    (async () => {
      const rules = [];
      for (const ss of document.styleSheets) {
        let cssRules;
        try { cssRules = ss.cssRules; } catch {
          // Cross-origin sheet without crossorigin attr (e.g. the standard
          // fonts.googleapis.com <link>) — fetch the CSS text directly and
          // regex-extract the @font-face blocks.
          if (ss.href) {
            try {
              const txt = await fetch(ss.href).then(r => { if (!r.ok) throw 0; return r.text(); });
              for (const ff of (txt.match(/@font-face\s*{[^}]*}/g) || []))
                rules.push({ css: ff, base: ss.href });
            } catch {}
          }
          continue;
        }
        if (!cssRules) continue;
        for (const r of cssRules) {
          if (r.type === CSSRule.FONT_FACE_RULE) {
            rules.push({ css: r.cssText, base: ss.href || location.href });
          }
        }
      }
      const toDataURL = (url) => fetch(url)
        .then(r => { if (!r.ok) throw 0; return r.blob(); })
        .then(b => new Promise(res => {
          const fr = new FileReader();
          fr.onload = () => res(fr.result);
          fr.onerror = () => res(url);
          fr.readAsDataURL(b);
        }))
        .catch(() => url);
      const parts = await Promise.all(rules.map(async ({ css, base }) => {
        const re = /url\((['"]?)([^'")]+)\1\)/g;
        let out = css, m;
        while ((m = re.exec(css))) {
          const u = m[2];
          if (u.startsWith('data:')) continue;
          let abs; try { abs = new URL(u, base).href; } catch { continue; }
          out = out.split(m[0]).join(`url("${await toDataURL(abs)}")`);
        }
        return out;
      }));
      if (cancelled || !parts.length) {
        svg.setAttribute('data-om-fonts-inlined', 'true');
        return;
      }
      const style = document.createElement('style');
      style.textContent = parts.join('\n');
      host.insertBefore(style, host.firstChild);
      svg.setAttribute('data-om-fonts-inlined', 'true');
    })();
    return () => { cancelled = true; };
  }, []);
}


function Stage({
  width = 1280,
  height = 720,
  duration = 10,
  background = '#f6f4ef',
  fps = 60,
  loop = true,
  autoplay = true,
  persistKey = 'animstage',
  children,
}) {
  // Props arrive as strings when Stage is mounted via <x-import> (DC
  // projects) — coerce so style={{width}} gets a number React can px-ify.
  width = +width || 1280; height = +height || 720;
  duration = +duration || 10; fps = +fps || 60;
  if (typeof loop === 'string') loop = loop !== 'false';
  if (typeof autoplay === 'string') autoplay = autoplay !== 'false';

  const [time, setTime] = React.useState(() => {
    try {
      const v = parseFloat(localStorage.getItem(persistKey + ':t') || '0');
      return isFinite(v) ? clamp(v, 0, duration) : 0;
    } catch { return 0; }
  });
  const [playing, setPlaying] = React.useState(autoplay);
  const [hoverTime, setHoverTime] = React.useState(null);
  const [scale, setScale] = React.useState(1);

  const stageRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const rafRef = React.useRef(null);
  const lastTsRef = React.useRef(null);

  // Persist playhead
  React.useEffect(() => {
    try { localStorage.setItem(persistKey + ':t', String(time)); } catch {}
  }, [time, persistKey]);

  // Auto-scale to fit viewport
  React.useEffect(() => {
    if (!stageRef.current) return;
    const el = stageRef.current;
    const measure = () => {
      const barH = 44; // playback bar height
      const s = Math.min(
        el.clientWidth / width,
        (el.clientHeight - barH) / height
      );
      setScale(Math.max(0.05, s));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [width, height]);

  // Animation loop
  React.useEffect(() => {
    if (!playing) {
      lastTsRef.current = null;
      return;
    }
    const step = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts;
      const dt = (ts - lastTsRef.current) / 1000;
      lastTsRef.current = ts;
      setTime((t) => {
        let next = t + dt;
        if (next >= duration) {
          if (loop) next = next % duration;
          else { next = duration; setPlaying(false); }
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      lastTsRef.current = null;
    };
  }, [playing, duration, loop]);

  // Keyboard: space = play/pause, ← → = seek
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
      if (e.code === 'Space') {
        e.preventDefault();
        setPlaying(p => !p);
      } else if (e.code === 'ArrowLeft') {
        setTime(t => clamp(t - (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.code === 'ArrowRight') {
        setTime(t => clamp(t + (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.key === '0' || e.code === 'Home') {
        setTime(0);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [duration]);

  // Video-export protocol: the exporter dispatches this event per frame;
  // pause + sync the playhead so the capture sees exactly that timestamp.
  React.useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onSeek = (e) => {
      setPlaying(false);
      setTime(clamp(e.detail.time, 0, duration));
    };
    el.addEventListener('data-om-seek-to-time-frame', onSeek);
    return () => el.removeEventListener('data-om-seek-to-time-frame', onSeek);
  }, [duration]);

  // Inline @font-face rules into the svg's foreignObject so the svg is
  // self-describing — serializing it alone (for video export) then renders
  // with the right fonts. Sets data-om-fonts-inlined once done.
  useInlineFontsInto(canvasRef);

  const displayTime = hoverTime != null ? hoverTime : time;

  const ctxValue = React.useMemo(
    () => ({ time: displayTime, duration, playing, setTime, setPlaying }),
    [displayTime, duration, playing]
  );

  return (
    <div
      ref={stageRef}
      style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center',
        background: '#0a0a0a',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* Canvas area — vertically centered in remaining space */}
      <div style={{
        flex: 1,
        width: '100%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden',
        minHeight: 0,
      }}>
        <svg
          ref={canvasRef}
          width={width} height={height}
          data-om-exportable-video-with-duration-secs={duration}
          style={{
            transform: `scale(${scale})`,
            transformOrigin: 'center',
            flexShrink: 0,
            boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            display: 'block',
          }}
        >
          <foreignObject x="0" y="0" width="100%" height="100%">
            <div
              xmlns="http://www.w3.org/1999/xhtml"
              style={{
                width, height,
                background,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <TimelineContext.Provider value={ctxValue}>
                {children}
              </TimelineContext.Provider>
            </div>
          </foreignObject>
        </svg>
      </div>

      {/* Playback bar — stacked below canvas, never overlapping */}
      <PlaybackBar
        time={displayTime}
        actualTime={time}
        duration={duration}
        playing={playing}
        onPlayPause={() => setPlaying(p => !p)}
        onReset={() => { setTime(0); }}
        onSeek={(t) => setTime(t)}
        onHover={(t) => setHoverTime(t)}
      />
    </div>
  );
}

// ── Playback bar ────────────────────────────────────────────────────────────
// Play/pause, return-to-begin, scrub track, time display.
// Uses fixed-width time fields so layout doesn't thrash.

function PlaybackBar({ time, duration, playing, onPlayPause, onReset, onSeek, onHover }) {
  const trackRef = React.useRef(null);
  const [dragging, setDragging] = React.useState(false);

  const timeFromEvent = React.useCallback((e) => {
    const rect = trackRef.current.getBoundingClientRect();
    const x = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    return x * duration;
  }, [duration]);

  const onTrackMove = (e) => {
    if (!trackRef.current) return;
    const t = timeFromEvent(e);
    if (dragging) {
      onSeek(t);
    } else {
      onHover(t);
    }
  };

  const onTrackLeave = () => {
    if (!dragging) onHover(null);
  };

  const onTrackDown = (e) => {
    setDragging(true);
    const t = timeFromEvent(e);
    onSeek(t);
    onHover(null);
  };

  React.useEffect(() => {
    if (!dragging) return;
    const onUp = () => setDragging(false);
    const onMove = (e) => {
      if (!trackRef.current) return;
      const t = timeFromEvent(e);
      onSeek(t);
    };
    window.addEventListener('mouseup', onUp);
    window.addEventListener('mousemove', onMove);
    return () => {
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('mousemove', onMove);
    };
  }, [dragging, timeFromEvent, onSeek]);

  const pct = duration > 0 ? (time / duration) * 100 : 0;
  const fmt = (t) => {
    const total = Math.max(0, t);
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    const cs = Math.floor((total * 100) % 100);
    return `${String(m).padStart(1, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
  };

  const mono = 'JetBrains Mono, ui-monospace, SFMono-Regular, monospace';

  return (
    <div data-omelette-chrome style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '8px 16px',
      background: 'rgba(20,20,20,0.92)',
      borderTop: '1px solid rgba(255,255,255,0.08)',
      width: '100%',
      maxWidth: 680,
      alignSelf: 'center',

      borderRadius: 8,
      color: '#f6f4ef',
      fontFamily: 'Inter, system-ui, sans-serif',
      userSelect: 'none',
      flexShrink: 0,
    }}>
      <IconButton onClick={onReset} title="Return to start (0)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M3 2v10M12 2L5 7l7 5V2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"/>
        </svg>
      </IconButton>
      <IconButton onClick={onPlayPause} title="Play/pause (space)">
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="3" y="2" width="3" height="10" fill="currentColor"/>
            <rect x="8" y="2" width="3" height="10" fill="currentColor"/>
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 2l9 5-9 5V2z" fill="currentColor"/>
          </svg>
        )}
      </IconButton>

      {/* Current time: fixed width so it doesn't thrash */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'right',
        color: '#f6f4ef',
      }}>
        {fmt(time)}
      </div>

      {/* Scrub track */}
      <div
        ref={trackRef}
        onMouseMove={onTrackMove}
        onMouseLeave={onTrackLeave}
        onMouseDown={onTrackDown}
        style={{
          flex: 1,
          height: 22,
          position: 'relative',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center',
        }}
      >
        <div style={{
          position: 'absolute',
          left: 0, right: 0, height: 4,
          background: 'rgba(255,255,255,0.12)',
          borderRadius: 2,
        }}/>
        <div style={{
          position: 'absolute',
          left: 0, width: `${pct}%`, height: 4,
          background: 'oklch(72% 0.12 250)',
          borderRadius: 2,
        }}/>
        <div style={{
          position: 'absolute',
          left: `${pct}%`, top: '50%',
          width: 12, height: 12,
          marginLeft: -6, marginTop: -6,
          background: '#fff',
          borderRadius: 6,
          boxShadow: '0 2px 4px rgba(0,0,0,0.4)',
        }}/>
      </div>

      {/* Duration: fixed width */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'left',
        color: 'rgba(246,244,239,0.55)',
      }}>
        {fmt(duration)}
      </div>

      {typeof VideoEncoder !== 'undefined' && (
        <IconButton
          title="Export video"
          onClick={() => window.parent.postMessage({ type: 'omelette:request-video-export' }, '*')}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M7 2v7m0 0L4 6m3 3l3-3M2 12h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </IconButton>
      )}
    </div>
  );
}

function IconButton({ children, onClick, title }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 28, height: 28,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: hover ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 6,
        color: '#f6f4ef',
        cursor: 'pointer',
        padding: 0,
        transition: 'background 120ms',
      }}
    >
      {children}
    </button>
  );
}


// ── VideoSprite ─────────────────────────────────────────────────────────────
// Renders a <video> that loops within [start,end] of its source at `speed`,
// kept in sync with the Stage's playhead. Carries the
// data-om-exportable-video-play-* attrs so video export can mix its audio.
//
//   <VideoSprite src="clip.mp4" start={2} end={5} speed={1}
//     style={{ width: 640, height: 360 }} />

function VideoSprite({ src, start = 0, end, speed = 1, style, ...rest }) {
  start = +start || 0; speed = +speed || 1;
  if (end != null) end = +end || undefined;
  const t = useTime();
  const ref = React.useRef(null);
  const span = Math.max(0.001, ((end ?? start + 1) - start));
  React.useEffect(() => {
    const v = ref.current;
    if (!v || v.readyState < 1) return;
    const target = start + ((t * speed) % span);
    if (Math.abs(v.currentTime - target) > 0.05) v.currentTime = target;
  }, [t, start, span, speed]);
  return (
    <video
      ref={ref}
      src={src}
      muted playsInline preload="auto"
      data-om-exportable-video-play-start={start}
      data-om-exportable-video-play-end={end ?? start + span}
      data-om-exportable-video-play-speed={speed}
      style={{ display: 'block', objectFit: 'cover', ...style }}
      {...rest}
    />
  );
}


Object.assign(window, {
  Easing, interpolate, animate, clamp,
  TimelineContext, useTime, useTimeline,
  Sprite, SpriteContext, useSprite,
  TextSprite, ImageSprite, RectSprite, VideoSprite,
  Stage, PlaybackBar,
});



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
