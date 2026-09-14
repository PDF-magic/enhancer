export function shellQuote(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(text)) return text;
  return `'${text.replaceAll("'", `'"'"'`)}'`;
}

export function requireValue(values, name, label) {
  const value = String(values[name] ?? "").trim();
  if (!value) throw new Error(`${label} is required.`);
  return value;
}

export function positiveInteger(value, label) {
  if (value === "" || value == null) return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive whole number.`);
  }
  return parsed;
}

export function command(parts) {
  return parts.filter((part) => part !== "" && part != null).join(" ");
}

export function optionalFlag(parts, flag, value) {
  if (String(value ?? "").trim()) parts.push(flag, shellQuote(value));
}
