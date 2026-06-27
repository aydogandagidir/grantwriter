/**
 * Centralised TanStack Query keys + fetch hooks.
 *
 * Every key is a tuple — first the resource family, then narrowing
 * params. This convention keeps `queryClient.invalidateQueries` calls
 * targeted (e.g. after creating an invitation we invalidate
 * `['invitations']` without touching `['members']`).
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type {
  AuditListResponse,
  BillingStatus,
  CallCreate,
  CallDetail,
  CallListResponse,
  CallSearchFilters,
  CheckoutResponse,
  CommentListResponse,
  CommentRecord,
  ExportEnqueued,
  GenerateEnqueued,
  IdeaCreate,
  IdeaListResponse,
  IdeaMatchResponse,
  IdeaSummary,
  InvitationCreated,
  InvitationListResponse,
  InvitationPreview,
  LlmConfigSummary,
  LlmConfigTestResponse,
  MeResponse,
  MemberListResponse,
  OrganizationProfile,
  OrganizationProfileUpsert,
  ProgrammeListResponse,
  ProposalCreate,
  ProposalDetail,
  ProposalListResponse,
  ProposalUpdate,
  UsageReport,
  ValidationReport,
  VersionListResponse,
  ProvenanceBatchRequest,
  ProvenanceBatchResponse,
  ProvenanceListResponse,
  ProvenanceStatsResponse,
} from '@bluedev/shared-types';

/** Response shape of POST /api/v1/calls/{id}/generate-ideas. */
export interface GenerateIdeasResponse {
  call_id: string;
  ideas: Array<{
    title: string;
    abstract: string;
    technology_angle: string;
    impact_thesis: string;
    est_budget_eur_min: number | null;
    est_budget_eur_max: number | null;
    est_trl: number | null;
    suggested_consortium_type: string;
    alignment_score: number;
    distinctiveness_score: number | null;
  }>;
  generated_at: string;
  generator_version: string;
  from_cache: boolean;
}

/** Slash-command name accepted by POST /proposals/{id}/inline-edit. */
export type InlineCommand =
  | 'rewrite'
  | 'shorter'
  | 'longer'
  | 'translate_en'
  | 'translate_tr';

/** Request body for POST /api/v1/proposals/{id}/inline-edit. */
export interface InlineEditRequest {
  command: InlineCommand;
  section: 'excellence' | 'impact' | 'implementation' | 'other';
  selection_text: string;
  context_before?: string;
  context_after?: string;
}

/** Response shape of POST /api/v1/proposals/{id}/inline-edit. */
export interface InlineEditResponse {
  replacement_text: string;
  command: InlineCommand;
  model: string;
  cost_usd: number;
  tokens_used: number;
}

/** Response shape of GET /api/v1/calls/{id}/eligibility. */
export interface EligibilityReport {
  verdict: 'ELIGIBLE' | 'CONDITIONAL' | 'NOT_ELIGIBLE';
  checks: Array<{
    rule: string;
    status: 'pass' | 'warn' | 'fail';
    message_tr: string;
    message_en: string;
  }>;
  blockers: string[];
  warnings: string[];
  confidence: number;
  model_version: string;
  checked_at: string;
}

import { apiClient } from '@/lib/api/client';

// ── Query keys ──────────────────────────────────────────────────────────

export const queryKeys = {
  me: ['me'] as const,
  members: ['members'] as const,
  invitations: ['invitations'] as const,
  invitationPreview: (token: string) => ['invitations', 'preview', token] as const,
  audit: (filter?: { action?: string; limit?: number; before?: string }) =>
    ['audit', filter ?? {}] as const,
  usage: ['usage'] as const,
  llmConfig: ['llm-config'] as const,
  billing: ['billing'] as const,
  versions: (proposalId: string) => ['versions', proposalId] as const,
  comments: (proposalId: string, opts?: { includeResolved?: boolean }) =>
    ['comments', proposalId, opts ?? {}] as const,
  validation: (proposalId: string) => ['validation', proposalId] as const,
  programmes: ['programmes'] as const,
  calls: (filters?: CallSearchFilters) =>
    ['calls', filters ? normalizeCallFilters(filters) : 'all'] as const,
  callDetail: (id: string) => ['calls', 'detail', id] as const,
  callEligibility: (id: string) => ['calls', 'eligibility', id] as const,
  ideas: ['ideas'] as const,
  ideaDetail: (id: string) => ['ideas', 'detail', id] as const,
  ideaMatches: (id: string) => ['ideas', 'matches', id] as const,
  organizationProfile: ['organization', 'profile'] as const,
  proposalList: ['proposals'] as const,
  proposal: (id: string) => ['proposals', id] as const,
  job: (id: string) => ['jobs', id] as const,
};

