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
  CheckoutResponse,
  CommentListResponse,
  CommentRecord,
  InvitationCreated,
  InvitationListResponse,
  InvitationPreview,
  LlmConfigSummary,
  LlmConfigTestResponse,
  MeResponse,
  MemberListResponse,
  UsageReport,
  VersionListResponse,
} from '@bluedev/shared-types';

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
};

// ── /me ─────────────────────────────────────────────────────────────────

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => apiClient<MeResponse>('/api/v1/me'),
    staleTime: 60_000,
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
