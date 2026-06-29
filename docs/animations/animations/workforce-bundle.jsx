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



/* ===== scene ===== */

// Workforce Runtime — system explainer animation.
// Deterministic function of timeline time `t` so scrubbing + video export work.
// One agent → a few independent → organize into a tree → it GROWS into a full,
// real-world company org (integrated, not a flat grid). Lower agents use the two
// supplied app icons. Discuss = comet streaks; assign/report = up/down pulses.

// (Stage, useTime, interpolate, Easing, clamp come from the animations engine above)

const W = 1280, H = 720;
const lerp = (a, b, t) => a + (b - a) * t;
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const eio = Easing.easeInOutCubic, eob = Easing.easeOutBack;
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}

const SANS = '-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif';
const ICON = [
  (window.__resources && window.__resources.codexIcon) || 'uploads/codex-rounded2.png',
  (window.__resources && window.__resources.electronIcon) || 'uploads/electron-256.png',
];

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
