import {
  CASH_WALK_FIXTURE,
  DEBT_FIXTURE,
  EARNINGS_FIXTURE,
  ESTIMATES_FIXTURE,
  PEERS_FIXTURE,
  SEGMENTS_FIXTURE,
  SEGMENTS_MISALIGNED_FIXTURE,
  STATEMENTS_FIXTURE,
} from "./fixture";
import type { CashWalk, DebtSchedule, EarningsEvent, EstimateSet, PeerSet, SegmentSet, StatementSet } from "./types";

export type FinancialsPageData = {
  statements: StatementSet;
  cashWalk: CashWalk;
  debt: DebtSchedule;
  segments: SegmentSet;
  segmentsMisaligned: SegmentSet;
  estimates: EstimateSet;
  earnings: readonly EarningsEvent[];
  peers: PeerSet;
};

/** Seam for the fundamentals read APIs (`D4.1`, `D4.7`, `D4.8`); resolves the typed fixture until they exist. */
export async function loadFinancialsPage(): Promise<FinancialsPageData> {
  return {
    statements: STATEMENTS_FIXTURE,
    cashWalk: CASH_WALK_FIXTURE,
    debt: DEBT_FIXTURE,
    segments: SEGMENTS_FIXTURE,
    segmentsMisaligned: SEGMENTS_MISALIGNED_FIXTURE,
    estimates: ESTIMATES_FIXTURE,
    earnings: EARNINGS_FIXTURE,
    peers: PEERS_FIXTURE,
  };
}
