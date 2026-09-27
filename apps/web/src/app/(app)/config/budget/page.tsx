import type { Metadata } from "next";

import { BudgetEditor } from "./budget-editor";
import { CostPanel } from "./cost-panel";

export const metadata: Metadata = { title: "Budget" };

export default function BudgetPage() {
  return (
    <div className="space-y-6">
      <BudgetEditor />
      <CostPanel />
    </div>
  );
}
