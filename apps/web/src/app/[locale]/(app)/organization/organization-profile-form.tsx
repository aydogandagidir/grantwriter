'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';

import type { EntityType, OrganizationProfileUpsert } from '@bluedev/shared-types';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import {
  useOrganizationProfile,
  useUpsertOrganizationProfile,
} from '@/lib/api/queries';

const ENTITY_TYPES: EntityType[] = [
  'individual',
  'sme',
  'university',
  'large_corp',
  'ngo',
  'research_org',
];

function splitCsv(value: string): string[] {
  return [
    ...new Set(value.split(',').map((s) => s.trim()).filter(Boolean)),
  ];
}

function toNumber(value: string): number | undefined {
  if (value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

export function OrganizationProfileForm() {
  const t = useTranslations('organization');
  const tFields = useTranslations('organization.fields');
  const tEntity = useTranslations('organization.entityTypes');
  const { toast } = useToast();

  const { data: profile, isLoading } = useOrganizationProfile();
  const upsert = useUpsertOrganizationProfile();

  // Form state — string-backed so empty inputs round-trip cleanly.
  const [legalName, setLegalName] = useState('');
  const [entityType, setEntityType] = useState<EntityType | ''>('');
  const [country, setCountry] = useState('');
  const [nutsRegion, setNutsRegion] = useState('');
  const [naceCodes, setNaceCodes] = useState('');
  const [sectors, setSectors] = useState('');
  const [teamSize, setTeamSize] = useState('');
  const [annualRevenue, setAnnualRevenue] = useState('');
  const [foundedYear, setFoundedYear] = useState('');
  const [technologyAreas, setTechnologyAreas] = useState('');
  const [trlCurrent, setTrlCurrent] = useState('');
  const [trlTarget, setTrlTarget] = useState('');
  const [expertiseKeywords, setExpertiseKeywords] = useState('');
  const [preferredLanguages, setPreferredLanguages] = useState('');

  // Hydrate the form once the profile loads. 404 (no profile yet)
  // leaves the form empty — that's the intended "fill it in" state.
  useEffect(() => {
    if (!profile) return;
    setLegalName(profile.legal_name ?? '');
    setEntityType(profile.entity_type ?? '');
    setCountry(profile.country ?? '');
    setNutsRegion(profile.nuts_region ?? '');
    setNaceCodes(profile.nace_codes.join(', '));
    setSectors(profile.sectors.join(', '));
    setTeamSize(profile.team_size?.toString() ?? '');
    setAnnualRevenue(profile.annual_revenue_eur?.toString() ?? '');
    setFoundedYear(profile.founded_year?.toString() ?? '');
    setTechnologyAreas(profile.technology_areas.join(', '));
    setTrlCurrent(profile.trl_current?.toString() ?? '');
    setTrlTarget(profile.trl_target?.toString() ?? '');
    setExpertiseKeywords(profile.expertise_keywords.join(', '));
    setPreferredLanguages(profile.preferred_languages.join(', '));
  }, [profile]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const payload: OrganizationProfileUpsert = {
      legal_name: legalName.trim() || undefined,
      entity_type: entityType || undefined,
      country: country.trim() || undefined,
      nuts_region: nutsRegion.trim() || undefined,
      nace_codes: splitCsv(naceCodes),
      sectors: splitCsv(sectors),
      team_size: toNumber(teamSize),
      annual_revenue_eur: toNumber(annualRevenue),
      founded_year: toNumber(foundedYear),
      technology_areas: splitCsv(technologyAreas),
      trl_current: toNumber(trlCurrent),
      trl_target: toNumber(trlTarget),
      expertise_keywords: splitCsv(expertiseKeywords),
      preferred_languages: splitCsv(preferredLanguages),
    };
    try {
      await upsert.mutateAsync(payload);
      toast({ title: t('saved') });
    } catch (err) {
      toast({
        title: 'Error',
        description: err instanceof Error ? err.message : 'Could not save profile',
        variant: 'destructive',
      });
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </header>

      {isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('title')}</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="space-y-1.5">
                <Label htmlFor="org-legal-name">{tFields('legalName')}</Label>
                <Input
                  id="org-legal-name"
                  value={legalName}
                  onChange={(e) => setLegalName(e.target.value)}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="org-entity-type">{tFields('entityType')}</Label>
                  <Select
                    value={entityType}
                    onValueChange={(v) => setEntityType(v as EntityType)}
                  >
                    <SelectTrigger id="org-entity-type" data-testid="org-entity-type">
                      <SelectValue placeholder="—" />
                    </SelectTrigger>
                    <SelectContent>
                      {ENTITY_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {tEntity(type)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="org-country">{tFields('country')}</Label>
                  <Input
                    id="org-country"
                    maxLength={8}
                    value={country}
                    onChange={(e) => setCountry(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="org-nuts">{tFields('nutsRegion')}</Label>
                  <Input
                    id="org-nuts"
                    value={nutsRegion}
                    onChange={(e) => setNutsRegion(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="org-nace">{tFields('naceCodes')}</Label>
                  <Input
                    id="org-nace"
                    value={naceCodes}
                    onChange={(e) => setNaceCodes(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="org-sectors">{tFields('sectors')}</Label>
                <Input
                  id="org-sectors"
                  value={sectors}
                  onChange={(e) => setSectors(e.target.value)}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="org-team-size">{tFields('teamSize')}</Label>
                  <Input
                    id="org-team-size"
                    type="number"
                    min={0}
                    value={teamSize}
                    onChange={(e) => setTeamSize(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="org-revenue">{tFields('annualRevenue')}</Label>
                  <Input
                    id="org-revenue"
                    type="number"
                    min={0}
                    value={annualRevenue}
                    onChange={(e) => setAnnualRevenue(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="org-founded">{tFields('foundedYear')}</Label>
                  <Input
                    id="org-founded"
                    type="number"
                    min={1800}
                    max={2100}
                    value={foundedYear}
                    onChange={(e) => setFoundedYear(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="org-trl-current">{tFields('trlCurrent')}</Label>
                  <Input
                    id="org-trl-current"
                    type="number"
                    min={1}
                    max={9}
                    value={trlCurrent}
                    onChange={(e) => setTrlCurrent(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="org-trl-target">{tFields('trlTarget')}</Label>
                  <Input
                    id="org-trl-target"
                    type="number"
                    min={1}
                    max={9}
                    value={trlTarget}
                    onChange={(e) => setTrlTarget(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="org-tech-areas">{tFields('technologyAreas')}</Label>
                <Input
                  id="org-tech-areas"
                  value={technologyAreas}
                  onChange={(e) => setTechnologyAreas(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="org-expertise">{tFields('expertiseKeywords')}</Label>
                <Input
                  id="org-expertise"
                  value={expertiseKeywords}
                  onChange={(e) => setExpertiseKeywords(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="org-languages">{tFields('preferredLanguages')}</Label>
                <Input
                  id="org-languages"
                  value={preferredLanguages}
                  onChange={(e) => setPreferredLanguages(e.target.value)}
                />
              </div>

              <Button
                type="submit"
                data-testid="org-submit"
                disabled={upsert.isPending}
                className="w-full"
              >
                {upsert.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {t('saving')}
                  </>
                ) : (
                  t('save')
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
