import { useEffect, useState } from "react";

type Status = "idle" | "live" | "stale" | "error";

type Props = {
  status: Status;
  lastQueryLabel?: string;
  lastQueryAt?: number;
  target: string;
};

function formatRelative(at: number | undefined): string {
  if (!at) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export function LiveWire({ status, lastQueryLabel, lastQueryAt, target }: Props) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const dot =
    status === "live"
      ? "bg-accent shadow-[0_0_0_3px_rgba(14,165,164,0.15)] animate-pulse"
      : status === "stale"
        ? "bg-flag"
        : status === "error"
          ? "bg-flag"
          : "bg-mute";

  return (
    <div className="hairline-b bg-card/60 backdrop-blur">
      <div className="mx-auto max-w-7xl px-6 py-2 flex items-center gap-3 font-mono text-[11px] text-mute">
        <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden />
        <span className="uppercase tracking-wider text-ink">wire</span>
        <span className="text-mute">·</span>
        <span>target: <span className="text-ink">{target}</span></span>
        <span className="text-mute">·</span>
        <span>
          last query:{" "}
          <span className="text-ink">
            {lastQueryLabel ?? "—"} ({formatRelative(lastQueryAt)})
          </span>
        </span>
        <span className="ml-auto uppercase tracking-wider">{status}</span>
      </div>
    </div>
  );
}
