// Progressive disclosure: a titled section that starts collapsed and shows one summary line.
import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export function Disclosure({ id, title, summary, defaultOpen = false, children }: { id: string; title: string; summary?: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-lg border bg-card" data-testid={`section-${id}`}>
      <button type="button" className="flex w-full items-center gap-2 p-3 text-left" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid={`section-toggle-${id}`}>
        {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
        <h2 className="text-base font-semibold">{title}</h2>
        {summary && <span className="ml-1 min-w-0 truncate text-sm text-muted-foreground">{summary}</span>}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">{open ? "Hide" : "Show"}</span>
      </button>
      {open && <div className="space-y-2 border-t p-3">{children}</div>}
    </section>
  );
}
