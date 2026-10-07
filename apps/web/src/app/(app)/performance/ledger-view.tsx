"use client";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatQuantity } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ledgerSentence, type PerformanceData, type SettingsData } from "@/lib/performance/performance";

import { Money, TD, TH, TH_LEFT } from "./shared";
import { SettingsFormPanel } from "./settings-form";

const fetchPerformance = () => getBrowserApiClient().GET("/portfolio/performance");
const fetchSettings = () => getBrowserApiClient().GET("/portfolio/performance/settings");

const KIND_LABEL: Record<string, string> = {
  buy: "Purchases",
  sell: "Sales",
  dividend: "Dividends",
  deposit: "Deposits",
  withdrawal: "Withdrawals",
  transfer: "Transfers",
  fee: "Account fees",
  unsupported: "Not readable",
};

function Health({ data }: { data: PerformanceData }) {
  const l = data.ledger;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker={l.first && l.last ? `${formatDate(l.first)} to ${formatDate(l.last)}` : undefined}
          title={l.status === "clean" ? "The ledger explains every cash change" : l.status === "empty" ? "No transactions stored" : "The ledger has gaps"}
        />
        <p className="mt-2 max-w-3xl text-sm text-text-muted">{ledgerSentence(l)}</p>
        <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Object.entries(l.by_kind).map(([kind, count]) => (
            <div key={kind}>
              <dt className="text-xs text-text-muted">{KIND_LABEL[kind] ?? kind}</dt>
              <dd className="num text-lg font-semibold">{count}</dd>
            </div>
          ))}
        </dl>
        {l.fees_not_captured > 0 ? (
          <p className="mt-3 text-xs text-text-muted">
            {l.fees_not_captured} fills were stored before fees were captured; their fees count as zero until the next full history read.
          </p>
        ) : null}
      </Blueprint>

      {l.cash_breaks_total > 0 ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading
            kicker={`Tolerance ${l.cash_tolerance} in ${data.account_currency ?? "the account currency"} · total unexplained ${l.unexplained_cash.toFixed(2)}`}
            title={`Days where cash does not add up (${l.cash_breaks_total})`}
          />
          <div className="mt-3 overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">Snapshot intervals where the stored transactions do not explain the change in cash</caption>
              <thead>
                <tr className="bg-surface-muted">
                  <th scope="col" className={TH_LEFT}>Between</th>
                  <th scope="col" className={TH}>Unexplained</th>
                  <th scope="col" className={TH_LEFT}>Likely reason</th>
                </tr>
              </thead>
              <tbody>
                {l.cash_breaks.map((c) => (
                  <tr key={`${c.start_date}-${c.end_date}`} className="border-t border-border-soft">
                    <th scope="row" className="num px-3 py-1.5 text-left font-normal">{formatDate(c.start_date)} to {formatDate(c.end_date)}</th>
                    <td className={TD}><Money value={c.difference} currency={data.account_currency} signed decimals={2} /></td>
                    <td className="px-3 py-1.5 text-text-muted">{c.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {l.cash_breaks_total > l.cash_breaks.length ? <p className="mt-2 text-xs text-text-muted">Showing the latest {l.cash_breaks.length} of {l.cash_breaks_total}.</p> : null}
        </Blueprint>
      ) : null}

      {l.position_breaks.length > 0 ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker="Units rebuilt from fills against what the broker holds" title={`Holdings the fills do not explain (${l.position_breaks.length})`} />
          <div className="mt-3 overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <caption className="sr-only">Holdings where the fills and the position held at the broker disagree</caption>
              <thead>
                <tr className="bg-surface-muted">
                  <th scope="col" className={TH_LEFT}>Holding</th>
                  <th scope="col" className={TH}>From fills</th>
                  <th scope="col" className={TH}>At the broker</th>
                  <th scope="col" className={TH}>Difference</th>
                  <th scope="col" className={TH_LEFT}>Why</th>
                </tr>
              </thead>
              <tbody>
                {l.position_breaks.map((b) => (
                  <tr key={b.ticker} className="border-t border-border-soft">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{b.ticker}</th>
                    <td className={TD}>{formatQuantity(b.ledger_quantity)}</td>
                    <td className={TD}>{formatQuantity(b.broker_quantity)}</td>
                    <td className={TD}>{formatQuantity(b.difference)}</td>
                    <td className="px-3 py-1.5 text-text-muted">{b.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Blueprint>
      ) : null}

      {l.unsupported.length > 0 ? (
        <AlertMessage title="Transactions the ledger cannot read">
          {l.unsupported.join(", ")}. They are not counted in any balance until the type is supported.
        </AlertMessage>
      ) : null}
    </div>
  );
}

export function LedgerView() {
  const performance = useApiResource(fetchPerformance);
  const settings = useApiResource(fetchSettings);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={performance.state} label="ledger health">
        {(data: PerformanceData) => <Health data={data} />}
      </ResourceGate>
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Saved as a new version each time" title="Settings" />
        <div className="mt-4">
          <ResourceGate state={settings.state} label="settings">
            {(data: SettingsData) => (
              <SettingsFormPanel
                key={data.version ?? 0}
                initial={data}
                onSaved={() => {
                  settings.reload();
                  performance.reload();
                }}
              />
            )}
          </ResourceGate>
        </div>
      </Blueprint>
    </div>
  );
}