// ── /me ─────────────────────────────────────────────────────────────────

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => apiClient<MeResponse>('/api/v1/me'),
    staleTime: 60_000,
  });
}

// ── Provenance ──────────────────────────────────────────────────────────

export function useUpsertProvenance(proposalId: string) {
  return useMutation({
    mutationFn: (body: ProvenanceBatchRequest) =>
      apiClient<ProvenanceBatchResponse>(
        `/api/v1/proposals/${proposalId}/provenance`,
        { method: 'POST', body },
      ),
  });
}

export function useProvenanceStats(proposalId: string) {
  return useQuery({
    queryKey: ['provenance', 'stats', proposalId] as const,
    queryFn: () =>
      apiClient<ProvenanceStatsResponse>(
        `/api/v1/proposals/${proposalId}/provenance/stats`,
      ),
    enabled: Boolean(proposalId),
  });
}

/**
 * Fetch the saga-written sentence rows for a proposal so the editor
 * can re-attach provenance marks atop the persisted draft markdown
 * when the user opens the page.
 *
 * Pagination is server-driven via ``next_offset``; callers that need
 * the full list can fold over the response in a follow-up.
 */
export function useProvenanceItems(
  proposalId: string,
  options?: { section?: string; limit?: number },
) {
  const section = options?.section;
  const limit = options?.limit;
  return useQuery({
    queryKey: [
      'provenance',
      'items',
      proposalId,
      section ?? null,
      limit ?? null,
    ] as const,
    queryFn: () => {
      const params = new URLSearchParams();
      if (section) params.set('section', section);
      if (limit !== undefined) params.set('limit', String(limit));
      const qs = params.toString();
      const suffix = qs.length > 0 ? `?${qs}` : '';
      return apiClient<ProvenanceListResponse>(
        `/api/v1/proposals/${proposalId}/provenance${suffix}`,
      );
    },
    enabled: Boolean(proposalId),
  });
}

// ── Members ─────────────────────────────────────────────────────────────

export function useMembers() {
  return useQuery({
    queryKey: queryKeys.members,
    queryFn: () => apiClient<MemberListResponse>('/api/v1/tenant/members'),
  });
}

export function useUpdateMemberRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ memberId, role }: { memberId: string; role: string }) =>
      apiClient(`/api/v1/tenant/members/${memberId}/role`, {
        method: 'PATCH',
        body: { role },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.members }),
  });
}

export function useRemoveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (memberId: string) =>
      apiClient(`/api/v1/tenant/members/${memberId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.members }),
  });
}

// ── Invitations ────────────────────────────────────────────────────────

export function useInvitations() {
  return useQuery({
    queryKey: queryKeys.invitations,
    queryFn: () => apiClient<InvitationListResponse>('/api/v1/tenant/invitations'),
  });
}

export function useCreateInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { email: string; role: 'member' | 'admin' }) =>
      apiClient<InvitationCreated>('/api/v1/tenant/invitations', {
        method: 'POST',
        body: input,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) =>
      apiClient(`/api/v1/tenant/invitations/${invitationId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.invitations }),
  });
}

export function useInvitationPreview(token: string) {
  return useQuery({
    queryKey: queryKeys.invitationPreview(token),
    queryFn: () => apiClient<InvitationPreview>(`/api/v1/invitations/${token}`),
    retry: false,
    enabled: Boolean(token),
  });
}

export function useAcceptInvitation() {
  return useMutation({
    mutationFn: (token: string) =>
      apiClient('/api/v1/invitations/accept', {
        method: 'POST',
        body: { token },
      }),
  });
}

// ── Audit log ───────────────────────────────────────────────────────────

