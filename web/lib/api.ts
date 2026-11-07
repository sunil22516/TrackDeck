// Thin client over the real TrackDeck backend routes (see /openapi.json). No invented endpoints.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Counts = Record<"pending" | "matched" | "review" | "unresolved" | "duplicate" | "skipped" | "added" | "failed", number>;
export type Transfer = {
  id: string; status: "matching" | "ready" | "writing" | "completed" | "failed";
  destination: string; playlist_name: string; total: number; processed: number;
  counts: Counts; error: string | null;
};
export type Track = { title: string; artists: string[]; album?: string | null; duration_ms?: number | null };
export type Candidate = Track & { id: string };
export type Scored = { candidate: Candidate; score: number; flags: string[] };
export type Row = {
  id: number; position: number; status: string; source: Track; score: number | null;
  reason: string | null; chosen: Candidate | null; candidates: Scored[] | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const destinations = () => call<{ destinations: string[] }>("/v1/destinations").then((d) => d.destinations);

export function upload(file: File, destination: string): Promise<Transfer[]> {
  const fd = new FormData();
  fd.set("file", file);
  fd.set("destination", destination);
  return call("/v1/transfers", { method: "POST", body: fd });
}
export const getTransfer = (id: string) => call<Transfer>(`/v1/transfers/${id}`);
export const rematch = (id: string) => call<Transfer>(`/v1/transfers/${id}/rematch`, { method: "POST" });
export const reviewRows = (id: string) =>
  Promise.all(
    (["review", "unresolved"] as const).map((s) =>
      call<Row[]>(`/v1/transfers/${id}/tracks?status=${s}&include_candidates=true&limit=500`),
    ),
  ).then(([a, b]) => [...a, ...b].sort((x, y) => x.position - y.position));
export const resolve = (id: string, trackId: number, body: { candidate_id?: string; skip?: boolean }) =>
  call<Row>(`/v1/transfers/${id}/tracks/${trackId}/resolve`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
export const downloadUrl = (id: string, kind: "matched" | "unmatched" | "source", format: "txt" | "csv" | "json") =>
  `${API}/v1/transfers/${id}/export?kind=${kind}&format=${format}`;
export const remove = (id: string) => fetch(`${API}/v1/transfers/${id}`, { method: "DELETE" }).catch(() => {});
