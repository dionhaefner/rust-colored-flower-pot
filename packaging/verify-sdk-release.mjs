import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { VERSION } from "../js/sdk/index.js";

export function releaseInfo(tag, packageVersion, exportedVersion) {
  const match =
    /^sdk-v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-(?:alpha|beta|rc)\.(?:0|[1-9]\d*))?)$/.exec(
      tag,
    );
  assert.ok(
    match,
    "SDK tag must be sdk-vMAJOR.MINOR.PATCH[-alpha.N|-beta.N|-rc.N]",
  );
  assert.equal(match[1], packageVersion, "SDK tag and package version differ");
  assert.equal(
    exportedVersion,
    packageVersion,
    "SDK export and package version differ",
  );
  return {
    version: match[1],
    npmTag: match[1].includes("-") ? "next" : "latest",
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const metadata = JSON.parse(
    readFileSync(new URL("../js/sdk/package.json", import.meta.url)),
  );
  const release = releaseInfo(process.argv[2], metadata.version, VERSION);
  console.log(`version=${release.version}\nnpm_tag=${release.npmTag}`);
}
