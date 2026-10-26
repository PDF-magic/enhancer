import test from "node:test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test("macOS launcher has valid shell syntax", () => {
  execFileSync("bash", ["-n", "Launch PDF Magic.command"], {
    cwd: fileURLToPath(new URL("../", import.meta.url)),
  });
});
