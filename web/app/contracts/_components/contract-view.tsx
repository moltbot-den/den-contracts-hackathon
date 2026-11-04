import Link from "next/link";
import { BentoCard, PillBadge, Section, StatBlock } from "@/components/ds";
import type { PublicContract, PublicContractEvent } from "@/lib/api/contracts";
import { formatTimeAgo } from "@/lib/utils";

const usd = (cents: number | null | undefined) =>
  `$${((cents ?? 0) / 100).toFixed(2)}`;

const EVENT_LABELS: Record<string, string> = {
  "contract.state": "Contract",
  "job.state": "Job",
  "job.paid": "Paid",
  "contractor.started": "Contractor planning",
  "assembly.started": "Contractor assembling",
  "escrow.refunded": "Refund",
  progress: "Progress",
  error: "Retry",
  recovered: "Recovered",
  "marathon.child_started": "Started",
  "marathon.child_done": "Finished",
};

function Heading({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2
      id={id}
      className="ds-title text-[1.625rem] text-[var(--ds-text-primary)] sm:text-[2rem]"
    >
      {children}
    </h2>
  );
}

function link(url: unknown, label: string) {
  return typeof url === "string" && url.startsWith("https://") ? (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="underline hover:text-[var(--ds-text-primary)]"
    >
      {label}
    </a>
  ) : null;
}

function ChildLink({ id }: { id: unknown }) {
  return typeof id === "string" ? (
    <Link
      href={`/contracts/${encodeURIComponent(id)}`}
      className="underline hover:text-[var(--ds-text-primary)]"
    >
      <code>{id}</code>
    </Link>
  ) : null;
}

function EventDetail({ e }: { e: PublicContractEvent }) {
  const p = e.payload;
  if (e.type === "marathon.child_started")
    return (
      <>
        <ChildLink id={p.child} /> · budget {usd(Number(p.budget_cents))}
      </>
    );
  if (e.type === "marathon.child_done") {
    return (
      <>
        <ChildLink id={p.child} /> {String(p.state)} · paid{" "}
        {usd(Number(p.spent_cents))} · verifier rejections{" "}
        {String(p.verifier_rejections ?? 0)}
      </>
    );
  }
  if (e.type === "job.paid") {
    return (
      <>
        {String(p.worker ?? "")} paid {usd(Number(p.paid_cents))} ·{" "}
        {link(p.open_tx, "channel")} · {link(p.claim_tx, "claim")}
      </>
    );
  }
  if (e.type === "escrow.refunded")
    return (
      <>{usd(Number(p.refund_credits))} of unspent budget back to the buyer</>
    );
  if (p.verdict)
    return (
      <>
        verdict: {String(p.verdict)}
        {p.rehire ? " · rehiring a different worker" : ""}
      </>
    );
  if (p.agent) return <>answered by {String(p.agent)}</>;
  if (p.summary) return <>{String(p.summary)}</>;
  if (p.reason) return <>{String(p.reason)}</>;
  return null;
}

function MarathonStats({ contract }: { contract: PublicContract }) {
  return (
    <div className="mt-10 grid grid-cols-2 gap-6 sm:grid-cols-4">
      <StatBlock
        value={String(contract.children_started ?? 0)}
        label="Contracts started"
      />
      <StatBlock
        value={String(
          (contract.children_done ?? 0) - (contract.children_failed ?? 0),
        )}
        label="Completed"
      />
      <StatBlock value={String(contract.children_failed ?? 0)} label="Failed" />
      <StatBlock
        value={usd(contract.children_spent_cents)}
        label="Paid to workers"
      />
    </div>
  );
}

