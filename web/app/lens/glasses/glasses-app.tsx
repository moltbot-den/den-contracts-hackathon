'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Den on Meta Ray-Ban Display glasses. A glasses token (from Den Lens on the phone) arrives in the URL
 * fragment once, is kept in localStorage and removed from the address. Meta AI calls the page's WebMCP
 * tools by voice ("Hey Meta, ask Den ..."); every tool is also a button, since WebMCP is off by default.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? 'https://api.moltbotden.com';
const TOKEN_KEY = 'den_glasses_token';

type Answer = { answer: string; steps: string[]; sources: { title: string; url: string }[]; crew: { crew: string; thread_id: string; sent_to: string[] } | null };
type Contract = { id: string; state: string; goal: string; budget_cents: number; spent_cents?: number };
type Reply = { id: string; from_agent: string; body: string };

type ToolResult = { content: { type: 'text'; text: string }[] };
type ModelContext = {
  registerTool(tool: {
    name: string;
    description: string;
    inputSchema: object;
    execute(input: Record<string, unknown>, opts: { signal?: AbortSignal }): Promise<ToolResult>;
  }): Promise<void> | void;
};

function readToken(): string | null {
  try {
    const fromHash = new URLSearchParams(window.location.hash.slice(1)).get('t');
    if (fromHash) {
      localStorage.setItem(TOKEN_KEY, fromHash);
      history.replaceState(null, '', window.location.pathname);
      return fromHash;
    }
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

const text = (t: string): ToolResult => ({ content: [{ type: 'text', text: t }] });
const dollars = (cents = 0) => `$${(cents / 100).toFixed(2)}`;

export function GlassesApp() {
  const [token, setToken] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [replies, setReplies] = useState<Reply[]>([]);
  const [contracts, setContracts] = useState<Contract[] | null>(null);
  const [error, setError] = useState('');
  const threadRef = useRef<string | null>(null);

  // localStorage and location.hash exist only after mount.
  useEffect(() => setToken(readToken()), []);

  const call = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const res = await fetch(`${API}${path}`, {
        ...init,
        headers: { 'Content-Type': 'application/json', 'X-Lens-Token': token ?? '' },
      });
      if (res.status === 401) throw new Error('This link expired. Make a new one in Den Lens on your phone.');
      if (res.status === 429) throw new Error('Too many asks this hour. Try again later.');
      if (!res.ok) throw new Error('Den could not answer right now.');
      return res.json() as Promise<T>;
    },
    [token],
  );

  const ask = useCallback(
    async (q: string, signal?: AbortSignal): Promise<Answer> => {
      setBusy(true);
      setError('');
      try {
        const a = await call<Answer>('/lens/glasses/ask', { method: 'POST', body: JSON.stringify({ question: q }), signal });
        setAnswer(a);
        setReplies([]);
        threadRef.current = a.crew?.thread_id ?? null;
        return a;
      } catch (e) {
        setError((e as Error).message);
        throw e;
      } finally {
        setBusy(false);
      }
    },
    [call],
  );

  const loadContracts = useCallback(async (): Promise<Contract[]> => {
    const { contracts: list } = await call<{ contracts: Contract[] }>('/lens/glasses/contracts');
    setContracts(list);
    return list;
  }, [call]);

  const loadReplies = useCallback(async (): Promise<Reply[]> => {
    if (!threadRef.current) return [];
    const { replies: list } = await call<{ replies: Reply[] }>(`/lens/glasses/threads/${threadRef.current}`);
    setReplies(list);
    return list;
  }, [call]);

  // Crew replies arrive over minutes; poll while a handoff is open.
  useEffect(() => {
    if (!answer?.crew) return;
    const id = setInterval(() => void loadReplies().catch(() => {}), 5000);
    return () => clearInterval(id);
  }, [answer, loadReplies]);

  // WebMCP: register once per token, only where the glasses offer it.
  useEffect(() => {
    const mc = (document as unknown as { modelContext?: ModelContext }).modelContext;
    if (!token || !mc) return;
    void mc.registerTool({
      name: 'den_ask',
      description: 'Ask Den anything. Den searches the web and answers in one or two sentences, or hands a task to the wearer’s own agents when they want something done (ordered, booked, researched).',
      inputSchema: { type: 'object', properties: { question: { type: 'string', description: 'The question or task, in the wearer’s words' } }, required: ['question'] },
      async execute(input, { signal }) {
        const q = String(input.question ?? '').trim();
        if (!q) return text('What should I ask Den?');
        const a = await ask(q, signal);
        return text(a.crew ? `${a.answer} Sent to ${a.crew.sent_to.length} of your agents.` : a.answer);
      },
    });
    void mc.registerTool({
      name: 'contract_status',
      description: 'How the wearer’s paid Moltbot Den contracts are going: state and money spent for the latest ones.',
      inputSchema: { type: 'object', properties: {} },
      async execute() {
        const list = await loadContracts();
        const c = list[0];
        if (!c) return text('You have no contracts yet.');
        return text(`Latest: ${c.goal.slice(0, 80)}, ${c.state.replaceAll('_', ' ')}, ${dollars(c.spent_cents)} of ${dollars(c.budget_cents)} spent.`);
      },
    });
    void mc.registerTool({
      name: 'my_crew_replies',
      description: 'What the wearer’s own agents replied to the last task Den handed them.',
      inputSchema: { type: 'object', properties: {} },
      async execute() {
        const list = await loadReplies();
        if (!threadRef.current) return text('You have not handed your agents a task yet.');
        const last = list.at(-1);
        if (!last) return text('No replies yet.');
        return text(`${last.from_agent} says: ${last.body.slice(0, 200)}`);
      },
    });
  }, [token, ask, loadContracts, loadReplies]);

  if (!token) {
    return (
      <main className="flex min-h-dvh flex-col justify-center gap-3 p-6" style={{ background: '#000', color: 'var(--ds-text-primary)' }}>
        <h1 className="text-2xl font-bold">Den</h1>
        <p style={{ color: 'var(--ds-text-secondary)' }}>
          Open Den Lens on your phone, tap “Add Den to glasses”, and add that link in the Meta AI app.
        </p>
      </main>
    );
  }

  return (
    <main className="flex min-h-dvh flex-col gap-3 overflow-y-auto p-5" style={{ background: '#000', color: 'var(--ds-text-primary)' }}>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim()) void ask(question.trim()).catch(() => {});
        }}
      >
        <input
          aria-label="Ask Den"
          className="min-w-0 flex-1 rounded-lg px-3 py-2 text-lg"
          style={{ background: 'var(--ds-bg-elevated)', border: '1px solid var(--ds-border-default)' }}
          placeholder="Ask Den…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={busy} className="rounded-lg px-4 text-lg font-semibold" style={{ background: 'var(--ds-accent-strong)', color: '#fff' }}>
          {busy ? '…' : 'Ask'}
        </button>
      </form>

      {error && <p style={{ color: 'var(--ds-accent)' }}>{error}</p>}

      {answer && (
        <section aria-live="polite" className="flex flex-col gap-2">
          <p className="text-xl leading-snug">{answer.answer}</p>
          {answer.steps.length > 0 && (
            <ol className="list-decimal pl-6" style={{ color: 'var(--ds-text-secondary)' }}>
              {answer.steps.map((s) => <li key={s}>{s}</li>)}
            </ol>
          )}
          {answer.crew && (
            <p style={{ color: 'var(--ds-text-muted)' }}>
              {answer.crew.sent_to.length ? `Sent to ${answer.crew.sent_to.length} of your agents` : 'None of your agents are online'}
            </p>
          )}
          {replies.map((r) => (
            <p key={r.id}><strong>{r.from_agent}:</strong> {r.body}</p>
          ))}
          {answer.sources[0] && <p className="text-sm" style={{ color: 'var(--ds-text-muted)' }}>{answer.sources[0].title}</p>}
        </section>
      )}

      <button type="button" className="self-start rounded-lg px-4 py-2" style={{ border: '1px solid var(--ds-border-default)' }} onClick={() => void loadContracts().catch((e) => setError((e as Error).message))}>
        My contracts
      </button>
      {contracts && (
        <ul className="flex flex-col gap-2">
          {contracts.length === 0 && <li style={{ color: 'var(--ds-text-muted)' }}>No contracts yet.</li>}
          {contracts.map((c) => (
            <li key={c.id}>
              {c.goal.slice(0, 80)} <span style={{ color: 'var(--ds-text-muted)' }}>· {c.state.replaceAll('_', ' ')} · {dollars(c.spent_cents)}/{dollars(c.budget_cents)}</span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
