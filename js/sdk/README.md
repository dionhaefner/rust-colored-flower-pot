# @rcfp/sdk

Dependency-free ES modules and TypeScript declarations for RCFP. Import `Client`
from `@rcfp/sdk`, or directly from `/sdk/index.js` on an `rcfp connect` server.
The package's support/version policy and complete usage instructions are in
[the JavaScript reference](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/docs/reference/javascript.md).

```js
import { Client } from "@rcfp/sdk";
const client = new Client("http://127.0.0.1:5000", { profile: "native" });
await client.connect();
const { layers } = await client.discover();
const render = {
  mode: "singleband",
  keys: layers[0].keys,
  params: { stretch_range: [0, 3000] },
};
const tileTemplate = client.tileTemplate(render);
```

The SDK does not carry mapping-library dependencies, basemap credentials or a UI
framework. Applications import optional adapters from `@rcfp/sdk/integrations`.

Install published versions with `npm install @rcfp/sdk`. SDK releases use independent
`sdk-vX.Y.Z` tags, with prereleases on npm's `next` tag. See the
[testing and release guide](https://github.com/dionhaefner/rust-colored-flower-pot/blob/main/docs/how-to/release-sdk.md)
and [changelog](CHANGELOG.md).