export function useAuditLog(filter: { action?: string; limit?: number; before?: string } = {}) {
  return useQuery({
    queryKey: queryKeys.audit(filter),
    queryFn: () =>
      apiClient<AuditListResponse>('/api/v1/tenant/audit-log', {
        searchParams: filter,
      }),
  });
}

// ── Usage ──────────────────────────────────────────────────────────────

export function useUsage() {
  return useQuery({
    queryKey: queryKeys.usage,
    queryFn: () => apiClient<UsageReport>('/api/v1/tenant/usage'),
  });
}

// ── LLM config (BYOK) ──────────────────────────────────────────────────

export function useLlmConfig() {
  return useQuery({
    queryKey: queryKeys.llmConfig,
    queryFn: () => apiClient<LlmConfigSummary>('/api/v1/tenant/llm-config'),
  });
}

export function useUpdateLlmConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { anthropic_api_key?: string | null; openai_api_key?: string | null }) =>
      apiClient<LlmConfigSummary>('/api/v1/tenant/llm-config', {
        method: 'PUT',
        body: input,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.llmConfig }),
  });
}

export function useTestLlmConfig() {
  return useMutation({
    mutationFn: (provider: 'anthropic' | 'openai') =>
      apiClient<LlmConfigTestResponse>('/api/v1/tenant/llm-config/test', {
        method: 'POST',
        body: { provider },
      }),
  });
}

// ── Billing ────────────────────────────────────────────────────────────

export function useBilling() {
  return useQuery({
    queryKey: queryKeys.billing,
    queryFn: () => apiClient<BillingStatus>('/api/v1/billing/status'),
  });
}

export function useCheckout() {
  return useMutation({
    mutationFn: (plan_reference_code: string) =>
      apiClient<CheckoutResponse>('/api/v1/billing/checkout', {
        method: 'POST',
        body: { plan_reference_code },
      }),
  });
}

export function useCancelSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient('/api/v1/billing/subscription', { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.billing }),
  });
}

// ── Versions ────────────────────────────────────────────────────────────

export function useVersions(proposalId: string) {
  return useQuery({
    queryKey: queryKeys.versions(proposalId),
    queryFn: () =>
      apiClient<VersionListResponse>(`/api/v1/proposals/${proposalId}/versions`),
    enabled: Boolean(proposalId),
  });
}

export function useCreateVersion(proposalId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (comment?: string) =>
      apiClient(`/api/v1/proposals/${proposalId}/versions`, {
        method: 'POST',
        body: { comment },
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: queryKeys.versions(proposalId) }),
  });
}

export function useRestoreVersion(proposalId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (versionNumber: number) =>
      apiClient(
        `/api/v1/proposals/${proposalId}/versions/${versionNumber}/restore`,
        { method: 'POST' },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: queryKeys.versions(proposalId) }),
  });
}

// ── Comments ───────────────────────────────────────────────────────────

export function useComments(proposalId: string, opts: { includeResolved?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.comments(proposalId, opts),
    queryFn: () =>
      apiClient<CommentListResponse>(`/api/v1/proposals/${proposalId}/comments`, {
        searchParams: { include_resolved: opts.includeResolved ? 'true' : undefined },
      }),
    enabled: Boolean(proposalId),
  });
}

export function useCreateComment(proposalId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      content: string;
      section?: string;
      anchor?: string;
      parent_id?: string;
    }) =>
      apiClient<CommentRecord>(`/api/v1/proposals/${proposalId}/comments`, {
        method: 'POST',
        body: input,
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ['comments', proposalId] }),
  });
}

export function useUpdateComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      apiClient(`/api/v1/comments/${id}`, { method: 'PATCH', body: { content } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['comments'] }),
  });
}

export function useResolveComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient(`/api/v1/comments/${id}/resolve`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['comments'] }),
  });
}

