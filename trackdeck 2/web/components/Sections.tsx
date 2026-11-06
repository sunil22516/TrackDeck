import type { ReactNode } from "react";

const SAMPLE = `# My playlist
# format: Title | Artist1; Artist2 | Album | mm:ss | ISRC
Blinding Lights | The Weeknd | After Hours | 3:20 | USUG11904206
Get Lucky | Daft Punk; Pharrell Williams
Bohemian Rhapsody | Queen
`;

function Section({ id, kicker, title, children }: { id: string; kicker: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="mx-auto max-w-5xl scroll-mt-12 border-t border-line px-5 py-20 sm:py-28">
      <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cobalt">{kicker}</p>
      <h2 className="mt-3 max-w-2xl text-3xl font-black uppercase leading-[1] tracking-tighter sm:text-5xl">{title}</h2>
      <div className="mt-10">{children}</div>
    </section>
  );
}

const tag = (soon: boolean) => (
  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${soon ? "bg-line text-mute" : "bg-cobalt/20 text-cobalt"}`}>{soon ? "Soon" : "Live"}</span>
);

const FEATURES: [string, string, boolean][] = [
  ["Version-safe matching", "Scores title, artist, album and length. Refuses to swap in remixes, live cuts, karaoke or sped-up versions.", false],
  ["You approve the doubtful ones", "Anything uncertain is held for review with alternatives and a score. Nothing is guessed.", false],
  ["ISRC-level exports", "The matched file carries each recording's canonical title, artist and ISRC, ready for other tools.", false],
  ["Batch playlists", "Upload a full Spotify export and every playlist in it becomes its own transfer.", false],
  ["Resume and retry", "If the catalog is unreachable, retry only the failed tracks. Nothing is redone.", false],
  ["Backups", "Download your original playlist as TXT, CSV or JSON at any time.", false],
  ["Direct transfer to Spotify, YouTube Music", "Write the playlist straight to your account. Needs each platform's API approval.", true],
  ["Accounts, history and auto-sync", "Keep playlists mirrored across services and revisit past transfers.", true],
];

const FAQ: [string, string][] = [
  ["Do I need to log in to Spotify or anything else?", "No. You upload a file you exported yourself. TrackDeck never asks for a streaming-service password or token."],
  ["What happens to my data?", "Your playlist is used only for matching. Transfers and their track data are deleted automatically after 24 hours, or immediately if you press “Start over and delete my data”."],
  ["Why not just add everything automatically?", "Because a wrong version is worse than a missing song. Low-confidence matches are held for you; you can always skip."],
  ["Which catalog does it match against?", "MusicBrainz, an open music database. It is excellent for mainstream music; very obscure or brand-new releases may need your review."],
  ["Is there a limit?", "Up to 5 MB and 500 tracks per playlist, and 10 uploads per hour. Matching runs at roughly one song per second, so large playlists take a few minutes."],
  ["How do I get my Spotify export?", "In Spotify, request your data under Account → Privacy. They email a zip; upload the playlist JSON inside it. CSV exports from tools like Exportify also work."],
];

export function Sections() {
  const steps: [string, string][] = [
    ["Upload", "Drop a Spotify export, CSV, TXT or JSON. Pick the catalog."],
    ["Match", "Each track is searched, scored and checked against wrong-version traps."],
    ["Review", "Confirm, swap or skip anything uncertain."],
    ["Download", "Get your clean, ordered playlist plus a list of what didn’t make it."],
  ];
  return (
    <>
      <Section id="how" kicker="How it works" title="Four steps. No account.">
        <ol className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {steps.map(([t, d], i) => (
            <li key={t} className="bg-card p-6">
              <span className="font-mono text-sm text-cobalt">0{i + 1}</span>
              <h3 className="mt-6 text-lg font-bold">{t}</h3>
              <p className="mt-2 text-sm leading-relaxed text-mute">{d}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section id="formats" kicker="Formats" title="Bring a file from anywhere.">
        <div className="grid gap-8 md:grid-cols-2">
          <ul className="divide-y divide-line border-y border-line text-sm">
            {[["Spotify data export", ".json · all playlists at once"], ["CSV", ".csv · Exportify-style headers"], ["Plain text", ".txt · “Artist - Title” or pipe-separated"], ["TrackDeck JSON", ".json · what we export"]].map(([a, b]) => (
              <li key={a} className="flex justify-between gap-4 py-3"><span className="font-semibold">{a}</span><span className="text-mute">{b}</span></li>
            ))}
          </ul>
          <div className="rounded-lg border border-line bg-card p-6">
            <p className="text-sm text-mute">Not sure what a file should look like? Start from a sample.</p>
            <a className="mt-4 inline-block rounded-md border border-ink px-4 py-2 text-sm font-semibold hover:bg-ink hover:text-paper"
              href={`data:text/plain;charset=utf-8,${encodeURIComponent(SAMPLE)}`} download="trackdeck-sample.txt">
              Download sample.txt
            </a>
            <p className="mt-6 text-sm text-mute">Output formats: <span className="text-ink">TXT, CSV, JSON</span>. Catalog today: <span className="text-ink">MusicBrainz</span>. Direct Spotify and YouTube Music writing is on the roadmap.</p>
          </div>
        </div>
      </Section>

      <Section id="features" kicker="Features" title="Built around not getting it wrong.">
        <div className="grid gap-4 sm:grid-cols-2">
          {FEATURES.map(([t, d, soon]) => (
            <article key={t} className={`rounded-lg border border-line bg-card p-6 ${soon ? "opacity-60" : ""}`}>
              <div className="flex items-start justify-between gap-3"><h3 className="font-bold">{t}</h3>{tag(soon)}</div>
              <p className="mt-2 text-sm leading-relaxed text-mute">{d}</p>
            </article>
          ))}
        </div>
      </Section>

      <Section id="pricing" kicker="Pricing" title="Free while we learn.">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-cobalt bg-card p-8">
            <p className="text-sm font-bold uppercase tracking-widest text-cobalt">Free · available now</p>
            <ul className="mt-5 space-y-2 text-sm text-mute">
              <li>500 tracks per playlist</li><li>Unlimited downloads: TXT, CSV, JSON</li><li>Review and retry tools</li><li>No account, 24-hour deletion</li>
            </ul>
            <a href="#transfer" className="mt-8 inline-block rounded bg-cobalt px-5 py-2.5 text-sm font-bold text-white">Start a transfer</a>
          </div>
          <div className="rounded-lg border border-line bg-card p-8">
            <p className="text-sm font-bold uppercase tracking-widest text-mute">Pro · not released</p>
            <ul className="mt-5 space-y-2 text-sm text-mute">
              <li>Larger playlists and faster matching</li><li>Direct transfer to your streaming account</li><li>History and auto-sync</li>
            </ul>
            <a href="mailto:hello@trackdeck.app?subject=Pro%20early%20access" className="mt-8 inline-block rounded border border-line px-5 py-2.5 text-sm font-bold hover:border-ink">Ask for early access</a>
            <p className="mt-3 text-xs text-mute">No price set yet. We’ll decide it with early users.</p>
          </div>
        </div>
      </Section>

      <Section id="faq" kicker="FAQ" title="Before you upload.">
        <div className="divide-y divide-line border-y border-line">
          {FAQ.map(([q, a]) => (
            <details key={q} className="group py-5">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-semibold">{q}<span className="text-cobalt transition-transform group-open:rotate-45">+</span></summary>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-mute">{a}</p>
            </details>
          ))}
        </div>
      </Section>
    </>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-line px-6 py-10 text-xs leading-relaxed text-mute sm:px-10">
      <div className="mx-auto flex max-w-5xl flex-col justify-between gap-6 sm:flex-row">
        <p className="max-w-md">TrackDeck uses your file only to match songs. Results are deleted after 24 hours. No streaming-service login is ever requested. Music data from MusicBrainz.</p>
        <p>Not affiliated with Spotify, YouTube or Apple.</p>
      </div>
    </footer>
  );
}
