import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ApprovalOut, ConnectionDetails, PendingItem } from "../lib/types";

const STRATEGIES = [
  "Fake Name",
  "Fake Email",
  "Masked Phone",
  "Hash",
  "Tokenization",
  "Random Replacement",
  "Preserve Format",
  "Null",
  "Custom Rule",
  "LLM Anonymize",
] as const;

type Props = { jobId: string | null; conn: ConnectionDetails };

function sensitivityPill(s: string): string {
  const sensitive = new Set([
    "Name",
    "Email",
    "Phone",
    "Address",
    "National ID",
    "Passport",
    "IBAN",
    "Credit Card",
    "Salary",
    "Birth Date",
    "Company Name",
  ]);
  if (sensitive.has(s)) return "pill-sensitivity-sensitive";
  if (s === "Non Sensitive") return "pill-sensitivity-neutral";
  return "pill-sensitivity-unknown";
}

function Row({
  item,
  onSubmit,
}: {
  item: PendingItem;
  onSubmit: (req: {
    classification_id: string;
    decision: "approve" | "reject" | "override";
    masking_strategy?: string | null;
    comment?: string | null;
    excluded?: boolean;
  }) => Promise<void>;
}) {
  const [strategy, setStrategy] = useState<string>(item.suggested_strategy);
  const [comment, setComment] = useState<string>("");
  const [busy, setBusy] = useState<"approve" | "override" | "reject" | "exclude" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function act(
    kind: "approve" | "override" | "reject" | "exclude",
  ) {
    setBusy(kind);
    setError(null);
    try {
      await onSubmit({
        classification_id: item.classification_id,
        decision: kind === "exclude" ? "reject" : kind,
        masking_strategy: kind === "override" ? strategy : null,
        comment: comment || null,
        excluded: kind === "exclude",
      });
      setDone(kind);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (done) {
    return (
      <tr className="hairline-t">
        <td colSpan={6} className="px-4 py-2 text-[11px] font-mono text-mute">
          <span className="text-accent">&#10003; {done}</span> &middot; {item.schema_name}.
          {item.table_name}.{item.column_name}
        </td>
      </tr>
    );
  }

  return (
    <tr className="hairline-t">
      <td className="px-4 py-2 text-ink">
        {item.schema_name}.{item.table_name}.
        <span className="font-bold">{item.column_name}</span>
        <span className="ml-2 text-[10px] font-mono text-mute">{item.data_type}</span>
      </td>
      <td className="px-2 py-2">
        <span className={sensitivityPill(item.sensitivity)}>
          {item.sensitivity}
        </span>
        <span className="ml-2 text-[10px] text-mute font-mono">
          {(item.confidence * 100).toFixed(0)}%
        </span>
      </td>
      <td className="px-2 py-2">
        <select
          className="input"
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
        >
          {STRATEGIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </td>
      <td className="px-2 py-2 text-[11px] text-mute truncate max-w-[18rem]">
        {item.reasoning}
      </td>
      <td className="px-2 py-2">
        <input
          type="text"
          placeholder="optional"
          className="input"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </td>
      <td className="px-2 py-2 text-right space-x-1 whitespace-nowrap">
        <button
          type="button"
          className="btn-accent"
          disabled={busy !== null}
          onClick={() => act("approve")}
        >
          {busy === "approve" ? "..." : "Approve"}
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy !== null}
          onClick={() => act("override")}
        >
          {busy === "override" ? "..." : "Override"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={busy !== null}
          onClick={() => act("reject")}
        >
          {busy === "reject" ? "..." : "Reject"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={busy !== null}
          onClick={() => act("exclude")}
        >
          {busy === "exclude" ? "..." : "Exclude"}
        </button>
        {error ? (
          <div className="mt-1 text-[10px] font-mono text-flag">{error}</div>
        ) : null}
      </td>
    </tr>
  );
}

export function ApprovalCenter({ jobId, conn }: Props) {
  const [items, setItems] = useState<PendingItem[]>([]);
  const [decided, setDecided] = useState<ApprovalOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [showDecided, setShowDecided] = useState<boolean>(false);

  async function refresh() {
    if (!jobId) return;
    setBusy(true);
    try {
      const [pending, approvals] = await Promise.all([
        api.listPending(jobId),
        api.listApprovals(jobId),
      ]);
      setItems(pending.items);
      setDecided(approvals);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  if (!jobId) {
    return (
      <div className="hairline p-6 text-center text-mute font-mono text-sm">
        Run a discovery + classification first.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Approval Center</p>
        <p className="text-xs text-mute">
          {items.length} pending &middot; job <span className="font-mono">{jobId.slice(0, 8)}</span>
        </p>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={busy}>
            {busy ? "..." : "Refresh"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setShowDecided(!showDecided)}
          >
            {showDecided ? "Hide" : "Show"} Decided ({decided.length})
          </button>
        </div>
      </header>
      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {items.length === 0 ? (
        <div className="hairline p-6 text-center text-mute font-mono text-sm">
          Nothing pending. Approve / reject rows above the line to populate.
        </div>
      ) : (
        <div className="hairline">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">column</th>
                <th className="px-2 py-2 font-normal">sensitivity</th>
                <th className="px-2 py-2 font-normal">strategy</th>
                <th className="px-2 py-2 font-normal">reasoning</th>
                <th className="px-2 py-2 font-normal">comment</th>
                <th className="px-2 py-2 font-normal text-right">decision</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <Row
                  key={it.classification_id}
                  item={it}
                  onSubmit={async (req) => {
                    await api.submitApproval(req);
                    await refresh();
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {showDecided && decided.length > 0 && (
        <div className="hairline">
          <p className="px-4 py-2 text-[11px] font-mono text-mute hairline-b">
            Previously decided ({decided.length})
          </p>
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">column</th>
                <th className="px-2 py-2 font-normal">decision</th>
                <th className="px-2 py-2 font-normal">strategy</th>
                <th className="px-2 py-2 font-normal">when</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((d) => (
                <tr key={d.id} className="hairline-t">
                  <td className="px-4 py-2 text-ink">{d.classification_id.slice(0, 8)}...</td>
                  <td className="px-2 py-2">
                    <span className={d.decision === "approve" ? "text-accent" : d.decision === "reject" ? "text-flag" : "text-warn"}>
                      {d.decision}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-mute">{d.masking_strategy || "-"}</td>
                  <td className="px-2 py-2 text-mute">{new Date(d.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] font-mono text-mute">
        {conn.host}:{conn.port}/{conn.database} &middot; Approver identity comes from your login token.
      </p>
    </div>
  );
}
