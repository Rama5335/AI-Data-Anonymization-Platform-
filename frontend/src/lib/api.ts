import type {
  ApprovalDecisionRequest,
  ApprovalOut,
  AuditList,
  BackupList,
  BackupResult,
  ClassificationReport,
  ClassificationRunResponse,
  ConnectionDetails,
  CreateServerRequest,
  CreateUserRequest,
  DiscoveredTableOut,
  JobClassificationResponse,
  JobSchemaResponse,
  LoginRequest,
  LoginResponse,
  ObjectInfo,
  PendingItem,
  ProbeServerRequest,
  ProbeServerResult,
  SampleResultResponse,
  SampleTableRequest,
  SchemaDiscoveryResponse,
  Server,
  UpdatePlan,
  UpdateResult,
  WorkflowState,
} from "./types";

// All API requests go through the Nginx proxy.
const API_BASE = "/api";

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// JWT is kept in memory only.
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

function authHeaders(): Record<string, string> {
  return authToken ? { authorization: `Bearer ${authToken}` } : {};
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });

  if (resp.status === 401) {
    throw new AuthRequiredError(
      `${resp.status} ${resp.statusText}: ${await resp.text()}`,
    );
  }

  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
  }

  return (await resp.json()) as T;
}

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    headers: authHeaders(),
  });

  if (resp.status === 401) {
    throw new AuthRequiredError(
      `${resp.status} ${resp.statusText}: ${await resp.text()}`,
    );
  }

  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}: ${await resp.text()}`);
  }

  return (await resp.json()) as T;
}

export class AuthRequiredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthRequiredError";
  }
}

export const api = {
  health: () => getJson<{ status: string }>("/health"),

  login: (req: LoginRequest) =>
    postJson<LoginResponse>("/auth/login", req),

  me: () => getJson<LoginResponse["user"]>("/users/me"),

  createUser: (req: CreateUserRequest) =>
    postJson<LoginResponse["user"]>("/users", req),

  getPhases: () => getJson<Record<string, string[]>>("/workflow/phases"),

  listServers: () => getJson<Server[]>("/servers"),

  createServer: (req: CreateServerRequest) =>
    postJson<Server>("/servers", req),

  updateServer: (id: string, req: Partial<CreateServerRequest>) =>
    fetch(apiUrl(`/servers/${id}`), {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify(req),
    }).then(async (r) => {
      if (!r.ok) {
        throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
      }
      return (await r.json()) as Server;
    }),

  deleteServer: (id: string) =>
    fetch(apiUrl(`/servers/${id}`), {
      method: "DELETE",
      headers: authHeaders(),
    }).then((r) => {
      if (!r.ok && r.status !== 204) {
        throw new Error(`${r.status} ${r.statusText}`);
      }
    }),

  probeServer: (id: string, req: ProbeServerRequest) =>
    postJson<ProbeServerResult>(`/servers/${id}/probe`, req),

  testConnection: (c: ConnectionDetails) =>
    postJson<{ success: boolean }>("/discovery/test-connection", c),

  listDatabases: (c: ConnectionDetails) =>
    postJson<{ databases: string[] }>("/discovery/databases", c),

  listSchemas: (c: ConnectionDetails) =>
    postJson<{ schemas: string[] }>("/discovery/schemas", c),

  listObjects: (c: ConnectionDetails, object_type: "TABLE" | "VIEW") =>
    postJson<{ objects: ObjectInfo[] }>(
      `/discovery/objects?object_type=${object_type}`,
      c,
    ),

  discoverSchema: (c: ConnectionDetails) =>
    postJson<SchemaDiscoveryResponse>("/schema/discover", {
      connection: c,
    }),

  getJobSchema: (job_id: string) =>
    getJson<JobSchemaResponse>(`/schema/job/${job_id}`),

  sampleTable: (table_id: string, req: SampleTableRequest) =>
    postJson<SampleResultResponse>(`/sampling/tables/${table_id}`, req),

  classifyColumn: (column_id: string, max_retries = 3) =>
    postJson<ClassificationRunResponse>(
      `/classification/columns/${column_id}`,
      { max_retries },
    ),

  classifyTable: (table_id: string, max_retries = 3) =>
    postJson<ClassificationRunResponse>(
      `/classification/tables/${table_id}`,
      { max_retries },
    ),

  classifyJob: (job_id: string, max_retries = 3) =>
    postJson<ClassificationRunResponse>(
      `/classification/jobs/${job_id}`,
      { max_retries },
    ),

  getJobClassifications: (job_id: string) =>
    getJson<JobClassificationResponse>(`/classification/jobs/${job_id}`),

  getJobReport: (job_id: string, format: "json" | "md" = "json") =>
    getJson<ClassificationReport | { job_id: string; markdown: string }>(
      `/classification/jobs/${job_id}/report?format=${format}`,
    ),

  listPending: (job_id: string) =>
    getJson<{ job_id: string; items: PendingItem[] }>(
      `/approvals/jobs/${job_id}/pending`,
    ),

  listApprovals: (job_id: string) =>
    getJson<ApprovalOut[]>(`/approvals/jobs/${job_id}`),

  submitApproval: (req: ApprovalDecisionRequest) =>
    postJson<ApprovalOut>("/approvals", req),

  runBackup: (
    connection: ConnectionDetails,
    database: string | null,
    keep: number | null = null,
  ) =>
    postJson<BackupResult>("/backups", {
      connection,
      database,
      keep,
    }),

  listBackups: (database: string | null = null) => {
    const q = database ? `?database=${encodeURIComponent(database)}` : "";
    return getJson<BackupList>(`/backups${q}`);
  },

  planUpdate: (job_id: string, connection: ConnectionDetails) =>
    postJson<{ job_id: string; plans: UpdatePlan[] }>("/updates/plan", {
      job_id,
      connection,
    }),

  applyUpdate: (req: {
    job_id: string;
    connection: ConnectionDetails;
    expected_backup_path: string;
    confirm: boolean;
    dry_run: boolean;
  }) =>
    postJson<{
      job_id: string;
      results: UpdateResult[];
      dry_run: boolean;
    }>("/updates/apply", req),

  prepareMasking: (job_id: string) =>
    postJson<{ job_id: string; status: string }>(
      "/masking/prepare",
      { job_id },
    ),

  listAudit: (limit: number = 100) =>
    getJson<AuditList>(`/audit?limit=${limit}`),

  listAuditForJob: (job_id: string, limit: number = 100) =>
    getJson<AuditList>(`/audit/jobs/${job_id}?limit=${limit}`),

  getWorkflowState: (job_id: string) =>
    getJson<WorkflowState>(`/workflow/jobs/${job_id}`),

  transitionWorkflow: (
    job_id: string,
    to_phase: string,
    note: string | null = null,
  ) =>
    postJson<{ ok: boolean; state: WorkflowState }>(
      `/workflow/jobs/${job_id}/transition`,
      { to_phase, note },
    ),

  getSettings: () =>
    getJson<{
      llm_provider: string;
      ollama_model: string;
      ollama_base_url: string;
      openai_model: string;
      openai_base_url: string;
      backup_dir: string;
      backup_keep: number;
      safe_update_batch_size: number;
      credential_encryption_key_set: boolean;
    }>("/settings"),

  rollback: (req: {
    connection: ConnectionDetails;
    backup_path: string;
    database: string;
  }) =>
    postJson<{
      success: boolean;
      database: string;
      backup_path: string;
      message: string;
    }>("/updates/rollback", req),
};

export type { ConnectionDetails, DiscoveredTableOut }