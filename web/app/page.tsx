import Hero from "@/components/Hero";
import Transfer from "@/components/Transfer";
import { Sections, Footer } from "@/components/Sections";
import Sharingan from "@/components/Sharingan";

const links = [["How it works", "#how"], ["Formats", "#formats"], ["Features", "#features"], ["Pricing", "#pricing"], ["FAQ", "#faq"]];

export default function Page() {
  return (
    <>
      <nav className="fixed inset-x-0 top-0 z-30 flex items-center justify-between px-6 py-5 text-white sm:px-10" style={{ mixBlendMode: "difference" }}>
        <a href="#" className="flex items-center gap-2 text-lg font-black uppercase tracking-tighter">
          <Sharingan className="h-6 w-6" /> TrackDeck
        </a>
        <div className="hidden gap-7 text-xs font-semibold uppercase tracking-widest md:flex">
          {links.map(([l, h]) => <a key={h} href={h} className="hover:opacity-60">{l}</a>)}
        </div>
        <a href="#transfer" className="rounded bg-white px-4 py-2 text-xs font-bold uppercase tracking-wider text-black">Start</a>
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
