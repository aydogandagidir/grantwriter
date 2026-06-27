// Shared types between @bluedev/web and the FastAPI backend.
// Mirror of the Pydantic response models in apps/api/src/api/routes/*.py.
// When the API changes, update both halves at once — these are the
// canonical TypeScript names the web consumes via @bluedev/shared-types.

// ── Tenants & roles ─────────────────────────────────────────────────────

export type UserRole = 'owner' | 'admin' | 'member' | 'viewer';
export type PlanName = 'starter' | 'pro' | 'enterprise';

// ── Provenance ──────────────────────────────────────────────────────────

export type ProvenanceSource =
  | 'human'
  | 'ai-generated'
  | 'ai-edited'
  | 'imported'
  | 'rag-retrieved';

export interface ProvenanceSentence {
  sentence_id: string;
  section: string;
  content: string;
  source: ProvenanceSource;
  agent_id?: string | null;
  llm_model?: string | null;
  llm_tokens?: number | null;
  source_citations?: string[] | null;
}

export interface ProvenanceBatchRequest {
  sentences: ProvenanceSentence[];
}

export interface ProvenanceBatchResponse {
  upserted: number;
}

export interface ProvenanceSourceCount {
  source: string;
  count: number;
}

export interface ProvenanceStatsResponse {
  total: number;
  per_source: ProvenanceSourceCount[];
  per_agent: ProvenanceSourceCount[];
  per_model: ProvenanceSourceCount[];
}

export interface ProvenanceItem {
  sentence_id: string;
  section: string;
  content: string;
  source: string;
  agent_id: string | null;
  llm_model: string | null;
  llm_tokens: number | null;
  created_at: string;
}

export interface ProvenanceListResponse {
  items: ProvenanceItem[];
  next_offset: number | null;
}

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

// ── Programmes (catalog) ────────────────────────────────────────────────

export type ProgrammeLanguage = 'tr' | 'en' | 'both';

export interface ProgrammeSummary {
  id: string;
  name_tr: string;
  name_en: string;
  funder: string;
  language: ProgrammeLanguage;
  description_tr: string | null;
  description_en: string | null;
  active: boolean;
}

export interface ProgrammeListResponse {
  programmes: ProgrammeSummary[];
}

// ── Calls (open-call catalog) ───────────────────────────────────────────

export type CallSource =
  | 'eu_ft_portal'
  | 'nlnet'
  | 'cascade'
  | 'tubitak'
  | 'kosgeb'
  | 'manual';

export type CallStatus = 'open' | 'closing_soon' | 'closed' | 'draft';

export interface CallSummary {
  id: string;
  programme_id: string;
  agency_id: string | null;
  source: CallSource;
  external_id: string;
  title: string;
  language: string;
  status: CallStatus;
  deadline: string | null;
  opening_at: string | null;
  call_url: string | null;
  topic_keywords: string[];
  sectors: string[];
  geo_scope: string[];
  eligibility_tags: string[];
  budget_per_project_min_eur: number | null;
  budget_per_project_max_eur: number | null;
  trl_min: number | null;
  trl_max: number | null;
  funding_rate_pct: number | null;
  partner_consortium_required: boolean | null;
  scraped_at: string;
}

