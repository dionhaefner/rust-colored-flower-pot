import test from "node:test";
import assert from "node:assert/strict";
import { releaseInfo } from "../packaging/verify-sdk-release.mjs";

test("stable SDK versions publish to latest", () => {
  assert.deepEqual(releaseInfo("sdk-v0.1.0", "0.1.0", "0.1.0"), {
    version: "0.1.0",
    npmTag: "latest",
  });
});

test("SDK prereleases publish to next", () => {
  for (const stage of ["alpha", "beta", "rc"]) {
    const version = `1.2.3-${stage}.0`;
    assert.equal(
      releaseInfo(`sdk-v${version}`, version, version).npmTag,
      "next",
    );
  }
});

test("malformed and Python tags cannot publish an SDK", () => {
  for (const tag of [
    undefined,
    "v0.1.0",
    "sdk-v01.1.0",
    "sdk-v0.1",
    "sdk-v0.1.0+build",
    "sdk-v0.1.0-rc.01",
    "sdk-v0.1.0-dev.1",
  ]) {
    assert.throws(() => releaseInfo(tag, "0.1.0", "0.1.0"));
  }
});

test("a tag or export mismatch cannot publish", () => {
  assert.throws(() => releaseInfo("sdk-v0.2.0", "0.1.0", "0.1.0"));
  assert.throws(() => releaseInfo("sdk-v0.1.0", "0.1.0", "0.2.0"));
});
