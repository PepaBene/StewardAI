import type { Middleware } from "openapi-fetch";

export const CONFIG_SAVED_EVENT = "steward:config-saved";

/** A save of a config domain (PUT /config/<domain>), not of its versions or imports. */
export function isConfigSave(method: string, url: string): boolean {
  if (method.toUpperCase() !== "PUT") return false;
  const path = new URL(url, "http://local").pathname;
  return /\/config\/[a-z-]+$/.test(path);
}

/** Tells the page a config was saved, so it can ask why if the save overruled the engine. */
export function configSavedSignal(): Middleware {
  return {
    onResponse({ request, response }) {
      if (typeof window !== "undefined" && response.ok && isConfigSave(request.method, request.url)) {
        window.dispatchEvent(new CustomEvent(CONFIG_SAVED_EVENT));
      }
      return response;
    },
  };
}
