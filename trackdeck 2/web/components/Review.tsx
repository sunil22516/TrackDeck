"use client";
import { useEffect, useState } from "react";
import { resolve, reviewRows, type Candidate, type Row, type Track } from "@/lib/api";

const dur = (ms?: number | null) => (ms ? `${Math.floor(ms / 60000)}:${String(Math.round((ms % 60000) / 1000)).padStart(2, "0")}` : "");
const line = (t: Track) => [t.artists.join(", "), t.album, dur(t.duration_ms)].filter(Boolean).join(" · ");

function Meter({ score }: { score: number }) {
  const tone = score >= 0.88 ? "bg-ok" : score >= 0.7 ? "bg-warn" : "bg-bad";
  return (
    <span className="inline-flex items-center gap-2" title={`match score ${score.toFixed(2)}`}>
      <span className="h-1.5 w-16 overflow-hidden rounded bg-line"><span className={`block h-full ${tone}`} style={{ width: `${score * 100}%` }} /></span>
      <span className="font-mono text-xs tabular-nums text-mute">{score.toFixed(2)}</span>
    </span>
  );
}

export default function Review({ id, onResolved }: { id: string; onResolved: () => void }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<number | null>(null);

  useEffect(() => { reviewRows(id).then(setRows).catch((e) => setError(e.message)); }, [id]);

  async function act(row: Row, body: { candidate_id?: string; skip?: boolean }) {
    setPending(row.id); setError(null);
    try {
      await resolve(id, row.id, body);
      setRows((r) => r?.filter((x) => x.id !== row.id) ?? r);
      onResolved();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save that choice"); }
    finally { setPending(null); }
  }

  if (error && !rows) return <p role="alert" className="mt-8 text-bad">{error}</p>;
  if (!rows) return <p className="mt-8 text-mute">Loading tracks that need a look…</p>;
  if (rows.length === 0) return <p className="mt-8 font-medium text-ok">Everything is resolved. Download your playlist above.</p>;

  return (
    <div className="mt-10">
      <h3 className="text-xl font-bold">{rows.length} to check</h3>
      <p className="mt-1 max-w-xl text-sm text-mute">
        Nothing here is added until you choose. Skipping is always safe, and a skipped song beats a wrong one.
      </p>
      {error && <p role="alert" className="mt-3 text-sm font-medium text-bad">{error}</p>}
      <ul className="mt-5 divide-y divide-line border-y border-line">
        {rows.map((row) => {
          const [best, ...others] = (row.candidates ?? []);
          const isLow = row.status === "unresolved";
          return (
            <li key={row.id} className="grid gap-4 py-5 sm:grid-cols-[1fr_1fr_auto] sm:items-start">
              <div>
                <p className="text-xs text-mute">Your file, track {row.position + 1}</p>
                <p className="font-semibold">{row.source.title}</p>
                <p className="text-sm text-mute">{line(row.source)}</p>
              </div>
              <div>
                {best ? (
                  <>
                    <p className="text-xs text-mute">{isLow ? "Closest, probably wrong" : "Best match"}</p>
                    <p className="font-semibold">{best.candidate.title}</p>
                    <p className="text-sm text-mute">{line(best.candidate)}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                      <Meter score={best.score} />
                      {row.reason && <span className="text-xs text-warn">{row.reason}</span>}
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-bad">{row.reason ?? "No candidates found"}</p>
                )}
                {others.length > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-sm text-cobalt">{others.length} other option{others.length > 1 ? "s" : ""}</summary>
                    <ul className="mt-2 space-y-2">
                      {others.map((o) => <Option key={o.candidate.id} c={o.candidate} score={o.score} disabled={pending === row.id} onPick={() => act(row, { candidate_id: o.candidate.id })} />)}
                    </ul>
                  </details>
                )}
              </div>
              <div className="flex gap-2 sm:flex-col">
                {best && (
                  <button disabled={pending === row.id} onClick={() => act(row, { candidate_id: best.candidate.id })}
                    className="rounded-md bg-cobalt px-3.5 py-1.5 text-sm font-semibold text-white hover:brightness-110 disabled:opacity-50">
                    Use this
                  </button>
                )}
                <button disabled={pending === row.id} onClick={() => act(row, { skip: true })}
                  className="rounded-md border border-line px-3.5 py-1.5 text-sm font-semibold hover:border-ink disabled:opacity-50">
                  Skip
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Option({ c, score, onPick, disabled }: { c: Candidate; score: number; onPick: () => void; disabled: boolean }) {
  return (
    <li className="flex items-center justify-between gap-3 rounded border border-line bg-card px-3 py-2">
      <span className="min-w-0 text-sm"><span className="font-medium">{c.title}</span><br /><span className="text-mute">{line(c)}</span></span>
      <span className="flex shrink-0 items-center gap-3"><Meter score={score} />
        <button disabled={disabled} onClick={onPick} className="text-sm font-semibold text-cobalt underline underline-offset-4 disabled:opacity-50">Use</button>
      </span>
    </li>
  );
}
