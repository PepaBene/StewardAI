import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("createApiClient", () => {
  it("calls GET /health with a Bearer token and returns the typed body", async () => {
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: "ok", version: "0.1.0" }),
    );
    const api = createApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "jwt-123",
      fetch: fetchMock as unknown as typeof fetch,
    });

    const { data, error, response } = await api.GET("/health");

    expect(response.status).toBe(200);
    expect(error).toBeUndefined();
    expect(data?.status).toBe("ok");
    expect(data?.version).toBe("0.1.0");

    const request = fetchMock.mock.calls[0][0];
    expect(request.url).toBe("http://api.test/health");
    expect(request.headers.get("Authorization")).toBe("Bearer jwt-123");
  });

  it("omits the Authorization header when there is no session", async () => {
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () =>
      jsonResponse({ status: "ok", version: "0.1.0" }),
    );
    const api = createApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => null,
      fetch: fetchMock as unknown as typeof fetch,
    });

    await api.GET("/health");

    expect(fetchMock.mock.calls[0][0].headers.has("Authorization")).toBe(false);
  });
});
