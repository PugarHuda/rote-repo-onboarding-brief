#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: codeowners-drift
 * description: Audit a repository's CODEOWNERS file against the files it actually tracks. A CODEOWNERS file is written once and then the tree moves out from under it, so review routing silently stops working — a rule points at a directory that was renamed, a broad rule added at the bottom quietly overrides every rule above it because the LAST match wins, and half the repository ends up with no owner and no required reviewer. This reports each rule with how many tracked files it matches and how many it still owns after later rules override it, names the rules that match nothing and the rules that are fully shadowed, lists every file no rule covers grouped by top-level directory with a coverage percentage, and flags syntax the forge will reject — negation, malformed owner handles, GitLab section headers in a GitHub file. It reads the one file GitHub actually uses when several are present and says which. Matching follows GitHub's documented semantics, including that docs/* does not descend into subdirectories. What it cannot check is stated in the output — whether each team exists and whether branch protection actually requires a code owner review need the forge API, so they are listed as not checked rather than assumed fine. Read-only, no credentials, no adapters. A local path is inspected in place, a URL is shallow-cloned to a temp directory, and nothing the repository ships is ever executed.
 * source: https://github.com/PugarHuda/rote-repo-onboarding-brief
 * tags:
 * - domain-code-analysis
 * - job-code-review
 * - audience-developers
 * - effect-read-only
 * discoverability:
 *   tags:
 *   - domain-code-analysis
 *   - job-code-review
 *   - audience-developers
 *   - effect-read-only
 * metadata:
 *   rote_version: 0.79.0
 *   version: 0.1.1
 *   status: released
 *   kind: atomic
 *   flow_type: sequential
 *   execution_model: steps_with_presentation
 *   requires_endpoints: []
 *   requires_sessions: false
 *   contract:
 *     atomic: true
 *     input:
 *       type: none
 *     output:
 *       format: json
 *       destination: stdout
 *     composable: true
 * parameters:
 * - name: repo
 *   type: string
 *   required: true
 *   description: 'Repository to audit: a git URL (https://, ssh://, git@host:owner/name) or a path to a local checkout'
 *   example: https://github.com/vuejs/core
 * - name: branch
 *   type: string
 *   required: false
 *   default: ''
 *   description: Branch or tag to inspect. Ignored for a local path; defaults to the repository default branch.
 * steps:
 *   resolve:
 *     type: process.exec
 *     timeout_ms: 180000
 *     argv:
 *     - sh
 *     - '@resource{resolve.sh}'
 *     - $repo
 *     - $branch
 *   audit:
 *     type: process.exec
 *     timeout_ms: 120000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{codeowners.py}'
 *     - '@resolve{.stdout.text}'
 * ---
 */

// A CODEOWNERS file is a claim about the tree that nothing re-checks. The
// forge evaluates it per pull request and never tells you a rule stopped
// matching, or that the rule you added at the bottom now owns everything.
//
// Three findings carry this Play: rules that match nothing (the path moved),
// rules that are fully shadowed (a later rule wins for every file they name),
// and the files no rule covers at all. The last one is the coverage number,
// and it is usually lower than anyone expects.

const presentationSdk = await import("__ROTE_PRESENTATION_SDK__").catch((cause) => {
  throw new Error(
    "This is a rote steps presentation program. Run it with `rote play run <name>`.",
    { cause },
  );
});
const { FlowOutput, loadPresentationContext, stepName } = presentationSdk;

const out = new FlowOutput();
const ctx = await loadPresentationContext();

type Dict = Record<string, unknown>;

const auditStep = ctx.step(stepName("audit"));
const resolveStep = ctx.step(stepName("resolve"));

function bodyOf(step: ReturnType<typeof ctx.step>): Dict | null {
  const o = step.outcome;
  if (o.status !== "completed" && o.status !== "restored") return null;
  const text = (o.output.body as { stdout?: { text?: string } })?.stdout?.text;
  if (typeof text !== "string" || !text.trim()) return null;
  try {
    const parsed = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null ? parsed as Dict : null;
  } catch {
    return null;
  }
}

const data = bodyOf(auditStep);
const S = (v: unknown) => String(v ?? "");

