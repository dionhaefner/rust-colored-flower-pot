import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const out = resolve(process.argv[2] ?? "build/sdk-package");
const sdk = join(root, "js/sdk");
const metadata = JSON.parse(readFileSync(join(sdk, "package.json")));
assert.deepEqual(metadata.dependencies ?? {}, {}, "SDK runtime dependencies");
assert.deepEqual(
  metadata.optionalDependencies ?? {},
  {},
  "SDK optional dependencies",
);
const files = [
  "CHANGELOG.md",
  "LICENSE",
  "README.md",
  "index.d.ts",
  "index.js",
  "integrations.d.ts",
  "integrations.js",
  "package.json",
];
mkdirSync(out, { recursive: true });
const [packed] = JSON.parse(
  execFileSync(
    "npm",
    ["pack", "--json", "--ignore-scripts", "--pack-destination", out],
    { cwd: sdk, encoding: "utf8" },
  ),
);
assert.equal(packed.name, metadata.name);
assert.equal(packed.version, metadata.version);
assert.deepEqual(packed.files.map((file) => file.path).sort(), files);
const tarball = join(out, packed.filename);
const consumer = join(out, "consumer");
mkdirSync(consumer);
writeFileSync(
  join(consumer, "package.json"),
  JSON.stringify({ private: true, type: "module" }),
);
execFileSync(
  "npm",
  [
    "install",
    "--offline",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    "--package-lock=false",
    tarball,
  ],
  { cwd: consumer, stdio: "inherit" },
);
const installed = join(consumer, "node_modules", ...metadata.name.split("/"));
for (const file of files) {
  assert.deepEqual(
    readFileSync(join(installed, file)),
    readFileSync(join(sdk, file)),
    file,
  );
}
copyFileSync(
  join(root, "tests/sdk_package_types.mts"),
  join(consumer, "check.mts"),
);
execFileSync(
  process.execPath,
  [
    join(root, "packaging/sdk/node_modules/typescript/bin/tsc"),
    "--strict",
    "--module",
    "nodenext",
    "--target",
    "es2022",
    "check.mts",
  ],
  { cwd: consumer, stdio: "inherit" },
);
execFileSync(process.execPath, [join(consumer, "check.mjs")], {
  stdio: "inherit",
});
writeFileSync(
  join(out, "SHA256SUMS"),
  `${createHash("sha256").update(readFileSync(tarball)).digest("hex")}  ${packed.filename}\n`,
);
console.log(`Verified publishable tarball: ${tarball}`);
