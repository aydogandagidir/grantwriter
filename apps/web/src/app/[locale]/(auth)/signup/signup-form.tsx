'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState, useTransition } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { signup } from '@/app/[locale]/(auth)/signup/actions';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/use-toast';

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});

type FormValues = z.infer<typeof schema>;

export function SignupForm() {
  const t = useTranslations('auth.signup');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const [pending, startTransition] = useTransition();
  const [confirmationSent, setConfirmationSent] = useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });

  function onSubmit(values: FormValues) {
    const formData = new FormData();
    formData.set('email', values.email);
    formData.set('password', values.password);
    startTransition(async () => {
      const result = await signup(formData);
      if (!result.ok) {
        toast({
          variant: 'destructive',
          title: tCommon('error'),
          description: result.error,
        });
        return;
      }
      if (result.needsConfirmation) {
        setConfirmationSent(true);
      }
      // Otherwise the action redirected and we're already gone.
    });
  }

  if (confirmationSent) {
    return (
      <p className="rounded-md border bg-muted/40 p-4 text-sm">
        {t('subtitle')} — check your inbox to confirm your email.
      </p>
    );
  }

  return (
    <Form {...form}>
      <form className="space-y-4" onSubmit={form.handleSubmit(onSubmit)} noValidate>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('email')}</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="email" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('password')}</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormDescription>{t('passwordHint')}</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" className="w-full" disabled={pending}>
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          {t('submit')}
        </Button>
      </form>
    </Form>
  );
}