export function useDeleteComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient(`/api/v1/comments/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['comments'] }),
  });
}

// ── Validation (compliance + hallucination hunter) ──────────────────────

/**
 * Run `POST /proposals/{id}/validate` and cache the resulting
 * `ValidationReport`. The hook is mutation-shaped (vs. useQuery) because
 * the underlying endpoint is rate-limited (10 / 60s) and the FE only
 * fires it from an explicit "Re-validate" button click — not on render.
 *
 * On success the cache is primed for `queryKeys.validation(proposalId)`
 * so any component (badge, export button, issues panel) reads the same
 * shape without re-firing the request.
 */
export function useValidateProposal(proposalId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient<ValidationReport>(`/api/v1/proposals/${proposalId}/validate`, {
        method: 'POST',
      }),
    onSuccess: (data) =>
      qc.setQueryData(queryKeys.validation(proposalId), data),
  });
}

/**
 * Slash-command inline editor rewrite. Hits
 * `POST /api/v1/proposals/{id}/inline-edit`. Mutation-shaped because the
 * editor fires it from explicit user action (selection + slash menu pick)
 * and the underlying endpoint is rate-limited (10 / 60s) — never on
 * render.
 *
 * The hook does NOT touch the proposal cache: the editor is the source
 * of truth for the live document and persists explicitly via save. We
 * just deliver the replacement text + cost/model so the editor can
 * tag the new content with provenance.
 */
export function useInlineEdit(proposalId: string) {
  return useMutation({
    mutationFn: (body: InlineEditRequest) =>
      apiClient<InlineEditResponse>(
        `/api/v1/proposals/${proposalId}/inline-edit`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
      ),
  });
}

/**
 * Read the cached validation report without firing a request. Returns
 * `undefined` until `useValidateProposal` has run at least once.
 */
export function useCachedValidation(proposalId: string) {
  const qc = useQueryClient();
  return qc.getQueryData<ValidationReport>(queryKeys.validation(proposalId));
}

// ── Programmes catalog ──────────────────────────────────────────────────

export function useProgrammes() {
  return useQuery({
    queryKey: queryKeys.programmes,
    queryFn: () => apiClient<ProgrammeListResponse>('/api/v1/programmes'),
    // The catalog is small and changes once per quarter; keep it warm
    // across page transitions.
    staleTime: 5 * 60 * 1000,
  });
}

// ── Calls catalog ───────────────────────────────────────────────────────

/**
 * Normalise filters into a stable query-key payload + searchParams.
 *
 * Two arrays with the same members but different ordering must produce
 * the same cache key, otherwise `useCalls` would re-fetch every time the
 * user toggles a chip in the same set. We sort each array and drop
 * empties so `{ sectors: ['J62','C29'] }` and `{ sectors: ['C29','J62'] }`
 * cache-hit each other.
 */
function normalizeCallFilters(filters: CallSearchFilters): CallSearchFilters {
  const out: CallSearchFilters = {};
  for (const key of Object.keys(filters) as (keyof CallSearchFilters)[]) {
    const value = filters[key];
    if (value === undefined || value === null) continue;
    if (typeof value === 'string' && value.trim() === '') continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      (out as Record<string, unknown>)[key] = [...value].sort();
    } else {
      (out as Record<string, unknown>)[key] = value;
    }
  }
  return out;
}

function callFiltersToSearchParams(
  filters: CallSearchFilters,
): Record<string, string | number | string[] | undefined> {
  // apiClient's URLSearchParams builder accepts plain strings + arrays.
  // Drop fields the API treats as "not specified".
  const params: Record<string, string | number | string[] | undefined> = {};
  if (filters.q?.trim()) params.q = filters.q.trim();
  if (filters.programme_ids?.length) params.programme_ids = filters.programme_ids;
  if (filters.agency_ids?.length) params.agency_ids = filters.agency_ids;
  if (filters.source) params.source = filters.source;
  if (filters.status_filter) params.status_filter = filters.status_filter;
  if (filters.deadline_after) params.deadline_after = filters.deadline_after;
  if (filters.deadline_before) params.deadline_before = filters.deadline_before;
  if (filters.budget_min_eur != null) params.budget_min_eur = filters.budget_min_eur;
  if (filters.budget_max_eur != null) params.budget_max_eur = filters.budget_max_eur;
  if (filters.trl_min != null) params.trl_min = filters.trl_min;
  if (filters.trl_max != null) params.trl_max = filters.trl_max;
  if (filters.sectors?.length) params.sectors = filters.sectors;
  if (filters.eligibility_tags?.length) params.eligibility_tags = filters.eligibility_tags;
  if (filters.geo_scope?.length) params.geo_scope = filters.geo_scope;
  if (filters.language) params.language = filters.language;
  if (filters.sort) params.sort = filters.sort;
  if (filters.limit != null) params.limit = filters.limit;
  if (filters.offset != null) params.offset = filters.offset;
  return params;
}

export function useCalls(filters: CallSearchFilters = {}, opts: { enabled?: boolean } = {}) {
  const normalised = normalizeCallFilters(filters);
  return useQuery({
    queryKey: queryKeys.calls(normalised),
    queryFn: () =>
      apiClient<CallListResponse>('/api/v1/calls', {
        searchParams: callFiltersToSearchParams(normalised),
      }),
    enabled: opts.enabled ?? true,
    // Catalogue data doesn't change minute-to-minute; keep it cached
    // long enough that flipping a single filter chip stays instant for
    // the user.
    staleTime: 60_000,
  });
}

export function useCallDetail(callId: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.callDetail(callId),
    queryFn: () => apiClient<CallDetail>(`/api/v1/calls/${callId}`),
    enabled: opts.enabled ?? Boolean(callId),
    staleTime: 60_000,
  });
}

export function useCreateCall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CallCreate) =>
      apiClient('/api/v1/calls', { method: 'POST', body: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['calls'] }),
  });
}

/** Generate project ideas tailored to one call (Faz 2). */
export function useGenerateIdeasForCall() {
  return useMutation({
    mutationFn: ({
      callId,
      nIdeas = 3,
      useOrgProfile = true,
      forceRefresh = false,
    }: {
      callId: string;
      nIdeas?: number;
      useOrgProfile?: boolean;
      forceRefresh?: boolean;
    }) =>
      apiClient<GenerateIdeasResponse>(
        `/api/v1/calls/${callId}/generate-ideas`,
        {
          method: 'POST',
          body: {
            n_ideas: nIdeas,
            use_org_profile: useOrgProfile,
            force_refresh: forceRefresh,
          },
        },
      ),
  });
}

/** Rule-based eligibility check for the caller's org against a call. */
export function useCallEligibility(callId: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.callEligibility(callId),
    queryFn: () =>
      apiClient<EligibilityReport>(`/api/v1/calls/${callId}/eligibility`),
    enabled: opts.enabled ?? Boolean(callId),
    staleTime: 60_000,
  });
}

// ── Project ideas + bidirectional matching (Faz 2) ──────────────────────

export function useIdeas(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.ideas,
    queryFn: () => apiClient<IdeaListResponse>('/api/v1/ideas'),
    enabled: opts.enabled ?? true,
    staleTime: 30_000,
  });
}

export function useIdeaDetail(ideaId: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.ideaDetail(ideaId),
    queryFn: () => apiClient<IdeaSummary>(`/api/v1/ideas/${ideaId}`),
    enabled: opts.enabled ?? Boolean(ideaId),
  });
}

export function useCreateIdea() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: IdeaCreate) =>
      apiClient<IdeaSummary>('/api/v1/ideas', { method: 'POST', body: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.ideas }),
  });
}

/** Run the matcher for an idea; persists + returns the ranked calls. */
export function useMatchIdea() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ideaId, topK = 5 }: { ideaId: string; topK?: number }) =>
      apiClient<IdeaMatchResponse>(`/api/v1/ideas/${ideaId}/match`, {
        method: 'POST',
        searchParams: { top_k: topK },
      }),
    onSuccess: (_data, { ideaId }) =>
      qc.invalidateQueries({ queryKey: queryKeys.ideaMatches(ideaId) }),
  });
}

/** Read the cached match list for an idea (empty until the matcher runs). */
export function useIdeaMatches(ideaId: string, opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.ideaMatches(ideaId),
    queryFn: () =>
      apiClient<IdeaMatchResponse>(`/api/v1/ideas/${ideaId}/matches`),
    enabled: opts.enabled ?? Boolean(ideaId),
  });
}

// ── Organization profile (Faz 2) ────────────────────────────────────────

export function useOrganizationProfile(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.organizationProfile,
    queryFn: () =>
      apiClient<OrganizationProfile>('/api/v1/organizations/profile'),
    enabled: opts.enabled ?? true,
    // 404 means "no profile yet" — a real product state, not an error
    // to retry. The consuming component checks isError + treats it as
    // "show the empty form".
    retry: false,
    staleTime: 60_000,
  });
}

export function useUpsertOrganizationProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OrganizationProfileUpsert) =>
      apiClient<OrganizationProfile>('/api/v1/organizations/profile', {
        method: 'PUT',
        body: input,
      }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: queryKeys.organizationProfile }),
  });
}

// ── Proposals CRUD ──────────────────────────────────────────────────────

export function useProposals() {
  return useQuery({
    queryKey: queryKeys.proposalList,
    queryFn: () =>
      apiClient<ProposalListResponse>('/api/v1/proposals'),
  });
}

export function useProposal(id: string) {
  return useQuery({
    queryKey: queryKeys.proposal(id),
    queryFn: () =>
      apiClient<ProposalDetail>(`/api/v1/proposals/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ProposalCreate) =>
      apiClient<ProposalDetail>('/api/v1/proposals', {
        method: 'POST',
        body: input,
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: queryKeys.proposalList });
      qc.setQueryData(queryKeys.proposal(data.id), data);
    },
  });
}

