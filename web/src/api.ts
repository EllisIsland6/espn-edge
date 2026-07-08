// Thin API client. All ESPN traffic goes through the backend (SPEC guardrail 11);
// the frontend only ever talks to our own FastAPI.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface Health {
  status: string;
  season: number;
  db_path: string;
}

export async function getHealth(): Promise<Health> {
  const res = await fetch(`${BASE}/api/health`);
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}
