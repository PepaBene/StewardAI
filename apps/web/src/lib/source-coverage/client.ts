import { SOURCE_COVERAGE_FIXTURE } from "./fixture";
import type { SourceCoverageReport } from "./types";

/**
 * The single seam between the source-coverage view and its data. Today it resolves the typed
 * fixture; when the owner-only `GET /data/coverage`, `GET /sources/access` and
 * `GET /sources/usage` routes exist, replace the body with those three calls merged into a
 * `SourceCoverageReport` (the shape is the contract), and nothing else changes.
 */
export async function loadSourceCoverage(): Promise<SourceCoverageReport> {
  return SOURCE_COVERAGE_FIXTURE;
}
