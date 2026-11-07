// Three-tomoe Sharingan. Pure SVG so it can spin via CSS and double as the app's loading indicator.
export default function Sharingan({ className = "", spin = false }: { className?: string; spin?: boolean }) {
  return (
    <svg viewBox="-50 -50 100 100" className={className} role="img" aria-label="Sharingan">
      <defs>
        <radialGradient id="iris"><stop offset="0" stopColor="#ff3b3b" /><stop offset="1" stopColor="#a30d18" /></radialGradient>
      </defs>
      <circle r="48" fill="url(#iris)" />
      <circle r="33" fill="none" stroke="#120305" strokeWidth="2.2" />
      <g className={spin ? "spin-slow" : ""} style={{ transformOrigin: "center" }}>
        {[0, 120, 240].map((a) => (
          <g key={a} transform={`rotate(${a})`} fill="#120305">
            <circle cx="0" cy="-25" r="6.5" />
            <path d="M-6.4 -24.2 C-17 -24 -24 -17 -25 -8 C-18 -15 -11 -17 -6.4 -19.5 Z" />
          </g>
        ))}
      </g>
      <circle r="7" fill="#120305" />
    </svg>
  );
}
