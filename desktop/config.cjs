"use strict";
const fs = require("node:fs");
const path = require("node:path");
const {productName, version} = require("../package.json");

function dataDirectory(appData, env = process.env) {
  const override = env.KICAD_COMPONENT_PACKAGER_DATA_DIR || env.PARTSHELF_DATA_DIR;
  if (override) return path.resolve(override);
  const current = path.join(appData, productName);
  const legacy = path.join(appData, "PartShelf");
  // Reuse the old catalog and Electron preferences in place on upgrade.
  // Keeping the directory also preserves the single-instance lock.
  if (!fs.existsSync(current) && fs.existsSync(legacy)) return legacy;
  return current;
}

module.exports = {productName, version, dataDirectory};
