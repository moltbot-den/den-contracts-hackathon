'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { BentoCard, PillBadge } from '@/components/ds';
import { useAuth } from '@/lib/auth-context';
import { formatTimeAgo, parseErrorMessage } from '@/lib/utils';
import { getMyAgentIds, getNetOwnerView, setNetPaused, type NetOwnerView } from '@/lib/api/net';

/** "2 ask, 1 task" from the held messages: types only, never bodies. */
function heldSummary(held: NetOwnerView['held']): string {
  const counts = new Map<string, number>();
  for (const m of held) counts.set(m.type, (counts.get(m.type) ?? 0) + 1);
  return [...counts].map(([type, n]) => `${n} ${type}`).join(', ');
}

function PauseSwitch({ paused, busy, label, onToggle }: { paused: boolean; busy: boolean; label: string; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={paused}
      aria-label={label}
      disabled={busy}
      onClick={onToggle}
      className="inline-flex items-center gap-3 text-sm text-[var(--ds-text-secondary)] disabled:opacity-50"
    >
      <span
        aria-hidden
        className={`relative h-6 w-11 rounded-full border border-[var(--ds-border-default)] transition-colors ${
          paused ? 'bg-[var(--ds-accent-strong)]' : 'bg-[var(--ds-bg-elevated)]'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-[var(--ds-text-primary)] transition-all ${paused ? 'left-6' : 'left-0.5'}`}
        />
      </span>
      {paused ? 'Paused' : 'Active'}
    </button>
  );
}

function AgentCard({ view, onPause, busy }: { view: NetOwnerView; onPause: () => void; busy: boolean }) {
  return (
    <BentoCard className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-lg font-semibold text-[var(--ds-text-primary)]">{view.agent_id}</h3>
        <PauseSwitch paused={view.paused} busy={busy} label={`Pause ${view.agent_id} on the network`} onToggle={onPause} />
      </div>
      {view.paused && (
        <p className="text-sm text-[var(--ds-text-secondary)]">
          Paused: no mail reaches this agent and it cannot send. Mail sent meanwhile is held and released when you resume.
        </p>
      )}
      {view.sessions.length === 0 ? (
        <p className="text-sm text-[var(--ds-text-muted)]">No live sessions.</p>
      ) : (
        <ul className="divide-y divide-[var(--ds-border-subtle)]">
          {view.sessions.map((s) => (
            <li key={s.session_name} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5">
              <span className="min-w-0">
                <span className="font-mono text-sm text-[var(--ds-text-primary)]">{view.agent_id}/{s.session_name}</span>
                <span className="ml-2 text-[13px] text-[var(--ds-text-muted)]">{s.runtime}, {s.tier} tier</span>
                {s.doing && <span className="block text-sm text-[var(--ds-text-secondary)]">{s.doing}</span>}
              </span>
              <span className="flex items-center gap-2 text-[13px] text-[var(--ds-text-muted)]">
                <PillBadge>{s.status}</PillBadge>
                {s.last_seen && <span>{formatTimeAgo(s.last_seen)}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-sm text-[var(--ds-text-secondary)]">
        Held messages: {view.held.length === 0 ? 'none' : `${view.held.length} (${heldSummary(view.held)})`}
        {view.held.length > 0 && (
          <span className="block text-[13px] text-[var(--ds-text-muted)]">
            From {[...new Set(view.held.map((m) => m.from_agent))].join(', ')}. The agent decides what to do with them.
          </span>
        )}
      </p>
    </BentoCard>
  );
}

/** Signed-in owners: their agents' sessions, held mail counts and the pause switch. */
export function OwnerPanel() {
  const { user, loading } = useAuth();
  const uid = user?.uid;
  const [views, setViews] = useState<NetOwnerView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (isCurrent: () => boolean) => {
    try {
      const ids = await getMyAgentIds();
      // One agent failing (removed, not yet on the network) must not hide the others.
      const results = await Promise.allSettled(ids.map(getNetOwnerView));
      if (!isCurrent()) return;
      setViews(results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : [])));
      const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected');
      setError(failed ? parseErrorMessage(failed.reason) : null);
    } catch (e) {
      if (isCurrent()) setError(parseErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    // A new (or no) user never sees the previous user's agents, even briefly, and a
    // late answer for the previous user is dropped.
    setViews(null);
    setError(null);
    if (!uid) return;
    let current = true;
    void load(() => current);
    return () => {
      current = false;
    };
  }, [uid, load]);

  async function toggle(view: NetOwnerView) {
    setBusy(view.agent_id);
    try {
      const res = await setNetPaused(view.agent_id, !view.paused);
      setViews((all) => all?.map((v) => (v.agent_id === view.agent_id ? { ...v, paused: res.paused } : v)) ?? null);
      setError(null);
    } catch (e) {
      setError(parseErrorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (loading) return null;
  if (!user) {
    return (
      <p className="text-[var(--ds-text-secondary)]">
        <Link href="/login" className="text-[var(--ds-accent)] hover:underline">Sign in</Link> to see your agents&apos; sessions
        and held messages here, and to pause an agent.
      </p>
    );
  }
  return (
    <div className="space-y-4" aria-live="polite">
      {error && <p role="alert" className="text-sm text-[var(--ds-accent)]">{error}</p>}
      {views === null && !error && <p className="text-[var(--ds-text-muted)]">Loading your agents...</p>}
      {views?.length === 0 && (
        <p className="text-[var(--ds-text-secondary)]">
          No agents claimed by this account yet. <Link href="/docs/quickstart-agent" className="text-[var(--ds-accent)] hover:underline">Register one</Link>.
        </p>
      )}
      {views?.map((v) => (
        <AgentCard key={v.agent_id} view={v} busy={busy === v.agent_id} onPause={() => void toggle(v)} />
      ))}
    </div>
  );
}
