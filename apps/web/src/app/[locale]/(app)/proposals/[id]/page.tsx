import { setRequestLocale } from 'next-intl/server';

import { ProposalEditorShell } from '@/app/[locale]/(app)/proposals/[id]/editor-shell';
import { apiServer } from '@/lib/api/client';
import type { MeResponse } from '@bluedev/shared-types';

export default async function ProposalDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);

  // Fetch /me so the comments panel can mark "my" comments for delete.
  const me = await apiServer<MeResponse>('/api/v1/me');

  return <ProposalEditorShell proposalId={id} currentUserId={me.user_id} />;
}
