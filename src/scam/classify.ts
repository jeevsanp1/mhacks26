const SCAM_PATTERNS: { label: string; pattern: RegExp }[] = [
  { label: "urgency", pattern: /\b(act now|immediately|urgent|right away|expires? (today|soon)|final notice)\b/i },
  { label: "gift_card", pattern: /\b(gift card|itunes card|google play card|steam card)\b/i },
  { label: "wire_transfer", pattern: /\b(wire transfer|western union|moneygram|send money now)\b/i },
  { label: "secrecy", pattern: /\b(don'?t tell|keep this (a )?secret|between us|do not mention to)\b/i },
  { label: "credential_request", pattern: /\b(social security number|ssn|password|one[- ]time (code|password)|pin number)\b/i },
  { label: "impersonation", pattern: /\b(irs|social security administration|microsoft support|amazon security)\b/i },
];

export interface ScamFlag {
  flagged: boolean;
  matchedPatterns: string[];
}

/** Rules-based first pass over untrusted inbound content (email/text/web page).
 * Cheap and deterministic; swap/augment with an LLM classifier call later if
 * recall needs to improve, but keep it outside the main conversation model. */
export function classifyForScamPatterns(text: string): ScamFlag {
  const matched = SCAM_PATTERNS.filter(({ pattern }) => pattern.test(text)).map((p) => p.label);
  return { flagged: matched.length > 0, matchedPatterns: matched };
}
