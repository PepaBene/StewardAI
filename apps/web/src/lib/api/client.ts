import createClient, { type Client, type Middleware } from "openapi-fetch";

import { getApiBaseUrl } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

import type { components, paths } from "./schema";

/*
 * Typed client for the StewardAI Python API. `paths` is generated from
 * apps/backend/openapi.json by `npm run gen:api` (openapi-typescript), so every
 * path, parameter and response body here is type-checked against the backend.
 */

export type ApiClient = Client<paths>;
type Schemas = components["schemas"];
export type HealthResponse = Schemas["HealthResponse"];
export type PortfolioOverview = Schemas["PortfolioOverview"];
export type PortfolioHistory = Schemas["PortfolioHistory"];
export type ContributionReport = Schemas["ContributionReport"];
export type HoldingContribution = Schemas["HoldingContribution"];
export type HoldingsResponse = Schemas["HoldingsResponse"];
export type Holding = Schemas["Holding"];
export type JobSummary = Schemas["JobSummary"];
export type DriftReport = Schemas["DriftReport"];
export type PositionDrift = Schemas["PositionDrift"];
export type DriftStatus = PositionDrift["status"];
export type Freshness = Schemas["Freshness"];
export type HoldingDetail = Schemas["HoldingDetail"];
export type KpiStatus = Schemas["KpiStatus"];
export type VerdictOut = Schemas["VerdictOut"];
export type FactOut = Schemas["FactOut"];
export type FactCheck = Schemas["FactCheck"];
export type EvidenceLink = Schemas["EvidenceLink"];
export type Confidence = FactOut["confidence"];
export type EvidenceRelation = EvidenceLink["relation"];
export type VerdictStatus = VerdictOut["status"];
export type FactStatus = FactOut["status"];
export type ThesisBoard = Schemas["ThesisBoard"];
export type BoardRow = Schemas["BoardRow"];
export type BoardKpi = Schemas["BoardKpi"];
export type FactIndexEntry = Schemas["FactIndexEntry"];
export type ReviewItem = Schemas["ReviewItem"];
export type ReconciliationOut = Schemas["ReconciliationOut"];
export type FailedCheck = Schemas["FailedCheck"];
export type FactReview = Schemas["FactReview"];
export type FactReviewResult = Schemas["FactReviewResult"];
export type ReviewAction = FactReview["action"];
export type ThesesConfig = Schemas["ThesesConfig"];
export type ThesisConfig = Schemas["ThesisConfig"];
export type ThesisKPI = Schemas["ThesisKPI"];
export type HoldingProfilesConfig = Schemas["HoldingProfilesConfig"];
export type HoldingProfile = Schemas["HoldingProfile"];
export type Exposure = Schemas["Exposure"];
export type NewsFeed = Schemas["NewsFeed"];
export type NewsItemOut = Schemas["NewsItemOut"];
export type StoryOut = Schemas["StoryOut"];
export type AlertOut = Schemas["AlertOut"];
export type ReportSummary = Schemas["ReportSummary"];
export type ReportOut = Schemas["ReportOut"];
export type DigestBody = Schemas["DigestBody"];
export type DigestSection = Schemas["DigestSection"];
export type DigestSentence = Schemas["Sentence"];
export type DigestSource = Schemas["DigestSource"];
export type TestAlertResult = Schemas["TestAlertResult"];
export type EventTypeInfo = Schemas["EventTypeInfo"];
export type EventType = EventTypeInfo["id"];
export type NotificationsConfig = Schemas["NotificationsConfig"];
export type NotificationRule = Schemas["NotificationRule"];
export type NewsSourcesConfig = Schemas["NewsSourcesConfig"];
export type NewsSource = Schemas["NewsSource"];
export type EventRulesConfig = Schemas["EventRulesConfig"];
export type StrategyConfig = Schemas["StrategyConfig"];
export type StrategyRule = NonNullable<StrategyConfig["rules"]>[number];
export type JobsConfig = Schemas["JobsConfig"];
export type JobSchedule = Schemas["JobSchedule"];
export type ConfigDomainSlug = paths["/config/{domain}/versions"]["get"]["parameters"]["path"]["domain"];
export type ConfigVersionMeta = Schemas["ConfigVersionMeta"];
export type ConfigVersionBody = Schemas["ConfigVersionBody"];
export type ConfigDiff = Schemas["ConfigDiff"];
export type DiffChange = Schemas["DiffChange"];
export type ConfigExport = Schemas["ConfigExport"];
export type ImportResult = Schemas["ImportResult"];
export type TransactionOut = Schemas["TransactionOut"];
export type TransactionType = TransactionOut["type"];
export type TransactionsPage = Schemas["TransactionsPage"];
export type TotalByCurrency = Schemas["TotalByCurrency"];
export type FeeTotals = Schemas["FeeTotals"];
export type TransactionFeeOut = Schemas["TransactionFeeOut"];
export type PortfolioRisk = Schemas["PortfolioRisk"];
export type SharpeStats = Schemas["SharpeStats"];
export type HoldingIdentity = Schemas["HoldingIdentity"];
export type IdentifierOut = Schemas["IdentifierOut"];
export type ListingOut = Schemas["ListingOut"];
export type LiquidityOut = Schemas["LiquidityOut"];
export type LegalEntityOut = Schemas["LegalEntityOut"];
export type RelationshipOut = Schemas["RelationshipOut"];
export type RelationshipType = RelationshipOut["type"];
export type RelationshipDecision = Schemas["RelationshipDecision"];
export type ReportingExceptionOut = Schemas["ReportingExceptionOut"];
export type AliasOut = Schemas["AliasOut"];
export type PriceHistory = Schemas["PriceHistory"];
export type PricePoint = Schemas["PricePoint"];
export type RiskCard = Schemas["RiskCard"];
export type Correlation = Schemas["Correlation"];
export type ReferenceGaps = Schemas["ReferenceGaps"];
export type ExchangeCalendar = Schemas["ExchangeCalendar"];
export type ExchangeClosure = Schemas["ExchangeClosure"];
export type ClosureSuggestion = Schemas["ClosureSuggestion"];
export type PriceGap = Schemas["PriceGap"];
export type JobDetail = Schemas["JobDetail"];
export type JobStepOut = Schemas["JobStepOut"];
export type JobStatus = JobSummary["status"];
export type DisputedStory = Schemas["DisputedStory"];
export type ClaimSource = Schemas["ClaimSource"];
export type StoryResolution = Schemas["StoryResolution"];
export type EscalationOut = Schemas["EscalationOut"];
export type EscalationBody = Schemas["EscalationBody"];
export type EscalationSource = Schemas["EscalationSource"];
export type NoteSentence = Schemas["NoteSentence"];
export type PortfolioFlag = Schemas["PortfolioFlag"];
export type ThesisChange = Schemas["ThesisChange"];
export type ReviewDue = Schemas["ReviewDue"];
export type MacroFigure = Schemas["MacroFigure"];
export type MarketHoliday = Schemas["MarketHoliday"];
export type AnnouncementRule = Schemas["AnnouncementRule"];
// Phase 4: verification hardening.
export type FeedbackIn = Schemas["FeedbackIn"];
export type FeedbackOut = Schemas["FeedbackOut"];
export type FeedbackTargetKind = FeedbackIn["target_kind"];
export type FeedbackRating = FeedbackIn["rating"];
export type NewsLabel = Schemas["NewsLabel"];
export type SourceTrustOut = Schemas["SourceTrustOut"];
export type EvalCandidateOut = Schemas["EvalCandidateOut"];
export type ActivityCoverage = Schemas["ActivityCoverage"];
export type HoldingCoverage = Schemas["HoldingCoverage"];
export type JobList = Schemas["JobList"];
export type JobListItem = Schemas["JobListItem"];
export type ReplayPreview = Schemas["ReplayPreview"];
export type ReplayChange = Schemas["ReplayChange"];
export type ReplayOutcome = Schemas["ReplayOutcome"];
export type VerificationConfig = Schemas["VerificationConfig"];
export type ClaimVerifierSettings = Schemas["ClaimVerifierSettings"];
export type ClaimTarget = NonNullable<ClaimVerifierSettings["applies_to"]>[number];
export type ClaimCheckSummary = Schemas["ClaimCheckSummary"];
export type SentenceCheck = NonNullable<DigestSentence["check"]>;
export type DigestFactSource = Schemas["DigestFactSource"];
export type DigestStory = Schemas["DigestStory"];
export type AlertKind = AlertOut["kind"];
// Phase 5: research, the monthly review, chat, report search and 13F ownership.
export type Source = Schemas["Source"];
export type SourceKind = Source["kind"];
export type CitedSentence = Schemas["CitedSentence"];
export type ResearchBody = Schemas["ResearchBody"];
export type ResearchSection = Schemas["ResearchSection"];
export type ResearchReportOut = Schemas["ResearchReportOut"];
export type ResearchSummary = Schemas["ResearchSummary"];
export type Valuation = Schemas["Valuation"];
export type ResearchProfilesConfig = Schemas["ResearchProfilesConfig"];
export type ResearchProfile = Schemas["ResearchProfile"];
export type ResearchSectionId = ResearchProfile["sections"][number];
export type ValuationMethod = NonNullable<ResearchProfile["valuation_methods"]>[number];
export type ReviewBody = Schemas["ReviewBody"];
export type ReviewReportOut = Schemas["ReviewReportOut"];
export type ComplianceRow = Schemas["ComplianceRow"];
export type KpiRow = Schemas["KpiRow"];
export type ProposedAction = Schemas["ProposedAction"];
export type ReportHit = Schemas["ReportHit"];
export type ReportType = ReportHit["type"];
export type ConversationOut = Schemas["ConversationOut"];
export type ConversationDetail = Schemas["ConversationDetail"];
export type MessageOut = Schemas["MessageOut"];
export type ChatAnswer = Schemas["ChatAnswer"];
export type ChatSentence = Schemas["ChatSentence"];
export type ActionOut = Schemas["ActionOut"];
export type HoldingOwnership = Schemas["HoldingOwnership"];
export type OwnershipPeriod = Schemas["OwnershipPeriod"];
export type TopHolder = Schemas["TopHolder"];
export type ManagerChange = Schemas["ManagerChange"];
// Prompts config: owner overrides of the AI steps' system prompts.
export type PromptsConfig = Schemas["PromptsConfig"];
export type PromptDefault = Schemas["PromptDefault"];
export type PromptKey = PromptDefault["key"];

export type AccessTokenGetter = () => Promise<string | null | undefined>;

/** openapi-fetch middleware adding `Authorization: Bearer <Supabase JWT>` when a session exists. */
export function bearerAuth(getAccessToken: AccessTokenGetter): Middleware {
  return {
    async onRequest({ request }) {
      const token = await getAccessToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  };
}

export function createApiClient(options: {
  getAccessToken?: AccessTokenGetter;
  baseUrl?: string;
  fetch?: typeof fetch;
} = {}): ApiClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? getApiBaseUrl(),
    fetch: options.fetch,
  });
  if (options.getAccessToken) client.use(bearerAuth(options.getAccessToken));
  return client;
}

async function browserAccessToken(): Promise<string | null> {
  try {
    const { data } = await getSupabaseBrowserClient().auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    // Supabase not configured: call the API without a token (public routes only).
    return null;
  }
}

let browserClient: ApiClient | null = null;

/** API client for Client Components; carries the signed-in user's Supabase JWT. */
export function getBrowserApiClient(): ApiClient {
  browserClient ??= createApiClient({ getAccessToken: browserAccessToken });
  return browserClient;
}
