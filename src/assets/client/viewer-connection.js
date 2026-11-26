import { Client } from "./sdk/index.js";

export function serviceUrl() {
  const configured = document.body.dataset.service;
  const selected = new URLSearchParams(location.search).get("service");
  return configured ? new URL(configured, location.href) : new URL(selected);
}

export async function connectService(url) {
  const profile = document.body.dataset.profile;
  if (profile) {
    const client = new Client(url, { profile });
    return { client, identity: await client.connect() };
  }

  const probe = new Client(url);
  let identity;
  try {
    identity = await probe.request(probe.url(["__terracotta", "identity"]));
  } catch (error) {
    // Terracotta has no identity endpoint, and its default 404 has no CORS headers.
    if (error.status !== 404 && error.kind !== "network") throw error;
    return { client: new Client(url, { profile: "terracotta" }), identity: {} };
  }
  if (
    identity?.protocol_compatibility !== "0.10.1" ||
    !["native", "terracotta"].includes(identity.compatibility_profile)
  )
    throw new Error(
      "The server advertises an unsupported protocol or profile.",
    );
  return {
    client: new Client(url, { profile: identity.compatibility_profile }),
    identity,
  };
}