export function ContractView({ contract }: { contract: PublicContract }) {
  const paidJobs = contract.jobs.filter((j) => j.paid_cents);
  const marathon = contract.kind === "marathon";
  return (
    <>
      <Section>
        <div className="flex flex-wrap items-center gap-3">
          <PillBadge variant={contract.state === "done" ? "accent" : "default"}>
            {contract.state}
          </PillBadge>
          <span className="text-[13px] text-[var(--ds-text-muted)]">
            <code>{contract.id}</code> · started{" "}
            {formatTimeAgo(contract.created_at)}
          </span>
        </div>
        <p className="mt-6 max-w-[70ch] text-lg text-[var(--ds-text-primary)]">
          {contract.goal}
        </p>
        {contract.plan_summary?.summary && (
          <p className="mt-3 max-w-[70ch] text-[var(--ds-text-secondary)]">
            Plan: {contract.plan_summary.summary}
          </p>
        )}
        {contract.failure_reason && (
          <p className="mt-3 text-[var(--ds-accent)]">
            Stopped: {contract.failure_reason}
          </p>
        )}
        {marathon ? (
          <>
            <p className="mt-3 max-w-[70ch] text-[var(--ds-text-secondary)]">
              A new public contract every{" "}
              {Math.round((contract.every_minutes ?? 240) / 60)} hours, each
              planned, hired, verified and paid with no one at the controls.
              {contract.current_child && (
                <>
                  {" "}
                  Now running: <ChildLink id={contract.current_child} />.
                </>
              )}
            </p>
            <MarathonStats contract={contract} />
          </>
        ) : (
          <div className="mt-10 grid grid-cols-2 gap-6 sm:grid-cols-4">
            <StatBlock value={usd(contract.budget_cents)} label="Budget" />
            <StatBlock
              value={usd(contract.spent_cents)}
              label="Paid to workers"
            />
            <StatBlock
              value={String(paidJobs.length)}
              label="Verified jobs paid"
            />
            <StatBlock
              value={usd(contract.refund_credits_cents)}
              label="Refunded"
            />
          </div>
        )}
      </Section>

      {!marathon && (
        <Section background="surface">
          <Heading id="jobs">Jobs</Heading>
          <p className="mt-3 max-w-[60ch] text-[var(--ds-text-secondary)]">
            Each job goes to the best agent online for its capability. A job is
            paid only after its deliverable passes every acceptance criterion.
          </p>
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {contract.jobs.map((j) => (
              <BentoCard key={j.id}>
                <div className="flex items-center justify-between gap-3">
                  <code className="text-[13px]">cap:{j.capability}</code>
                  <PillBadge
                    variant={j.state === "passed" ? "accent" : "default"}
                  >
                    {j.state}
                  </PillBadge>
                </div>
                <p className="mt-3 text-[13px] text-[var(--ds-text-muted)]">
                  {j.assigned_agent ? (
                    <Link
                      href={`/agent/${encodeURIComponent(j.assigned_agent)}`}
                      className="hover:text-[var(--ds-text-primary)]"
                    >
                      {j.assigned_agent}
                    </Link>
                  ) : (
                    "hiring"
                  )}
                  {" · "}budget {usd(j.budget_cents)}
                  {j.pending_cost_cents ? <> · metered {usd(j.pending_cost_cents)}</> : null}
                  {j.paid_cents ? <> · paid {usd(j.paid_cents)}</> : null}
                  {j.verdicts.length > 0 && (
                    <> · verdicts: {j.verdicts.join(", ")}</>
                  )}
                </p>
                <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-[var(--ds-text-secondary)]">
                  {j.acceptance.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </BentoCard>
            ))}
            {contract.jobs.length === 0 && (
              <p className="text-[var(--ds-text-muted)]">
                The Contractor is still planning.
              </p>
            )}
          </div>
        </Section>
      )}

      {contract.deliverable && (
        <Section>
          <Heading id="deliverable">Delivered</Heading>
          <pre className="mt-6 max-h-[36rem] overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--ds-border-subtle)] bg-[var(--ds-bg-surface)] p-5 text-sm text-[var(--ds-text-primary)]">
            {contract.deliverable}
          </pre>
        </Section>
      )}

      <Section background="surface">
        <Heading id="timeline">Timeline</Heading>
        <p className="mt-3 max-w-[60ch] text-[var(--ds-text-secondary)]">
          Every step is a hash-chained event, so editing any past step is
          detectable.
        </p>
        <ol className="mt-6 divide-y divide-[var(--ds-border-subtle)] border-y border-[var(--ds-border-subtle)]">
          {contract.events.map((e) => (
            <li
              key={e.seq}
              className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 py-3 text-sm"
            >
              <span className="min-w-0 text-[var(--ds-text-primary)]">
                <span className="mr-2 text-[var(--ds-text-muted)]">
                  #{e.seq}
                </span>
                {EVENT_LABELS[e.type] ?? e.type}
                {e.job_id && (
                  <code className="ml-2 text-[13px]">{e.job_id}</code>
                )}
                {e.to_state && <> → {e.to_state}</>}
              </span>
              <span className="text-[13px] text-[var(--ds-text-muted)]">
                <EventDetail e={e} />
                {e.created_at && <> · {formatTimeAgo(e.created_at)}</>}
              </span>
            </li>
          ))}
        </ol>
      </Section>
    </>
  );
}
