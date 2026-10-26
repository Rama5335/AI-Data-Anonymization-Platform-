export type ConnectionDetails = {
  host: string;
  port: number;
  auth_type: "sql" | "windows";
  username?: string | null;
  password?: string | null;
  database?: string | null;
  driver?: string;
  encrypt?: boolean;
  trust_server_certificate?: boolean;
  timeout_seconds?: number;
};

export type ObjectInfo = {
  schema_name: string;
  name: string;
  object_type: string;
};

export type DiscoveredColumnOut = {
  id: string;
  name: string;
  ordinal_position: number;
  data_type: string;
  is_nullable: boolean;
  column_default: string | null;
  character_maximum_length: number | null;
  numeric_precision: number | null;
  numeric_scale: number | null;
  is_primary_key: boolean;
  is_foreign_key: boolean;
};

export type DiscoveredForeignKeyOut = {
  fk_name: string;
  column_name: string;
  referenced_schema: string;
  referenced_table: string;
  referenced_column: string;
};

export type DiscoveredIndexOut = {
  name: string;
  is_unique: boolean;
  is_primary: boolean;
  column_names: string[];
};

export type DiscoveredConstraintOut = {
  name: string;
  constraint_type: string;
};

export type DiscoveredTableOut = {
  id: string;
  schema_name: string;
  table_name: string;
  object_type: string;
  discovered_at: string;
  columns: DiscoveredColumnOut[];
  foreign_keys: DiscoveredForeignKeyOut[];
  indexes: DiscoveredIndexOut[];
  constraints: DiscoveredConstraintOut[];
};

export type JobSchemaResponse = {
  job_id: string;
  tables: DiscoveredTableOut[];
};

export type SchemaDiscoveryResponse = {
  job_id: string;
  table_count: number;
  column_count: number;
  foreign_key_count: number;
  index_count: number;
  constraint_count: number;
  cached: boolean;
};

export type SampleCellOut = {
  value: string | number | boolean | null;
  truncated: boolean;
  original_length: number | null;
};

export type SampleTableRequest = {
  connection: ConnectionDetails;
  row_count?: number;
  max_bytes_per_value?: number;
  excluded_column_names?: string[];
  include_views?: boolean;
};

export type SampleResultResponse = {
  job_id: string;
  table_id: string;
  schema_name: string;
  table_name: string;
  row_count: number;
  sampled_columns: string[];
  max_bytes_per_value: number;
  rows: Array<Record<string, SampleCellOut>>;
  cached: boolean;
};

export type ClassificationResultOut = {
  id: string;
  job_id: string;
  schema_name: string;
  table_name: string;
  column_name: string;
  sensitivity: string;
  confidence: number;
  reasoning: string;
  suggested_strategy: string;
  attempts: number;
  ok: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type ClassificationRunResponse = {
  job_id: string;
  results: ClassificationResultOut[];
  classified_count: number;
  failed_count: number;
};

export type JobClassificationResponse = {
  job_id: string;
  results: ClassificationResultOut[];
};

export type PendingItem = {
  classification_id: string;
  schema_name: string;
  table_name: string;
  column_name: string;
  data_type: string;
  sensitivity: string;
  confidence: number;
  suggested_strategy: string;
  attempts: number;
  reasoning: string;
};

export type ApprovalDecisionRequest = {
  classification_id: string;
  decision: "approve" | "reject" | "override";
  masking_strategy?: string | null;
  comment?: string | null;
  excluded?: boolean;
};

export type ApprovalOut = {
  id: string;
  classification_id: string;
  approved_by_user_id: string;
  decision: string;
  masking_strategy: string | null;
  comment: string | null;
  excluded: boolean;
  created_at: string;
  updated_at: string;
};

export type BackupResult = {
  database: string;
  backup_path: string;
  size_bytes: number;
  verified: boolean;
  created_at: string;
  kept_after: number;
};

export type BackupRecord = {
  backup_path: string;
  database: string;
  size_bytes: number;
  verified: boolean;
  created_at: string;
  job_id: string | null;
};

export type BackupList = {
  backup_dir: string;
  files: string[];
  records: BackupRecord[];
};

export type UpdatePlan = {
  table: string;
  column: string;
  row_count_estimate: number;
  batches: number;
  dry_run_sql: string;
};

export type UpdateResult = {
  table: string;
  column: string;
  rows_updated: number;
  batches: number;
  duration_ms: number;
  audit_log_id: string;
};

export type AuditRow = {
  id: string;
  occurred_at: string;
  user_id: string | null;
  job_id: string | null;
  event_type: string;
  outcome: string;
  details: Record<string, unknown>;
};

export type AuditList = {
  items: AuditRow[];
};

export type WorkflowState = {
  job_id: string;
  phase: string;
  updated_at: string;
  started_at: string;
  is_stale: boolean;
  notes: string | null;
};

export type AuthUser = {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
};

export type LoginRequest = {
  username: string;
  password: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
};

export type CreateUserRequest = {
  username: string;
  password: string;
  role: "user" | "admin";
};

export type Server = {
  id: string;
  name: string;
  host: string;
  port: number;
  auth_type: "sql" | "windows";
  created_at: string;
  updated_at: string;
};

export type CreateServerRequest = {
  name: string;
  host: string;
  port: number;
  auth_type: "sql" | "windows";
};

export type ProbeServerRequest = {
  username: string;
  password: string;
  database?: string | null;
};

export type ProbeServerResult = {
  server_id: string;
  host: string;
  port: number;
  reachable: boolean;
  databases: string[] | null;
  error: string | null;
};

export type ClassificationReport = {
  job_id: string;
  totals: {
    tables: number;
    columns: number;
    sensitive_columns: number;
    failed_columns: number;
    pending_columns: number;
  };
  sensitivity_counts: Record<string, number>;
  strategy_counts: Record<string, number>;
  tables: Array<{
    schema_name: string;
    table_name: string;
    sensitive_columns: number;
    columns: Array<{
      name: string;
      data_type: string;
      sensitivity: string;
      confidence: number;
      suggested_strategy: string;
      attempts: number;
      ok: boolean;
      error: string | null;
      reasoning: string;
    }>;
  }>;
};
