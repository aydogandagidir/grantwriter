// Shared types between @bluedev/web and the FastAPI backend.
// Mirror of the Pydantic response models in apps/api/src/api/routes/*.py.
// When the API changes, update both halves at once — these are the
// canonical TypeScript names the web consumes via @bluedev/shared-types.

// ── Tenants & roles ─────────────────────────────────────────────────────

export type UserRole = 'owner' | 'admin' | 'member' | 'viewer';
export type PlanName = 'starter' | 'pro' | 'enterprise';

// ── /me ──────────────────────────────────────────────────────────────────

export interface MeResponse {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: UserRole;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  plan: PlanName;
}

// ── Members ──────────────────────────────────────────────────────────────

export interface MemberSummary {
  id: string;
  email: string | null;
  display_name: string | null;
  role: UserRole;
  is_self: boolean;
  joined_at: string;
}

export interface MemberListResponse {
  members: MemberSummary[];
}

// ── Invitations ──────────────────────────────────────────────────────────

export interface InvitationCreated {
  id: string;
  email: string;
  role: UserRole;
  token: string;
  expires_at: string;
  accept_url_path: string;
}

export interface InvitationSummary {
  id: string;
  email: string;
  role: UserRole;
  invited_by: string | null;
  expires_at: string;
  created_at: string;
}

export interface InvitationListResponse {
  invitations: InvitationSummary[];
}

export interface InvitationPreview {
  tenant_name: string;
  tenant_slug: string;
  role: UserRole;
  invited_email: string;
  inviter_display_name: string | null;
  expires_at: string;
}

export interface InvitationAcceptedResponse {
  tenant_id: string;
  role: UserRole;
}

// ── Audit log ───────────────────────────────────────────────────────────

export interface AuditEvent {
  id: string;
  action: string;
  user_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  diff: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditListResponse {
  events: AuditEvent[];
  has_more: boolean;
}

// ── Usage report ────────────────────────────────────────────────────────

export interface UsagePeriodTotal {
  period_start: string;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  call_count: number;
}

export interface UsageReport {
  tenant_id: string;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  call_count: number;
  byok_call_count: number;
  by_period: UsagePeriodTotal[];
}

// ── LLM config (BYOK) ──────────────────────────────────────────────────

export interface LlmConfigSummary {
  anthropic_configured: boolean;
  openai_configured: boolean;
  updated_at: string | null;
}

export interface LlmConfigTestResponse {
  ok: boolean;
  model_used: string | null;
  message: string;
}

// ── Billing ─────────────────────────────────────────────────────────────

export interface BillingStatus {
  plan: PlanName;
  monthly_proposal_limit: number;
  proposals_this_month: number;
  has_active_subscription: boolean;
  subscription_reference: string | null;
}

export interface CheckoutResponse {
  payment_page_url: string;
  token: string;
}

// ── Versions ────────────────────────────────────────────────────────────

export interface VersionSummary {
  id: string;
  version_number: number;
  created_by: string | null;
  comment: string | null;
  created_at: string;
}

export interface VersionListResponse {
  versions: VersionSummary[];
}

// ── Comments ────────────────────────────────────────────────────────────

export interface CommentRecord {
  id: string;
  author_id: string;
  section: string | null;
  anchor: string | null;
  content: string;
  resolved: boolean;
  parent_id: string | null;
  created_at: string;
}

export interface CommentListResponse {
  comments: CommentRecord[];
}

// ── Validation (compliance + hallucination hunter) ──────────────────────

export type ValidationSeverity = 'blocker' | 'warning' | 'info';
export type HuntRecommendation = 'block_export' | 'ok';

export interface ValidationIssue {
  severity: ValidationSeverity;
  section: string | null;
  code: string;
  message_tr: string;
  message_en: string;
  suggestion: string | null;
}

export interface ComplianceReport {
  passed: boolean;
  issues: ValidationIssue[];
  ai_disclosure_text: string | null;
  compliance_score: number;
}

export interface FlaggedCitation {
  raw_text: string;
  section: string;
  status: string;
  source: string | null;
  match_score: number | null;
  warning: string | null;
}

export interface HuntReport {
  total_citations: number;
  verified: number;
  partial_match: number;
  fabricated: number;
  not_found: number;
  errors: number;
  verification_rate: number;
  flagged_citations: FlaggedCitation[];
  recommendation: HuntRecommendation;
  claim_check_pass_rate: number | null;
}

/**
 * Combined response of `POST /api/v1/proposals/{id}/validate`.
 * S3.D13.T1 — the hallucination hunter's recommendation = "block_export"
 * (or any compliance blocker) disables the export button on the FE.
 */
export interface ValidationReport {
  compliance: ComplianceReport;
  hallucination_hunter: HuntReport | null;
}
