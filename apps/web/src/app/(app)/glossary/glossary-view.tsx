"use client";

import { SearchIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { GLOSSARY } from "@/lib/glossary/terms";

export function GlossaryView() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All terms");
  const categories = useMemo(() => ["All terms", ...new Set(GLOSSARY.map((term) => term.category))], []);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return GLOSSARY.filter((term) => (category === "All terms" || term.category === category) &&
      (!needle || `${term.term} ${term.definition} ${term.formula ?? ""}`.toLowerCase().includes(needle)));
  }, [category, query]);
  const groups = [...new Set(filtered.map((term) => term.category))];

  return (
    <div className="space-y-6">
      <PageHeader kicker="Learn" title="Financial glossary" description="Clear explanations of the terms and ratios used throughout StewardAI." />
      <Blueprint className="space-y-4 p-5">
        <PanelHeading kicker="Reference" title="Find a term" action={<span className="kicker">{filtered.length} / {GLOSSARY.length} terms</span>} />
        <div className="relative max-w-xl">
          <SearchIcon aria-hidden="true" className="absolute top-2.5 left-3 size-4 text-text-faint" />
          <Input aria-label="Search glossary" placeholder="Search terms, definitions, or formulas" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" />
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Filter glossary by topic">
          {categories.map((item) => <Button key={item} type="button" size="sm" variant={category === item ? "default" : "outline"} aria-pressed={category === item} onClick={() => setCategory(item)}>{item}</Button>)}
        </div>
      </Blueprint>
      {filtered.length === 0 ? <EmptyState title="No matching terms">Try another search or choose a different topic.</EmptyState> : groups.map((group) => (
        <section key={group} aria-labelledby={`glossary-${group}`} className="space-y-3">
          <h2 id={`glossary-${group}`} className="kicker">{group}</h2>
          <dl className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filtered.filter((term) => term.category === group).map((term) => (
              <Blueprint as="article" key={term.slug} id={term.slug} className="space-y-2 p-4">
                <dt className="font-heading text-lg font-semibold leading-tight">{term.term}</dt>
                <dd className="text-sm leading-6 text-text-muted">{term.definition}</dd>
                {term.formula ? <dd className="border-l-2 border-accent bg-surface-muted px-3 py-2 font-mono text-xs leading-5 text-text">{term.formula}</dd> : null}
                {term.related?.length ? <dd className="text-xs text-text-muted">Related: {term.related.map((slug, index) => {
                  const related = GLOSSARY.find((item) => item.slug === slug);
                  return related ? <span key={slug}>{index > 0 ? ", " : ""}<a className="text-accent-ink underline-offset-4 hover:underline" href={`#${slug}`}>{related.term}</a></span> : null;
                })}</dd> : null}
              </Blueprint>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
