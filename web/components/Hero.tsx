"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// Both portraits share one aspect ratio, so one layout calculation positions the glow overlays on either image.
const AR = 852 / 1846;
const EYE_Y = 0.609; // vertical focus: keep the glows mid-screen on wide viewports
const EYES = [
  { x: 0.299, y: 0.608, r: 0.047, color: "#ff1a2a" as const },
  { x: 0.701, y: 0.61, r: 0.047, color: "#8b5cf6" as const },
];
const AWAKEN_AT = 2.4; // how much pointer travel (in hero widths) before the effect fires

const VERT = `varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;
const FLUID = `
uniform sampler2D uPrev; uniform vec2 uMouse, uPrevMouse; uniform float uDecay; uniform bool uMoving; varying vec2 vUv;
void main(){
  float v = texture2D(uPrev, vUv).r * uDecay;
  if (uMoving) {
    vec2 d = uMouse - uPrevMouse; float len = length(d);
    if (len > 0.001) {
      vec2 dir = d / len; float along = clamp(dot(vUv - uPrevMouse, dir), 0.0, len);
      float dist = length(vUv - (uPrevMouse + along * dir));
      v += smoothstep(0.09, 0.0, dist) * 0.3;
    }
  }
  gl_FragColor = vec4(v, 0.0, 0.0, 1.0);
}`;
const DISPLAY = `
uniform sampler2D uFluid, uTop, uBottom; uniform vec2 uRes, uTexSize; uniform float uDpr, uOffY; varying vec2 vUv;
vec2 coverUV(vec2 uv){
  if (uTexSize.x < 1.0) return uv;
  vec2 s = uRes / uTexSize; float sc = max(s.x, s.y); vec2 scaled = uTexSize * sc;
  vec2 off = vec2((uRes.x - scaled.x) * 0.5, uOffY);
  return (uv * uRes - off) / scaled;
}
void main(){
  float f = texture2D(uFluid, vUv).r; vec2 uv = coverUV(vUv);
  float t = smoothstep(0.02, 0.02 + 0.004 / uDpr, f);
  gl_FragColor = mix(texture2D(uTop, uv), texture2D(uBottom, uv), t);
}`;

type EyePos = { left: number; top: number; size: number };

export default function Hero() {
  const wrap = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [awake, setAwake] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [eyes, setEyes] = useState<EyePos[]>([]);
  const offY = useRef(0);

  const layout = useCallback((W: number, H: number) => {
    const s = Math.max(W / AR, H), sw = AR * s, sh = s, ox = (W - sw) / 2;
    const oy = Math.min(0, Math.max(H - sh, 0.5 * H - (1 - EYE_Y) * sh)); // bottom-origin offset, clamped to stay covered
    offY.current = oy;
    setEyes(EYES.map((e) => ({ left: ox + e.x * sw, top: H - (oy + (1 - e.y) * sh), size: e.r * sw * 2 })));
  }, []);

  const wake = useCallback(() => {
    setAwake(true);
    document.documentElement.setAttribute("data-awake", "1");
  }, []);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const host = wrap.current, cv = canvas.current;
    if (!host || !cv) return;
    layout(host.clientWidth, host.clientHeight);
    if (reduce) { setFallback(true); const t = setTimeout(wake, 1200); return () => clearTimeout(t); }

    let stop = () => {};
    let cancelled = false;
    (async () => {
      try {
        const THREE = await import("three");
        if (cancelled) return;
        const renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        const W0 = host.clientWidth, H0 = host.clientHeight;
        renderer.setSize(W0, H0, false);

        const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
        const quad = new THREE.PlaneGeometry(2, 2);
        const mouse = new THREE.Vector2(0.5, 0.5), prev = new THREE.Vector2(0.5, 0.5);
        const mkTarget = () => new THREE.WebGLRenderTarget(500, 500, { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, type: THREE.HalfFloatType });
        const targets = [mkTarget(), mkTarget()];
        let cur = 0, moving = false, lastMove = 0, travel = 0, woke = false;

        const loader = new THREE.TextureLoader();
        const blank = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
        blank.needsUpdate = true;
        const fluidMat = new THREE.ShaderMaterial({
          vertexShader: VERT, fragmentShader: FLUID,
          uniforms: { uPrev: { value: null }, uMouse: { value: mouse }, uPrevMouse: { value: prev }, uDecay: { value: 0.97 }, uMoving: { value: false } },
        });
        const dispMat = new THREE.ShaderMaterial({
          vertexShader: VERT, fragmentShader: DISPLAY,
          uniforms: {
            uFluid: { value: null }, uTop: { value: blank }, uBottom: { value: blank },
            uRes: { value: new THREE.Vector2(W0, H0) }, uTexSize: { value: new THREE.Vector2(1, 1) },
            uDpr: { value: renderer.getPixelRatio() }, uOffY: { value: offY.current },
          },
        });
        const load = (url: string, key: "uTop" | "uBottom") =>
          loader.load(url, (tex) => {
            tex.minFilter = tex.magFilter = THREE.LinearFilter;
            dispMat.uniforms[key].value = tex;
            dispMat.uniforms.uTexSize.value.set(tex.image.width, tex.image.height);
          }, undefined, () => setFallback(true));
        load("/top.jpg", "uTop"); load("/bottom.jpg", "uBottom");

        const simScene = new THREE.Scene(); simScene.add(new THREE.Mesh(quad, fluidMat));
        const scene = new THREE.Scene(); scene.add(new THREE.Mesh(quad, dispMat));

        const point = (x: number, y: number) => {
          const r = cv.getBoundingClientRect();
          if (x < r.left || x > r.right || y < r.top || y > r.bottom) { moving = false; return; }
          prev.copy(mouse);
          mouse.set((x - r.left) / r.width, 1 - (y - r.top) / r.height);
          travel += prev.distanceTo(mouse);
          moving = true; lastMove = performance.now();
          if (!woke && travel > AWAKEN_AT) { woke = true; wake(); }
        };
        const onMove = (e: MouseEvent) => point(e.clientX, e.clientY);
        const onTouch = (e: TouchEvent) => { const t = e.touches[0]; if (t) point(t.clientX, t.clientY); };
        const onResize = () => {
          const W = host.clientWidth, H = host.clientHeight;
          renderer.setSize(W, H, false);
          layout(W, H);
          dispMat.uniforms.uRes.value.set(W, H); dispMat.uniforms.uOffY.value = offY.current;
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("touchmove", onTouch, { passive: true });
        window.addEventListener("resize", onResize);
        onResize();

        let raf = 0;
        const tick = () => {
          raf = requestAnimationFrame(tick);
          if (moving && performance.now() - lastMove > 50) moving = false;
          const from = targets[cur]; cur = (cur + 1) % 2; const to = targets[cur];
          fluidMat.uniforms.uPrev.value = from.texture; fluidMat.uniforms.uMoving.value = moving;
          renderer.setRenderTarget(to); renderer.render(simScene, cam);
          dispMat.uniforms.uFluid.value = to.texture;
          renderer.setRenderTarget(null); renderer.render(scene, cam);
        };
        tick();
        stop = () => {
          cancelAnimationFrame(raf);
          window.removeEventListener("mousemove", onMove); window.removeEventListener("touchmove", onTouch); window.removeEventListener("resize", onResize);
          targets.forEach((t) => t.dispose()); quad.dispose(); fluidMat.dispose(); dispMat.dispose(); renderer.dispose();
        };
      } catch {
        setFallback(true); // no WebGL: static portrait, eyes still awaken on a timer
        setTimeout(wake, 1500);
      }
    })();
    return () => { cancelled = true; stop(); };
  }, [layout, wake]);

  return (
    <section ref={wrap} className="relative h-svh min-h-[560px] overflow-hidden bg-black">
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" aria-hidden />
      {fallback && <div className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: `url(${awake ? "/bottom.jpg" : "/top.jpg"})` }} aria-hidden />}

      {/* mouse-driven reveal: colour blooms appear on the portrait when you've moved enough */}
      {awake && eyes.map((e, i) => (
        <div key={i} className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2" style={{ left: e.left, top: e.top, width: e.size, height: e.size }} aria-hidden>
          <div className={`glow absolute -inset-[140%] rounded-full blur-2xl`} style={{ background: `radial-gradient(circle, ${EYES[i].color}80 0%, transparent 65%)` }} />
        </div>
      ))}
      {awake && <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_55%,transparent_40%,rgba(217,31,46,.22)_100%)]" aria-hidden />}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex flex-col gap-5 px-6 pb-10 text-white sm:px-10" style={{ mixBlendMode: "difference" }}>
        <p className="text-xs font-semibold uppercase tracking-[0.25em]" role="status">
          {awake ? "● Matching engine online" : "Playlist transfer · no login · free"}
        </p>
        <h1 className="max-w-3xl text-5xl font-black uppercase leading-[0.92] tracking-tighter sm:text-7xl lg:text-8xl">
          Every song.<br />The right version.
        </h1>
        <div className="pointer-events-auto flex flex-wrap items-center gap-5">
          <a href="#transfer" className="rounded bg-white px-6 py-3 text-sm font-bold uppercase tracking-wider text-black">Begin transfer</a>
          <span className={`text-xs uppercase tracking-widest transition-opacity duration-700 ${awake ? "opacity-0" : "opacity-80"}`}>Move your mouse across the image</span>
        </div>
      </div>
    </section>
  );
}
