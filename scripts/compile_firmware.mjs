// Compile firmware sketches with the pinned toolchain.   node scripts/compile_firmware.mjs <name|all> ...
// Names: gateway | flow_node_c3 | rain_node   (legacy sketches are archived and no longer compile against protocol v2)
import { join } from "node:path";
import { ROOT, fail } from "./lib.mjs";
import { cli } from "./setup_firmware_toolchain.mjs";

const TARGETS = {
  // the gateway uses its own partition table (firmware/gateway/partitions.csv): app slot 0x1F0000, so lift the board menu's 1.25 MB size check
  gateway:        { dir: "firmware/gateway",             fqbn: "esp32:esp32:esp32", extra: ["--build-property", "upload.maximum_size=2031616"] },
  flow_node_c3:   { dir: "firmware/flow_node_c3",        fqbn: "esp32:esp32:esp32c3:CDCOnBoot=cdc" },
  rain_node:      { dir: "firmware/rain_node",           fqbn: "esp32:esp32:esp32" },
  rain_hwtest:    { dir: "firmware/rain_hwtest",         fqbn: "esp32:esp32:esp32" },
};
const args = process.argv.slice(2);
if (!args.length) fail("usage: compile_firmware.mjs <" + Object.keys(TARGETS).join("|") + "|all>");
const all = Object.keys(TARGETS);
for (const name of args.includes("all") ? all : args) {
  const t = TARGETS[name];
  if (!t) fail(`unknown target ${name}`);
  console.log(`\n=== compiling ${name} (${t.fqbn}) ===`);
  cli(["compile", "--fqbn", t.fqbn, ...(t.extra || []), "--libraries", join(ROOT, "libraries"), join(ROOT, t.dir)]);
}
