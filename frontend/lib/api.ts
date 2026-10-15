export const API = process.env.NEXT_PUBLIC_API || "http://localhost:8000";

async function req(method: string, path: string, body?: any) {
  const r = await fetch(API + path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = j.detail || JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  get: (p: string) => req("GET", p),
  post: (p: string, b: any = {}) => req("POST", p, b),
  patch: (p: string, b: any) => req("PATCH", p, b),
  fileUrl: (pid: string, path: string) => `${API}/projects/${pid}/file?path=${encodeURIComponent(path)}`,
};

export type Step = {
  run_id: string;
  step_id: string;
  description: string;
  route: string;
  tool: string;
  args: any;
  risk: "read" | "write" | "irreversible";
  produces: any;
  status: string;
  attempts: number;
  output: any;
  evidence: any;
  error: string | null;
  foreach?: string;
  approval_id?: string;
  started_at?: string;
  ended_at?: string;
};

export type Run = {
  run_id: string;
  thread_id: string;
  goal_masked: string;
  status: string;
  error?: string | null;
  plan: Step[];
  assessment?: any;
  privacy?: any;
  active?: boolean;
  created_at?: string;
};

export const PII_RE = /<(EMAIL|PHONE|AADHAAR|PAN|CARD|UPI|SECRET|PASSWORD)_\d+>/;
