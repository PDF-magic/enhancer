import test from "node:test";
import assert from "node:assert/strict";

import { oppositeTheme, resolveTheme } from "../web/js/theme.js";

test("theme follows system preference until explicitly overridden", () => {
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme(null, false), "light");
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("invalid", true), "dark");
});

test("theme toggle alternates between light and dark", () => {
  assert.equal(oppositeTheme("light"), "dark");
  assert.equal(oppositeTheme("dark"), "light");
});
