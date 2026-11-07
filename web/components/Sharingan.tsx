// Simple spinning ring used as the app's loading indicator.
export default function Spinner({ className = "", spin = false }: { className?: string; spin?: boolean }) {
  return (
    <svg viewBox="0 0 100 100" className={className} role="img" aria-label="Loading">
      <circle cx="50" cy="50" r="42" fill="none" stroke="#d91f2e" strokeWidth="8" strokeOpacity="0.18" />
      <circle
        cx="50" cy="50" r="42"
        fill="none" stroke="#d91f2e" strokeWidth="8"
        strokeLinecap="round"
        strokeDasharray="66 198"
        className={spin ? "spin-slow" : ""}
        style={{ transformOrigin: "center" }}
      />
    </svg>
  );
}
