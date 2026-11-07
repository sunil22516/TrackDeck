"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { destinations, getTransfer, rematch, remove, upload, type Transfer } from "@/lib/api";
import Review from "@/components/Review";
import Done from "@/components/Done";
import Sharingan from "@/components/Sharingan";

const LABEL: Record<string, string> = { musicbrainz: "MusicBrainz, open catalog (free)", mock: "Demo catalog (40 songs)" };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Transfer() {
  const [dests, setDests] = useState<string[]>([]);
  const [dest, setDest] = useState("");
  const [transfers, setTransfers] = useState<Transfer[]>([]);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [waking, setWaking] = useState(true);
  const input = useRef<HTMLInputElement>(null);

  // Free hosting sleeps when idle; the first request can take up to a minute. Retry instead of failing.
  useEffect(() => {
    let live = true;
    (async () => {
      for (let i = 0; i < 18 && live; i++) {
        try {
          const d = await destinations();
          if (!live) return;
          setDests(d); setDest(d.find((x) => x !== "mock") ?? d[0] ?? ""); setWaking(false); setError(null);
          return;
        } catch { await sleep(4000); }
      }
      if (live) { setWaking(false); setError("Can't reach the TrackDeck server right now. Please try again in a minute."); }
    })();
    return () => { live = false; };
  }, []);

  const t = transfers[active];
  const patch = useCallback((n: Transfer) => setTransfers((all) => all.map((x) => (x.id === n.id ? n : x))), []);
  const refresh = useCallback(() => (t ? getTransfer(t.id).then(patch) : Promise.resolve()), [t, patch]);

  useEffect(() => {
    if (!t || t.status !== "matching") return;
    const h = setInterval(() => getTransfer(t.id).then(patch).catch(() => {}), 700);
    return () => clearInterval(h);
  }, [t, patch]);

  async function start(file: File) {
    setError(null); setBusy(true);
    try { setTransfers(await upload(file, dest)); setActive(0); }
    catch (e) { setError(e instanceof Error ? e.message : "Upload failed"); }
    finally { setBusy(false); }
  }

  return (
    <section id="transfer" className="mx-auto max-w-4xl scroll-mt-12 px-5 py-24 sm:py-32">
      <header className="mb-10 flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-[0.25em] text-mute">The transfer</span>
        {transfers.length > 0 && (
          <button className="text-sm text-mute underline underline-offset-4 hover:text-ink" onClick={() => { transfers.forEach((x) => remove(x.id)); setTransfers([]); setError(null); }}>
            Start over and delete my data
          </button>
        )}
      </header>

      {transfers.length === 0 ? (
        <section>
          <h2 className="max-w-2xl text-4xl font-black uppercase leading-[1] tracking-tighter sm:text-6xl">
            Feed it a playlist.
          </h2>
          <p className="mt-5 max-w-xl text-lg leading-relaxed text-mute">
            Upload an export from Spotify, or a CSV or TXT list. TrackDeck matches each song, holds back anything
            uncertain for your say-so, and gives you a clean file to take anywhere.
          </p>

          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) start(f); }}
            className={`dz mt-10 rounded-lg border-2 border-dashed p-10 text-center transition-all ${drag ? "border-cobalt bg-card" : "border-line bg-card"}`}
          >
            <p className="text-lg font-semibold">{busy ? "Reading your file…" : "Drop a playlist file here"}</p>
            <p className="mt-1 text-sm text-mute">Spotify data export (.json), .csv, .txt. Up to 5 MB.</p>
            <input ref={input} type="file" accept=".json,.csv,.txt,text/plain,application/json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) start(f); e.target.value = ""; }} />
            <button disabled={busy || !dest || waking} onClick={() => input.current?.click()}
              className="mt-5 rounded-md bg-cobalt px-5 py-2.5 font-semibold text-white hover:brightness-110 disabled:opacity-50">
              {waking ? "Waking the server…" : "Choose file"}
            </button>
            {waking && <p className="mt-2 text-xs text-mute">The free server naps when idle. This can take up to a minute.</p>}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-mute">
            <label htmlFor="dest">Match against</label>
            <select id="dest" value={dest} onChange={(e) => setDest(e.target.value)}
              className="rounded border border-line bg-card px-2 py-1 text-ink">
              {dests.map((d) => <option key={d} value={d}>{LABEL[d] ?? d}</option>)}
            </select>
            {dest === "mock" && <span className="text-warn">Demo catalog: 40 songs only. Most real playlists will show as not found.</span>}
            {dest === "musicbrainz" && <span>Open database, so obscure tracks may need your review. Roughly 1 song per second.</span>}
          </div>
          {error && <p role="alert" className="mt-4 font-medium text-bad">{error}</p>}
        </section>
      ) : (
        <section>
          {transfers.length > 1 && (
            <div role="tablist" className="mb-6 flex flex-wrap gap-2">
              {transfers.map((x, i) => (
                <button key={x.id} role="tab" aria-selected={i === active} onClick={() => setActive(i)}
                  className={`rounded-full border px-3.5 py-1 text-sm ${i === active ? "border-ink bg-ink text-paper" : "border-line bg-card"}`}>
                  {x.playlist_name} <span className="opacity-60">{x.total}</span>
                </button>
              ))}
            </div>
          )}
          <h2 className="text-3xl font-bold tracking-tight">{t.playlist_name}</h2>

          {t.status === "matching" && (
            <div className="mt-6" role="status">
              <Sharingan spin className="mb-4 h-14 w-14" />
              <div className="h-2 overflow-hidden rounded bg-line">
                <div className="h-full bg-cobalt transition-[width]" style={{ width: `${Math.max(4, (t.processed / Math.max(t.total, 1)) * 100)}%` }} />
              </div>
              <p className="mt-2 text-sm text-mute">
                Matched {t.processed} of {t.total} tracks
                {t.destination === "musicbrainz" && t.total - t.processed > 5 && ` · about ${Math.ceil(((t.total - t.processed) * 1.6) / 60)} min left`}
                . You can leave this tab open while it works.
              </p>
            </div>
          )}

          {t.status === "failed" && (
            <div role="alert" className="mt-6 rounded-lg border border-bad/40 bg-card p-5">
              <p className="font-semibold text-bad">Matching stopped</p>
              <p className="mt-1 text-sm text-mute">{t.error}</p>
              <button className="mt-3 rounded-md bg-ink px-4 py-2 text-sm font-semibold text-paper"
                onClick={() => rematch(t.id).then(patch).catch((e) => setError(e.message))}>
                Retry unmatched tracks
              </button>
              {error && <p className="mt-2 text-sm text-bad">{error}</p>}
            </div>
          )}

          {(t.status === "ready" || t.status === "completed") && (
            <>
              <Done t={t} />
              <Review key={t.id} id={t.id} onResolved={refresh} />
            </>
          )}
        </section>
      )}
    </section>
  );
}