export interface CallListResponse {
  calls: CallSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** Detail-page payload — everything the API surfaces on one call. */
export interface CallDetail extends CallSummary {
  scope_summary: string | null;
  call_text: string | null;
  call_pdf_url: string | null;
  application_form_url: string | null;
  work_programme_pdf_url: string | null;
  source_url_canonical: string | null;
  budget_total_eur: number | null;
  eligibility_summary: Record<string, unknown>;
  raw_metadata: Record<string, unknown>;
  historical_acceptance_rate: number | null;
  last_seen_at: string;
}

export type CallSortKey = 'deadline' | 'budget' | 'relevance' | 'recency';

/** Query-string params accepted by GET /api/v1/calls. */
export interface CallSearchFilters {
  q?: string;
  programme_ids?: string[];
  agency_ids?: string[];
  source?: CallSource;
  status_filter?: CallStatus;
  deadline_after?: string;
  deadline_before?: string;
  budget_min_eur?: number;
  budget_max_eur?: number;
  trl_min?: number;
  trl_max?: number;
  sectors?: string[];
  eligibility_tags?: string[];
  geo_scope?: string[];
  language?: 'tr' | 'en';
  sort?: CallSortKey;
  limit?: number;
  offset?: number;
}

// ── Project ideas + bidirectional matching (Faz 2) ───────────────────────

export type IdeaStatus = 'draft' | 'active' | 'archived';
export type IdeaSource = 'user_input' | 'generated_from_call' | 'imported';

export interface IdeaCreate {
  title: string;
  abstract: string;
  technology_angle?: string;
  target_market?: string;
  trl_estimate?: number;
  budget_estimate_eur_min?: number;
  budget_estimate_eur_max?: number;
  team_size_estimate?: number;
  sectors?: string[];
  keywords?: string[];
  source?: IdeaSource;
  seed_call_id?: string;
}

export interface IdeaSummary {
  id: string;
  title: string;
  abstract: string;
  technology_angle: string | null;
  target_market: string | null;
  trl_estimate: number | null;
  budget_estimate_eur_min: number | null;
  budget_estimate_eur_max: number | null;
  team_size_estimate: number | null;
  sectors: string[];
  keywords: string[];
  distinctiveness_score: number | null;
  status: IdeaStatus;
  source: IdeaSource;
  seed_call_id: string | null;
  created_at: string;
}

export interface IdeaListResponse {
  ideas: IdeaSummary[];
  total: number;
}

/** One ranked call in an idea-match response, with score breakdown. */
export interface CallMatchOut {
  call_id: string;
  total_score: number;
  semantic_score: number;
  keyword_overlap_score: number;
  sector_score: number;
  trl_fit_score: number;
  budget_fit_score: number;
  rationale_tr: string;
  rationale_en: string;
  identified_gaps: string[];
  call_title: string | null;
  programme_id: string | null;
  deadline: string | null;
}

export interface IdeaMatchResponse {
  idea_id: string;
  matches: CallMatchOut[];
  filter_stats: Record<string, number>;
  computed_at: string;
  model_version: string;
}

// ── Organization profile (Faz 2) ─────────────────────────────────────────

export type EntityType =
  | 'individual'
  | 'sme'
  | 'university'
  | 'large_corp'
  | 'ngo'
  | 'research_org';

export interface OrganizationProfileUpsert {
  legal_name?: string;
  entity_type?: EntityType;
  country?: string;
  nuts_region?: string;
  nace_codes?: string[];
  sectors?: string[];
  team_size?: number;
  annual_revenue_eur?: number;
  founded_year?: number;
  technology_areas?: string[];
  trl_current?: number;
  trl_target?: number;
  expertise_keywords?: string[];
  past_projects?: Record<string, unknown>[];
  funding_history?: Record<string, unknown>[];
  preferred_languages?: string[];
}

export interface OrganizationProfile {
  tenant_id: string;
  legal_name: string | null;
  entity_type: EntityType | null;
  country: string | null;
  nuts_region: string | null;
  nace_codes: string[];
  sectors: string[];
  team_size: number | null;
  annual_revenue_eur: number | null;
  founded_year: number | null;
  technology_areas: string[];
  trl_current: number | null;
  trl_target: number | null;
  expertise_keywords: string[];
  past_projects: Record<string, unknown>[];
  funding_history: Record<string, unknown>[];
  preferred_languages: string[];
  created_at: string;
  updated_at: string;
}

export interface CallCreate {
  programme_id: string;
  external_id: string;
  title: string;
  language: 'tr' | 'en';
  source?: CallSource;
  call_text?: string;
  call_url?: string;
  call_pdf_url?: string;
  deadline?: string;
  budget_total_eur?: number;
  budget_per_project_min_eur?: number;
  budget_per_project_max_eur?: number;
  trl_min?: number;
  trl_max?: number;
  topic_keywords?: string[];
  raw_metadata?: Record<string, unknown>;
}

// ── Proposals (CRUD) ────────────────────────────────────────────────────

export type ProposalStatus =
  | 'draft'
  | 'brief_complete'
  | 'generating'
  | 'draft_complete'
  | 'in_review'
  | 'validated'
  | 'exported'
  | 'submitted'
  | 'funded'
  | 'rejected'
  | 'archived';

/**
 * Status values the HTTP caller may set via PATCH. Saga-managed states
 * (`generating`, `draft_complete`, `validated`) are intentionally
 * excluded — those flip server-side as the pipeline progresses.
 */
export type ProposalStatusPatchable =
  | 'draft'
  | 'brief_complete'
  | 'in_review'
  | 'archived';

export interface ProposalSummary {
  id: string;
  programme_id: string;
  language: string;
  title: string | null;
  acronym: string | null;
  status: ProposalStatus;
  call_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
  word_count: number;
  llm_cost_usd: number;
}

export interface ProposalListResponse {
  proposals: ProposalSummary[];
}

export interface ProposalDetail {
  id: string;
  tenant_id: string;
  programme_id: string;
  language: string;
  title: string | null;
  acronym: string | null;
  status: ProposalStatus;
  call_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
  brief: Record<string, unknown>;
  draft: Record<string, unknown>;
  compliance_report: Record<string, unknown>;
  distinctiveness_score: number | null;
  ai_disclosure_text: string | null;
  word_count: number;
  page_count: number;
  llm_cost_usd: number;
}

export interface ProposalCreate {
  programme_id: string;
  language: 'tr' | 'en';
  title?: string;
  acronym?: string;
  call_id?: string;
  brief?: Record<string, unknown>;
}

export interface ProposalUpdate {
  title?: string | null;
  acronym?: string | null;
  call_id?: string | null;
  brief?: Record<string, unknown>;
  draft?: Record<string, unknown>;
  status?: ProposalStatusPatchable;
}

/**
 * Body of POST /proposals/{id}/generate response. The job_id maps to
 * a Celery task; the FE polls `/api/v1/jobs/{job_id}` or subscribes
 * to the SSE stream URL for progress.
 */
export interface GenerateEnqueued {
  job_id: string;
  proposal_id: string;
  estimated_duration_seconds: number;
  status_url: string;
  stream_url: string;
}

/**
 * Body of POST /proposals/{id}/export response.
 */
export interface ExportEnqueued {
  job_id: string;
  status: string;
  proposal_id: string;
  format: 'docx' | 'xlsx';
}
