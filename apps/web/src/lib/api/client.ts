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
export type EvidenceLink = Schemas["EvidenceLink"];
export type Confidence = FactOut["confidence"];
export type VerdictStatus = VerdictOut["status"];
export type ThesesConfig = Schemas["ThesesConfig"];
export type ThesisConfig = Schemas["ThesisConfig"];
export type ThesisKPI = Schemas["ThesisKPI"];
export type HoldingProfilesConfig = Schemas["HoldingProfilesConfig"];
export type HoldingProfile = Schemas["HoldingProfile"];
export type Exposure = Schemas["Exposure"];

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
