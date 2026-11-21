// Same algorithm as apps/api/lekha/engines/gstin.py — instant feedback while typing.
const ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function normaliseGstin(v: string): string {
  return v.replace(/[^0-9a-z]/gi, "").toUpperCase();
}

export function checkChar(first14: string): string {
  let total = 0;
  for (let i = 0; i < 14; i++) {
    const product = ALPHABET.indexOf(first14[i]) * (i % 2 ? 2 : 1);
    total += Math.floor(product / 36) + (product % 36);
  }
  return ALPHABET[(36 - (total % 36)) % 36];
}

export function gstinProblem(raw: string): string | null {
  const g = normaliseGstin(raw);
  if (!g) return null;
  if (g.length !== 15) return `GSTIN has 15 characters (${g.length} entered).`;
  if (!/^\d{2}[0-9A-Z]{13}$/.test(g)) return "Starts with a 2-digit state code.";
  if ([...g].some((c) => ALPHABET.indexOf(c) < 0)) return "Only letters and digits.";
  if (checkChar(g.slice(0, 14)) !== g[14]) return "Check character doesn't match — a character is mistyped.";
  return null;
}
