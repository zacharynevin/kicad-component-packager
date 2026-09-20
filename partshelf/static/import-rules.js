"use strict";
// Run user patterns outside the UI thread. The caller terminates slow patterns.
self.onmessage = ({data:{rule, symbols}}) => {
  try {
    const group = Number(rule.group);
    if (!Number.isInteger(group) || group < 0 || group > 99) throw new Error("Choose a capture group from 0 to 99 (0 means the whole match).");
    if (!rule.pattern || rule.pattern.length > 512) throw new Error("Enter a pattern of up to 512 characters.");
    const regex = new RegExp(rule.pattern, rule.ignoreCase ? "gi" : "g");
    const rows = symbols.map(symbol => {
      const original = String(symbol.properties?.MPN || "");
      const row = {key:symbol.key, name:symbol.name, original, value:original, status:"No match"};
      if (original && !rule.overwrite) return {...row, status:"Kept existing"};
      const input = String(rule.source === "description" ? symbol.description || "" : symbol.name);
      regex.lastIndex = 0;
      const match = regex.exec(input);
      if (!match) return row;
      if (match[0] === "") return {...row, status:"Empty match"};
      if (regex.exec(input)) return {...row, status:"Multiple matches"};
      const extracted = match[group]?.trim();
      if (!extracted) return {...row, status:"Capture group is empty or missing"};
      if (extracted.length > 256) return {...row, status:"Part number is too long"};
      return {...row, value:extracted, status:"Extracted"};
    });
    self.postMessage({rows});
  } catch (error) {self.postMessage({error:error.message});}
};
