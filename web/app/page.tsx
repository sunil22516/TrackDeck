import Hero from "@/components/Hero";
import Transfer from "@/components/Transfer";
import { Sections, Footer } from "@/components/Sections";

const links = [["How it works", "#how"], ["Formats", "#formats"], ["Features", "#features"], ["Pricing", "#pricing"], ["FAQ", "#faq"]];

function Logo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="3.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export default function Page() {
  return (
    <>
      <nav className="fixed inset-x-0 top-0 z-30 flex items-center justify-between px-6 py-4 sm:px-10 backdrop-blur-md bg-paper/60 border-b border-white/5 text-ink">
        <a href="#" className="flex items-center gap-2 text-lg font-black uppercase tracking-tighter">
          <Logo className="h-6 w-6" /> TrackDeck
        </a>
        <div className="hidden gap-7 text-xs font-semibold uppercase tracking-widest md:flex text-mute">
          {links.map(([l, h]) => <a key={h} href={h} className="hover:text-ink transition-colors">{l}</a>)}
        </div>
        <a href="#transfer" className="rounded bg-cobalt px-4 py-2 text-xs font-bold uppercase tracking-wider text-white hover:brightness-110 transition-all">Start</a>
      </nav>
      <main>
        <Hero />
        <Transfer />
        <Sections />
      </main>
      <Footer />
    </>
  );
}