if (!data) {
  const why = resolveStep.outcome.status !== "completed" && resolveStep.outcome.status !== "restored"
    ? "the repository could not be resolved — check the URL, the branch, and your network access"
    : "the repository was resolved but CODEOWNERS could not be audited";
  out.human(`No audit: ${why}.`);
  out.summary("audit unavailable");
  out.result({ ok: false, unavailable: why });
} else if (data["is_present"] === false) {
  const lines = [
    "NO CODEOWNERS",
    "",
    `  ${S(data["root"])}`,
    "",
    `  ${S(data["reason"])}`,
    "",
    `  ${S(data["file_count"])} tracked files, none of them has a code owner.`,
  ];
  out.human(lines.join("\n"));
  out.summary("no CODEOWNERS file");
  out.result({ ok: true, is_present: false, root: data["root"], reason: data["reason"],
    file_count: data["file_count"] });
} else {
  const rules = (data["rules"] as Dict[]) ?? [];
  const stale = (data["stale_rules"] as Dict[]) ?? [];
  const shadowed = (data["shadowed_rules"] as Dict[]) ?? [];
  const problems = (data["problems"] as Dict[]) ?? [];
  const unownedDirs = (data["unowned_by_top_dir"] as Dict[]) ?? [];
  const owners = (data["owners"] as Dict[]) ?? [];
  const others = (data["other_codeowners_files"] as string[]) ?? [];

  const lines: string[] = [];
  lines.push(`CODEOWNERS AUDIT · ${S(data["codeowners_file"])} · ${S(data["rule_count"])} rules · ` +
    `${S(data["file_count"])} tracked files`);
  lines.push("");
  if (others.length) {
    lines.push(`  Also present but NOT read by GitHub: ${others.join(", ")}`);
    lines.push("");
  }

  lines.push(`COVERAGE  ${S(data["coverage_pct"])}%  ` +
    `(${S(data["owned_count"])} owned · ${S(data["unowned_count"])} without an owner)`);
  lines.push("");

  lines.push("RULES");
  for (const r of rules) {
    const tag = r["status"] === "ok" ? "   " : r["status"] === "shadowed" ? "SHD" : "NIL";
    // Pad, then always emit a separator: padEnd adds nothing at the column width.
    lines.push(`  ${tag}  L${String(r["line"]).padEnd(4)} ${S(r["pattern"]).padEnd(36)}  ` +
      `matches ${String(r["matched"]).padStart(5)} · owns ${String(r["owns"]).padStart(5)}  ` +
      `${((r["owners"] as string[]) ?? []).join(" ") || "(no owner — clears ownership)"}`);
  }
  if (Number(data["rules_omitted"]) > 0) {
    lines.push(`  … ${S(data["rules_omitted"])} more rules not listed (stale and shadowed ones are always listed below)`);
  }
  lines.push("");

  lines.push("MATCHES NOTHING");
  if (stale.length) {
    for (const r of stale) lines.push(`  L${S(r["line"])}  ${S(r["pattern"])}`);
    lines.push("");
    lines.push("  The path moved or never existed. The forge accepts the rule and routes nothing.");
  } else {
    lines.push("  None. Every rule matches at least one tracked file.");
  }
  lines.push("");

  lines.push("SHADOWED");
  if (shadowed.length) {
    for (const r of shadowed) lines.push(`  L${S(r["line"])}  ${S(r["pattern"])}`);
    lines.push("");
    lines.push("  A later rule wins for every file these match. The last match wins, so a broad");
    lines.push("  rule near the bottom silently overrides the specific ones above it.");
  } else {
    lines.push("  None. Every rule that matches something still owns something.");
  }
  lines.push("");

  lines.push("WITHOUT AN OWNER");
  if (unownedDirs.length) {
    for (const d of unownedDirs.slice(0, 15)) {
      lines.push(`  ${String(d["files"]).padStart(6)}  ${S(d["dir"])}/`);
    }
    if (unownedDirs.length > 15) lines.push(`  … and ${unownedDirs.length - 15} more directories`);
  } else {
    lines.push("  None. Every tracked file has an owner.");
  }
  lines.push("");

  if (owners.length) {
    lines.push("OWNERS");
    for (const o of owners.slice(0, 15)) {
      lines.push(`  ${String(o["files"]).padStart(6)}  ${S(o["owner"])}`);
    }
    lines.push("");
  }

  if (problems.length) {
    lines.push("SYNTAX");
    for (const p of problems) lines.push(`  L${S(p["line"])}  ${S(p["why"])}`);
    lines.push("");
  }

  lines.push("NOT CHECKED");
  for (const n of (data["not_checked"] as string[]) ?? []) lines.push(`  ${n}`);

  const nStale = Number(data["stale_count"] ?? stale.length);
  const nShadow = Number(data["shadowed_count"] ?? shadowed.length);
  const nProb = Number(data["problem_count"] ?? problems.length);
  const issues = nStale + nShadow + nProb;
  const verdict = issues === 0
    ? `${S(data["coverage_pct"])}% covered, every rule live`
    : `${S(data["coverage_pct"])}% covered · ${nStale} stale · ${nShadow} shadowed · ${nProb} syntax`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    is_present: true,
    root: data["root"],
    codeowners_file: data["codeowners_file"],
    other_codeowners_files: others,
    file_count: data["file_count"],
    owned_count: data["owned_count"],
    unowned_count: data["unowned_count"],
    coverage_pct: data["coverage_pct"],
    rule_count: data["rule_count"],
    rules,
    rules_omitted: data["rules_omitted"],
    stale_count: nStale,
    stale_rules: stale,
    shadowed_count: nShadow,
    shadowed_rules: shadowed,
    unowned_by_top_dir: unownedDirs,
    unowned_sample: data["unowned_sample"],
    owners,
    problems,
    not_checked: data["not_checked"],
  });
}
