'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { Copy, Loader2, Mail, Trash2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

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
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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
import { useCreateInvitation, useInvitations, useRevokeInvitation } from '@/lib/api/queries';
import type { InvitationCreated } from '@bluedev/shared-types';

const schema = z.object({
  email: z.string().email(),
  role: z.enum(['member', 'admin']),
});
type FormValues = z.infer<typeof schema>;

export function InvitationsPanel() {
  const t = useTranslations('invitations');
  const tMembers = useTranslations('members');
  const tCommon = useTranslations('common');
  const format = useFormatter();
  const { toast } = useToast();
  const { data, isLoading } = useInvitations();
  const create = useCreateInvitation();
  const revoke = useRevokeInvitation();

  const [tokenModal, setTokenModal] = useState<InvitationCreated | null>(null);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', role: 'member' },
  });

  async function onSubmit(values: FormValues) {
    try {
      const result = await create.mutateAsync(values);
      toast({ title: t('createdToast') });
      form.reset();
      setTokenModal(result);
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  async function onRevoke(id: string) {
    try {
      await revoke.mutateAsync(id);
      toast({ title: t('revokedToast') });
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  async function copy(token: string) {
    await navigator.clipboard.writeText(token);
    toast({ title: tCommon('copied') });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <Mail className="h-5 w-5" />
            {t('invite')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form
              className="flex flex-col gap-3 md:flex-row md:items-end"
              onSubmit={form.handleSubmit(onSubmit)}
              noValidate
            >
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem className="flex-1">
                    <FormLabel>{t('email')}</FormLabel>
                    <FormControl>
                      <Input type="email" autoComplete="off" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="role"
                render={({ field }) => (
                  <FormItem className="w-full md:w-40">
                    <FormLabel>{t('role')}</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger aria-label={t('role')}>
                          <SelectValue placeholder={tMembers('roles.member')} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="member">{tMembers('roles.member')}</SelectItem>
                        <SelectItem value="admin">{tMembers('roles.admin')}</SelectItem>
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={create.isPending}>
                {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('invite')}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{t('list')}</CardTitle>
          <CardDescription>{data?.invitations.length ?? 0}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : data && data.invitations.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>{t('role')}</TableHead>
                  <TableHead>{t('expiresAt')}</TableHead>
                  <TableHead className="w-[60px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.invitations.map((inv) => (
                  <TableRow key={inv.id}>
                    <TableCell className="font-medium">{inv.email}</TableCell>
                    <TableCell>{tMembers(`roles.${inv.role}`)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {format.dateTime(new Date(inv.expires_at), 'short')}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => onRevoke(inv.id)}
                        disabled={revoke.isPending}
                        aria-label={t('revoke')}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={tokenModal !== null} onOpenChange={(o) => !o && setTokenModal(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('createdToast')}</DialogTitle>
            <DialogDescription>{t('tokenOnce')}</DialogDescription>
          </DialogHeader>
          {tokenModal && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Input value={tokenModal.token} readOnly />
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => copy(tokenModal.token)}
                  type="button"
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {tokenModal.email} · expires{' '}
                {format.dateTime(new Date(tokenModal.expires_at), 'short')}
              </p>
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => setTokenModal(null)}>{tCommon('confirm')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
