'use client';

import { MoreHorizontal, Trash2, UserCog } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/components/ui/use-toast';
import { useMembers, useRemoveMember, useUpdateMemberRole } from '@/lib/api/queries';
import type { MemberSummary, UserRole } from '@bluedev/shared-types';

const ROLE_ORDER: Record<UserRole, number> = { owner: 0, admin: 1, member: 2, viewer: 3 };

export function MembersTable() {
  const t = useTranslations('members');
  const tCommon = useTranslations('common');
  const format = useFormatter();
  const { toast } = useToast();
  const { data, isLoading } = useMembers();
  const updateRole = useUpdateMemberRole();
  const remove = useRemoveMember();

  const [target, setTarget] = useState<MemberSummary | null>(null);

  async function changeRole(memberId: string, role: UserRole) {
    try {
      await updateRole.mutateAsync({ memberId, role });
      toast({ title: tCommon('save') });
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  async function confirmRemove() {
    if (!target) return;
    try {
      await remove.mutateAsync(target.id);
      toast({ title: tCommon('delete') });
      setTarget(null);
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  const sorted = (data?.members ?? []).slice().sort((a, b) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{t('title')}</CardTitle>
          <CardDescription>{data?.members.length ?? 0}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>{t('role')}</TableHead>
                  <TableHead>{t('joined')}</TableHead>
                  <TableHead className="w-[60px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-medium">
                          {member.display_name ?? member.email ?? member.id}
                          {member.is_self && (
                            <span className="ml-1 text-xs text-muted-foreground">{t('you')}</span>
                          )}
                        </span>
                        {member.display_name && member.email && (
                          <span className="text-xs text-muted-foreground">{member.email}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={member.role === 'owner' ? 'default' : 'secondary'}>
                        {t(`roles.${member.role}`)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {format.dateTime(new Date(member.joined_at), 'short')}
                    </TableCell>
                    <TableCell>
                      {!member.is_self && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuLabel className="flex items-center gap-2">
                              <UserCog className="h-3 w-3" />
                              {t('role')}
                            </DropdownMenuLabel>
                            {(['owner', 'admin', 'member', 'viewer'] as UserRole[]).map((role) => (
                              <DropdownMenuItem
                                key={role}
                                disabled={role === member.role}
                                onClick={() => changeRole(member.id, role)}
                              >
                                {t(`roles.${role}`)}
                                {role === member.role && (
                                  <span className="ml-auto text-xs">✓</span>
                                )}
                              </DropdownMenuItem>
                            ))}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="text-destructive"
                              onClick={() => setTarget(member)}
                            >
                              <Trash2 className="h-4 w-4" />
                              {t('remove')}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={target !== null} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('remove')}</DialogTitle>
            <DialogDescription>
              {target && t('removeConfirm', { email: target.email ?? target.id })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTarget(null)}>
              {tCommon('cancel')}
            </Button>
            <Button variant="destructive" onClick={confirmRemove} disabled={remove.isPending}>
              {t('remove')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
