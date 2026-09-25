import { EmptyState } from "@/components/empty-state";

export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          Portfolio value, allocation, drift and today&apos;s alerts at a glance.
        </p>
      </div>
      <EmptyState title="Nothing to show yet">
        Holdings sync from Trading212 arrives in a later phase. Once connected, your
        portfolio overview appears here.
      </EmptyState>
    </div>
  );
}