export function useUpdateProposal(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ProposalUpdate) =>
      apiClient<ProposalDetail>(`/api/v1/proposals/${id}`, {
        method: 'PATCH',
        body: input,
      }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.proposal(id), data);
      qc.invalidateQueries({ queryKey: queryKeys.proposalList });
    },
  });
}

export function useDeleteProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient(`/api/v1/proposals/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.proposalList }),
  });
}

/**
 * Kick off the 7-agent saga. The mutation resolves immediately with a
 * Celery job id + a stream URL the caller can subscribe to for progress.
 * Detail-page state is invalidated so the next `useProposal()` read
 * picks up the `status=generating` flip.
 */
export function useGenerateProposal(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient<GenerateEnqueued>(`/api/v1/proposals/${id}/generate`, {
        method: 'POST',
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.proposal(id) }),
  });
}

/**
 * Enqueue a DOCX or XLSX export. Returns the Celery job id; the FE
 * polls `/api/v1/jobs/{id}` (via `useJob`) for the signed URL once the
 * worker uploads the file to Supabase Storage.
 */
export function useExportProposal(id: string) {
  return useMutation({
    mutationFn: (format: 'docx' | 'xlsx' = 'docx') =>
      apiClient<ExportEnqueued>(`/api/v1/proposals/${id}/export`, {
        method: 'POST',
        body: { format },
      }),
  });
}

// ── /jobs — Celery job polling ──────────────────────────────────────────

/** Mirror of `apps/api/src/api/routes/jobs.py::JobStatusResponse`. */
export interface JobStatusResponse {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  result: Record<string, unknown> | null;
  error: string | null;
}

const JOB_POLL_INTERVAL_MS = 3000;

/**
 * Refetch cadence for `useJob` — exported for unit tests.
 *
 * `false` stops TanStack's interval entirely once the job is terminal;
 * anything else keeps polling every 3s. `undefined` data (first fetch
 * in flight) keeps polling so a slow first response can't wedge the
 * loop into "never started".
 */
export function jobPollInterval(data: JobStatusResponse | undefined): number | false {
  if (data && (data.status === 'completed' || data.status === 'failed')) {
    return false;
  }
  return JOB_POLL_INTERVAL_MS;
}

/**
 * Poll a Celery job until it reaches a terminal state.
 *
 * Why polling is the PRIMARY progress channel (not just a fallback):
 * the SSE stream endpoint requires a bearer token, and the browser's
 * native `EventSource` cannot attach Authorization headers — so the
 * live stream 403s in production today. `/jobs/{id}` goes through the
 * normal authenticated `apiClient`, works everywhere, and (with
 * `task_track_started` on the worker) distinguishes queued → running →
 * completed/failed.
 *
 * Pass `null` to park the hook (no fetch, no interval) — callers hold
 * a job id only after the enqueue mutation resolves.
 */
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: queryKeys.job(jobId ?? 'none'),
    queryFn: () => apiClient<JobStatusResponse>(`/api/v1/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (query) => jobPollInterval(query.state.data),
    // A terminal job never un-completes; interval refetches ignore
    // staleTime anyway, and disabling focus-refetch avoids a redundant
    // request burst when the operator tabs back to watch progress.
    refetchOnWindowFocus: false,
  });
}
