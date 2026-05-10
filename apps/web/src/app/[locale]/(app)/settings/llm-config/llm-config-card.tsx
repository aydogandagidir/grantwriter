'use client';

import { CheckCircle2, KeyRound, Loader2, ShieldOff, TestTube2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { useLlmConfig, useTestLlmConfig, useUpdateLlmConfig } from '@/lib/api/queries';

export function LlmConfigCard() {
  const t = useTranslations('llmConfig');
  const tCommon = useTranslations('common');
  const tErrors = useTranslations('errors');
  const format = useFormatter();
  const { toast } = useToast();
  const { data, isLoading } = useLlmConfig();
  const update = useUpdateLlmConfig();
  const test = useTestLlmConfig();

  const [anthropic, setAnthropic] = useState('');
  const [openai, setOpenai] = useState('');

  async function onSave() {
    try {
      await update.mutateAsync({
        anthropic_api_key: anthropic || undefined,
        openai_api_key: openai || undefined,
      });
      toast({ title: t('savedToast') });
      setAnthropic('');
      setOpenai('');
    } catch (err) {
      toast({
        variant: 'destructive',
        title: tCommon('error'),
        description: (err as Error).message ?? tErrors('generic'),
      });
    }
  }

  async function onClear() {
    try {
      await update.mutateAsync({ anthropic_api_key: null, openai_api_key: null });
      toast({ title: t('clearedToast') });
    } catch (err) {
      toast({
        variant: 'destructive',
        title: tCommon('error'),
        description: (err as Error).message ?? tErrors('generic'),
      });
    }
  }

  async function onTest(provider: 'anthropic' | 'openai') {
    try {
      const result = await test.mutateAsync(provider);
      if (result.ok) {
        toast({
          title: t('testSuccess', { model: result.model_used ?? provider }),
        });
      } else {
        toast({
          variant: 'destructive',
          title: t('testFailed', { message: result.message }),
        });
      }
    } catch (err) {
      toast({
        variant: 'destructive',
        title: tCommon('error'),
        description: (err as Error).message ?? tErrors('generic'),
      });
    }
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
            <KeyRound className="h-5 w-5" />
            BYOK
          </CardTitle>
          <CardDescription>
            {isLoading ? (
              <Skeleton className="h-4 w-40" />
            ) : data?.updated_at ? (
              t('lastUpdated', { date: format.dateTime(new Date(data.updated_at), 'short') })
            ) : null}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <KeyRow
            i18nLabel={t('anthropic')}
            placeholder={t('placeholderAnthropic')}
            value={anthropic}
            onChange={setAnthropic}
            configured={data?.anthropic_configured ?? false}
            configuredLabel={t('configured')}
            notConfiguredLabel={t('notConfigured')}
            onTest={() => onTest('anthropic')}
            testLabel={t('test')}
            testing={test.isPending && test.variables === 'anthropic'}
            isLoading={isLoading}
          />
          <Separator />
          <KeyRow
            i18nLabel={t('openai')}
            placeholder={t('placeholderOpenai')}
            value={openai}
            onChange={setOpenai}
            configured={data?.openai_configured ?? false}
            configuredLabel={t('configured')}
            notConfiguredLabel={t('notConfigured')}
            onTest={() => onTest('openai')}
            testLabel={t('test')}
            testing={test.isPending && test.variables === 'openai'}
            isLoading={isLoading}
          />
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button onClick={onSave} disabled={update.isPending || (!anthropic && !openai)}>
              {update.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('save')}
            </Button>
            <Button variant="outline" onClick={onClear} disabled={update.isPending}>
              <ShieldOff className="h-4 w-4" />
              {t('clear')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface KeyRowProps {
  i18nLabel: string;
  placeholder: string;
  value: string;
  onChange: (next: string) => void;
  configured: boolean;
  configuredLabel: string;
  notConfiguredLabel: string;
  onTest: () => void;
  testLabel: string;
  testing: boolean;
  isLoading: boolean;
}

function KeyRow({
  i18nLabel,
  placeholder,
  value,
  onChange,
  configured,
  configuredLabel,
  notConfiguredLabel,
  onTest,
  testLabel,
  testing,
  isLoading,
}: KeyRowProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>{i18nLabel}</Label>
        {isLoading ? (
          <Skeleton className="h-5 w-24" />
        ) : configured ? (
          <Badge variant="default" className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {configuredLabel}
          </Badge>
        ) : (
          <Badge variant="secondary">{notConfiguredLabel}</Badge>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          type="password"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <Button variant="outline" type="button" disabled={!configured || testing} onClick={onTest}>
          {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}
          {testLabel}
        </Button>
      </div>
    </div>
  );
}
