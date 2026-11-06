import { downloadUrl, type Transfer } from "@/lib/api";

export default function Done({ t }: { t: Transfer }) {
  const c = t.counts;
  const matched = c.matched + c.added;
  const open = c.review + c.unresolved;
  const stats: [number, string, string][] = [
    [matched, "matched", "text-ok"],
    [c.review, "need your decision", "text-warn"],
    [c.unresolved, "not found", "text-bad"],
    [c.duplicate, "duplicates removed", "text-mute"],
  ];
  const btn = "rounded-md border border-ink px-3.5 py-1.5 text-sm font-semibold hover:bg-ink hover:text-paper";

  return (
    <div className="mt-6 rounded-lg border border-line bg-card p-5 sm:p-6">
      <dl className="flex flex-wrap gap-x-10 gap-y-3">
        {stats.map(([n, label, cls]) => (
          <div key={label}>
            <dd className={`text-4xl font-bold tabular-nums ${cls}`}>{n}</dd>
            <dt className="text-sm text-mute">{label}</dt>
          </div>
        ))}
      </dl>
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm font-semibold">Download matched playlist</span>
        {(["txt", "csv", "json"] as const).map((f) => (
          <a key={f} className={btn} href={downloadUrl(t.id, "matched", f)} download>.{f}</a>
        ))}
        <a className="text-sm text-mute underline underline-offset-4 hover:text-ink" href={downloadUrl(t.id, "source", "txt")} download>
          Backup of your original
        </a>
        {open > 0 && (
          <a className="ml-auto text-sm text-mute underline underline-offset-4 hover:text-ink" href={downloadUrl(t.id, "unmatched", "csv")} download>
            Download the {open} unresolved as CSV
          </a>
        )}
      </div>
      <p className="mt-3 text-xs text-mute">
        Matched files use the catalog&rsquo;s canonical title, artist and ISRC, in your original order. Tracks still
        awaiting a decision are left out until you resolve them.
      </p>
    </div>
  );
}
