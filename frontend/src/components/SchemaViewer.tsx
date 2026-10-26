import { useEffect, useState, useTransition } from "react";
import type {
  ClassificationResultOut,
  ConnectionDetails,
  DiscoveredTableOut,
  SampleCellOut,
  SampleResultResponse,
} from "../lib/types";
import { api } from "../lib/api";

type Props = {
  tables: DiscoveredTableOut[];
  jobId: string | null;
  conn: ConnectionDetails;
  selectedDatabase: string | null;
};

type ClassifyState = { kind: "idle" } | { kind: "loading" } | { kind: "ready" } | { kind: "error"; message: string };

const ROW_COUNT = 5;
const MAX_BYTES = 512;

const SENSITIVE = new Set([
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

function isObjectType(t: DiscoveredTableOut["object_type"]): boolean {
  return t.toUpperCase() === "BASE TABLE";
}

function tableKey(schemaName: string, tableName: string): string {
  return `${schemaName}.${tableName}`;
}

function CellDisplay({ cell }: { cell: SampleCellOut | undefined }) {
  if (cell === undefined) {
    return <span className="text-mute">—</span>;
  }
  if (cell.value === null) {
    return <span className="text-mute">null</span>;
  }
  const truncatedSuffix = cell.truncated ? (
    <span className="text-flag">
      {" "}
      ⏷{cell.original_length ?? "?"}
    </span>
  ) : null;
  if (typeof cell.value === "string" && cell.value.length > 60) {
    return (
      <span className="text-ink">
        {cell.value.slice(0, 60)}…{truncatedSuffix}
      </span>
    );
  }
  return (
    <span className="text-ink">
      {String(cell.value)}
      {truncatedSuffix}
    </span>
  );
}

function sensitivityPill(s: string): string {
  if (SENSITIVE.has(s)) return "pill-sensitivity-sensitive";
  if (s === "Non Sensitive") return "pill-sensitivity-neutral";
  return "pill-sensitivity-unknown";
}

function ClassificationPanel({
  rows,
  state,
}: {
  rows: ClassificationResultOut[];
  state: ClassifyState;
}) {
  if (state.kind === "error") {
    return (
      <div className="hairline-t px-4 py-3 text-[10px] font-mono text-flag">
        {state.message}
      </div>
    );
  }
  if (state.kind === "loading" || state.kind === "idle") {
    return null;
  }
  if (rows.length === 0) {
    return (
      <div className="hairline-t px-4 py-3 text-[10px] font-mono text-mute">
        No classification results yet.
      </div>
    );
  }
  return (
    <div className="hairline-t">
      <div className="px-4 py-2 hairline-b flex items-center gap-3 text-[10px] font-mono uppercase tracking-wider text-mute">
        <span>classification</span>
        <span>
          <span className="text-ink">
            {rows.filter((r) => r.ok).length}
          </span>{" "}
          ok
        </span>
        <span>
          <span className="text-flag">
            {rows.filter((r) => !r.ok).length}
          </span>{" "}
          failed
        </span>
        <span className="ml-auto text-mute">sensitivity · confidence · strategy</span>
      </div>
      <table className="w-full font-mono text-xs">
        <thead>
          <tr className="text-left text-mute">
            <th className="px-4 py-2 font-normal">column</th>
            <th className="px-2 py-2 font-normal">sensitivity</th>
            <th className="px-2 py-2 font-normal w-20">conf</th>
            <th className="px-2 py-2 font-normal">strategy</th>
            <th className="px-2 py-2 font-normal">reasoning</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="hairline-t">
              <td className="px-4 py-2 text-ink">{r.column_name}</td>
              <td className="px-2 py-2">
                <span className={sensitivityPill(r.sensitivity)}>
                  {r.sensitivity}
                </span>
                {r.attempts > 1 ? (
                  <span className="ml-2 text-[9px] text-mute">
                    ×{r.attempts}
                  </span>
                ) : null}
              </td>
              <td className="px-2 py-2 text-ink">
                {(r.confidence * 100).toFixed(0)}%
              </td>
              <td className="px-2 py-2 text-ink">{r.suggested_strategy}</td>
              <td className="px-2 py-2 text-mute truncate max-w-[28rem]">
                {r.ok ? r.reasoning : `error: ${r.error}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SampleTable({ data }: { data: SampleResultResponse }) {
  return (
    <div className="hairline-t">
      <div className="px-4 py-2 hairline-b flex items-center gap-3 text-[10px] font-mono uppercase tracking-wider text-mute">
        <span>sample</span>
        <span>
          <span className="text-ink">{data.row_count}</span> row
          {data.row_count === 1 ? "" : "s"}
        </span>
        <span>
          cap <span className="text-ink">{data.max_bytes_per_value}</span>b
        </span>
        {data.cached ? <span className="text-accent">cached</span> : <span>fresh</span>}
        <span className="ml-auto text-mute">
          click ⏷ for original byte length
        </span>
      </div>
      <table className="w-full font-mono text-xs">
        <thead>
          <tr className="text-left text-mute">
            <th className="px-4 py-2 font-normal w-8">#</th>
            {data.sampled_columns.map((c) => (
              <th key={c} className="px-2 py-2 font-normal">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, i) => (
            <tr key={i} className="hairline-t">
              <td className="px-4 py-2 text-mute">{i + 1}</td>
              {data.sampled_columns.map((c) => (
                <td key={c} className="px-2 py-2 align-top max-w-[20rem]">
                  <CellDisplay cell={row[c]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableRow({
  table,
  classifications,
  classifyState,
  onClassify,
  onSample,
}: {
  table: DiscoveredTableOut;
  classifications: ClassificationResultOut[];
  classifyState: ClassifyState;
  onClassify: () => void;
  onSample: (e: React.MouseEvent) => void;
}) {
  const [open, setOpen] = useState(false);
  const fks = table.foreign_keys;
  const indexes = table.indexes.filter((i) => !i.is_primary);
  const uniqueIndexes = indexes.filter((i) => i.is_unique);
  const nonUniqueIndexes = indexes.filter((i) => !i.is_unique);
  const constraints = table.constraints;
  const fkColumnNames = new Set(fks.map((fk) => fk.column_name));
  const sampleable = isObjectType(table.object_type);

  return (
    <article className="hairline">
      <header
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-paper"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="font-mono text-[11px] text-mute w-4 text-center">
          {open ? "▾" : "▸"}
        </span>
        <span className="font-mono text-sm text-ink">
          <span className="text-mute">{table.schema_name}.</span>
          {table.table_name}
        </span>
        <span className="text-[10px] font-mono uppercase tracking-wider text-mute">
          {table.object_type}
        </span>
        <span className="ml-auto flex items-center gap-3 text-[11px] font-mono text-mute">
          <span>
            <span className="text-ink">{table.columns.length}</span> cols
          </span>
          {fks.length > 0 ? (
            <span>
              <span className="text-flag">{fks.length}</span> fk
            </span>
          ) : null}
          {indexes.length > 0 ? (
            <span>
              <span className="text-ink">{indexes.length}</span> idx
            </span>
          ) : null}
          {constraints.length > 0 ? (
            <span>
              <span className="text-ink">{constraints.length}</span> con
            </span>
          ) : null}
        </span>
      </header>

      {open ? (
        <div className="hairline-t">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal w-8">#</th>
                <th className="px-2 py-2 font-normal">column</th>
                <th className="px-2 py-2 font-normal">type</th>
                <th className="px-2 py-2 font-normal w-20">null</th>
                <th className="px-2 py-2 font-normal">flags</th>
                <th className="px-4 py-2 font-normal">default</th>
              </tr>
            </thead>
            <tbody>
              {table.columns.map((c) => (
                <tr key={c.name} className="hairline-t">
                  <td className="px-4 py-2 text-mute">{c.ordinal_position}</td>
                  <td className="px-2 py-2 text-ink">{c.name}</td>
                  <td className="px-2 py-2 text-ink">{formatType(c)}</td>
                  <td className="px-2 py-2">
                    {c.is_nullable ? (
                      <span className="pill-nullable">null</span>
                    ) : (
                      <span className="pill-not-null">not null</span>
                    )}
                  </td>
                  <td className="px-2 py-2 space-x-1">
                    {c.is_primary_key ? <span className="pill-pk">PK</span> : null}
                    {fkColumnNames.has(c.name) ? (
                      <span className="pill-fk">FK</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 text-mute truncate max-w-[14rem]">
                    {c.column_default ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {fks.length > 0 ? (
            <div className="hairline-t px-4 py-3">
              <p className="text-[10px] font-mono uppercase tracking-wider text-mute mb-2">
                foreign keys
              </p>
              <ul className="font-mono text-xs space-y-0.5">
                {fks.map((fk) => (
                  <li key={fk.fk_name} className="text-ink">
                    <span className="text-flag">{fk.fk_name}</span>
                    <span className="text-mute"> · </span>
                    {fk.column_name}
                    <span className="text-mute"> → </span>
                    {fk.referenced_schema}.{fk.referenced_table}.
                    {fk.referenced_column}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {indexes.length > 0 ? (
            <div className="hairline-t px-4 py-3">
              <p className="text-[10px] font-mono uppercase tracking-wider text-mute mb-2">
                indexes (non-primary)
              </p>
              <ul className="font-mono text-xs space-y-0.5">
                {uniqueIndexes.map((idx) => (
                  <li key={idx.name} className="text-ink">
                    <span className="text-accent">UQ</span>
                    <span className="text-mute"> · </span>
                    {idx.name}
                    <span className="text-mute"> (</span>
                    {idx.column_names.join(", ")}
                    <span className="text-mute">)</span>
                  </li>
                ))}
                {nonUniqueIndexes.map((idx) => (
                  <li key={idx.name} className="text-ink">
                    <span className="text-mute">IX</span>
                    <span className="text-mute"> · </span>
                    {idx.name}
                    <span className="text-mute"> (</span>
                    {idx.column_names.join(", ")}
                    <span className="text-mute">)</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {constraints.length > 0 ? (
            <div className="hairline-t px-4 py-3">
              <p className="text-[10px] font-mono uppercase tracking-wider text-mute mb-2">
                constraints
              </p>
              <ul className="font-mono text-xs space-y-0.5">
                {constraints.map((c) => (
                  <li key={c.name} className="text-ink">
                    <span className="text-ink">{c.constraint_type}</span>
                    <span className="text-mute"> · </span>
                    {c.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="hairline-t px-4 py-3 flex items-center gap-3 flex-wrap">
            <button
              type="button"
              onClick={onSample}
              disabled={!sampleable}
              className="btn btn-accent"
            >
              Sample {ROW_COUNT} rows
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onClassify();
              }}
              disabled={classifyState.kind === "loading"}
              className="btn"
            >
              {classifyState.kind === "loading"
                ? "Classifying…"
                : "Classify columns"}
            </button>
            <span className="text-[10px] font-mono text-mute">
              cap {MAX_BYTES} bytes · sensitivity · strategy
            </span>
          </div>

          <ClassificationPanel rows={classifications} state={classifyState} />
        </div>
      ) : null}
    </article>
  );
}

function formatType(col: DiscoveredTableOut["columns"][number]): string {
  if (col.character_maximum_length !== null) {
    if (col.character_maximum_length < 0) {
      return `${col.data_type}(max)`;
    }
    return `${col.data_type}(${col.character_maximum_length})`;
  }
  if (col.numeric_precision !== null && col.numeric_scale !== null) {
    return `${col.data_type}(${col.numeric_precision},${col.numeric_scale})`;
  }
  if (col.numeric_precision !== null) {
    return `${col.data_type}(${col.numeric_precision})`;
  }
  return col.data_type;
}

export function SchemaViewer({
  tables,
  jobId,
  conn,
  selectedDatabase,
}: Props) {
  const [samples, setSamples] = useState<Record<string, SampleResultResponse | { error: string }>>({});
  const [classifications, setClassifications] = useState<Record<string, ClassificationResultOut[]>>({});
  const [classifyStates, setClassifyStates] = useState<Record<string, ClassifyState>>({});
  const [classifyAllState, setClassifyAllState] = useState<"idle" | "loading" | "done">("idle");
  const [reportData, setReportData] = useState<{ total: number; sensitive: number; nonSensitive: number } | null>(null);
  const [, startTransition] = useTransition();

  // Reload existing classifications on mount / when jobId changes.
  useEffect(() => {
    if (!jobId) return;
    api
      .getJobClassifications(jobId)
      .then((res) => {
        const byTable: Record<string, ClassificationResultOut[]> = {};
        for (const r of res.results) {
          const key = tableKey(r.schema_name, r.table_name);
          if (!byTable[key]) byTable[key] = [];
          byTable[key].push(r);
        }
        startTransition(() => setClassifications(byTable));
      })
      .catch(() => {});
  }, [jobId]);

  if (tables.length === 0) {
    return (
      <div className="hairline p-8 text-center font-mono text-sm text-mute">
        {jobId
          ? "No tables discovered for this job."
          : "Run a discovery to see schema metadata."}
      </div>
    );
  }

  function handleSample(table_id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!selectedDatabase) return;
    api
      .sampleTable(table_id, {
        connection: { ...conn, database: selectedDatabase },
        row_count: ROW_COUNT,
        max_bytes_per_value: MAX_BYTES,
      })
      .then((res) => {
        startTransition(() => {
          setSamples((s) => ({ ...s, [table_id]: res }));
        });
      })
      .catch((err) => {
        startTransition(() => {
          setSamples((s) => ({ ...s, [table_id]: { error: (err as Error).message } }));
        });
      });
  }

  function handleClassify(table: DiscoveredTableOut) {
    const key = tableKey(table.schema_name, table.table_name);
    setClassifyStates((s) => ({ ...s, [key]: { kind: "loading" } }));
    api
      .classifyTable(table.id, 3)
      .then((res) => {
        startTransition(() => {
          setClassifications((prev) => ({
            ...Object.fromEntries(
              Object.entries(prev).filter(([k]) => k !== key),
            ),
            [key]: res.results,
          }));
          setClassifyStates((s) => ({ ...s, [key]: { kind: "ready" } }));
        });
      })
      .catch((err) => {
        startTransition(() => {
          setClassifyStates((s) => ({
            ...s,
            [key]: { kind: "error", message: (err as Error).message },
          }));
        });
      });
  }

  function handleClassifyAll() {
    if (!jobId) return;
    setClassifyAllState("loading");
    api
      .classifyJob(jobId, 3)
      .then((res) => {
        const byTable: Record<string, ClassificationResultOut[]> = {};
        for (const r of res.results) {
          const key = tableKey(r.schema_name, r.table_name);
          if (!byTable[key]) byTable[key] = [];
          byTable[key].push(r);
        }
        startTransition(() => {
          setClassifications(byTable);
          setClassifyAllState("done");
          const sensitive = res.results.filter((r) => r.sensitivity !== "Non Sensitive" && r.sensitivity !== "Unknown").length;
          setReportData({ total: res.results.length, sensitive, nonSensitive: res.results.length - sensitive });
        });
      })
      .catch(() => {
        setClassifyAllState("idle");
      });
  }

  return (
    <div className="space-y-2">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg leading-none text-ink">Schema</p>
        <p className="text-xs text-mute">
          {tables.length} table{tables.length === 1 ? "" : "s"} · job{" "}
          <span className="font-mono text-ink">{jobId?.slice(0, 8) ?? "—"}</span>
        </p>
        <div className="ml-auto flex items-center gap-2">
          {reportData ? (
            <span className="text-[10px] font-mono text-mute">
              {reportData.total} columns · {reportData.sensitive} sensitive · {reportData.nonSensitive} non-sensitive
            </span>
          ) : null}
          <button
            type="button"
            className="btn-accent text-[11px]"
            disabled={!jobId || classifyAllState === "loading"}
            onClick={handleClassifyAll}
          >
            {classifyAllState === "loading" ? "Classifying..." : classifyAllState === "done" ? "Classify Again" : "Classify All"}
          </button>
          <p className="text-[10px] font-mono uppercase tracking-wider text-mute">
            click a row to expand
          </p>
        </div>
      </header>
      {tables.map((t) => {
        const key = tableKey(t.schema_name, t.table_name);
        const sample = samples[t.id];
        const classifyState = classifyStates[key] ?? { kind: "idle" };
        return (
          <div key={t.id}>
            <TableRowWithSample
              table={t}
              classifications={classifications[key] ?? []}
              classifyState={classifyState}
              onClassify={() => handleClassify(t)}
              onSample={(e) => handleSample(t.id, e)}
            />
            {sample && "row_count" in sample ? (
              <div className="ml-4 mr-4">
                <SampleTable data={sample} />
              </div>
            ) : sample && "error" in sample ? (
              <div className="ml-4 mr-4 hairline p-3 text-[11px] font-mono text-flag">
                Sampling error: {(sample as { error: string }).error}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function TableRowWithSample(props: {
  table: DiscoveredTableOut;
  classifications: ClassificationResultOut[];
  classifyState: ClassifyState;
  onClassify: () => void;
  onSample: (e: React.MouseEvent) => void;
}) {
  return <TableRow {...props} />;
}
