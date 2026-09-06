#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: dependabot-coverage
 * description: Which package ecosystems in a repository does the update bot actually watch? Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. A dependabot.yml is written once for the root package.json and then the repository grows a Dockerfile, a GitHub Actions workflow, a Python service in api/, a Terraform module and three workspace packages — none of which the bot ever updates, because each ecosystem and directory needs its own entry. This walks the tree for every manifest directory (npm, pip, gomod, cargo, bundler, composer, maven, gradle, docker, github-actions, terraform, nuget, mix, pub, swift, devcontainers), reads the updates entries in .github/dependabot.yml — directory, directories globs, interval, open-pull-requests-limit — and a renovate config when present, and reports which ecosystem/directory pairs are covered and at what cadence, which are uncovered, which are matched only by an entry with open-pull-requests-limit 0 (security updates only, no version updates), and which entries point at directories that hold no manifest of that ecosystem any more. A coverage percentage closes it. What it cannot know is printed — whether Dependabot is switched on in the repository settings, private registry credentials, and manifests deeper than four directories. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
 * source: https://github.com/PugarHuda/rote-repo-onboarding-brief
 * tags:
 * - domain-supply-chain
 * - job-dependency-review
 * - audience-developers
 * - effect-read-only
 * discoverability:
 *   tags:
 *   - domain-supply-chain
 *   - job-dependency-review
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
 *   example: https://github.com/cli/cli
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
 *     - '@resource{coverage.py}'
 *     - '@resolve{.stdout.text}'
 * ---
 */

// dependabot.yml is a list of (ecosystem, directory) pairs. Every manifest the
// repository grows afterwards is silently outside it. This is the diff between
// what the tree contains and what the bot was told to watch.

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
    : "the repository was resolved but its update-bot coverage could not be audited";
  out.human(`No audit: ${why}.`);
  out.summary("audit unavailable");
  out.result({ ok: false, unavailable: why });
} else {
  const covered = (data["covered"] as Dict[]) ?? [];
  const uncovered = (data["uncovered"] as Dict[]) ?? [];
  const disabled = (data["disabled"] as Dict[]) ?? [];
  const stale = (data["stale_entries"] as Dict[]) ?? [];
  const problems = (data["problems"] as Dict[]) ?? [];
  const renovate = data["renovate"] as Dict | null;
  const total = Number(data["ecosystems_present"] ?? 0);

  const lines: string[] = [];
  const cfg = data["config"] ? S(data["config"]) : renovate ? S(renovate["file"]) : "no dependabot.yml, no renovate config";
  lines.push(`UPDATE-BOT COVERAGE · ${cfg} · ${total} manifest directories · ${S(data["entries"])} update entries`);
  lines.push("");
  if (total === 0) {
    lines.push("  No package manifests found in the tree (to a depth of 4), so there is nothing for a bot to watch.");
  } else {
    lines.push(`COVERAGE  ${S(data["coverage_pct"])}%  (${covered.length} covered · ${uncovered.length} uncovered · ${disabled.length} version updates disabled)`);
    lines.push("");
  }
  if (renovate) {
    lines.push(`RENOVATE  ${S(renovate["file"])}${renovate["readable"] === false ? " — could not be parsed, so it counts for nothing" : ""}` +
      `${Array.isArray(renovate["enabled_managers"]) ? "  enabledManagers: " + (renovate["enabled_managers"] as string[]).join(", ") : ""}`);
    lines.push("");
  }

  lines.push("UNCOVERED");
  if (uncovered.length) {
    lines.push("  Manifests the bot was never told about. Nothing here gets a version or security PR:");
    for (const u of uncovered) lines.push(`  ${S(u["ecosystem"]).padEnd(16)} ${S(u["directory"]).padEnd(32)} ${((u["manifests"] as string[]) ?? []).join(", ")}${u["why"] && !S(u["why"]).startsWith("no update entry") ? "  — " + S(u["why"]) : ""}`);
  } else if (total) {
    lines.push("  None. Every manifest directory has a matching update entry.");
  }
  lines.push("");

  if (disabled.length) {
    lines.push("VERSION UPDATES DISABLED (open-pull-requests-limit: 0)");
    lines.push("  Dependabot still opens security PRs for these, but never a version bump:");
    for (const u of disabled) lines.push(`  ${S(u["ecosystem"]).padEnd(16)} ${S(u["directory"])}`);
    lines.push("");
  }

  lines.push("COVERED");
  for (const c of covered) lines.push(`  ${S(c["ecosystem"]).padEnd(16)} ${S(c["directory"]).padEnd(32)} ${S(c["interval"]) || "no interval"}`);
  if (!covered.length) lines.push("  Nothing.");
  lines.push("");

  if (stale.length) {
    lines.push("ENTRIES POINTING AT NOTHING");
    lines.push("  The bot checks these on schedule and finds no manifest — usually a directory that moved:");
    for (const s of stale) lines.push(`  L${S(s["line"])}  ${S(s["ecosystem"]).padEnd(16)} ${((s["directories"] as string[]) ?? []).join(", ")}`);
    lines.push("");
  }
  if (problems.length) {
    lines.push("CONFIG PROBLEMS");
    for (const p of problems) lines.push(`  L${S(p["line"])}  ${S(p["why"])}`);
    lines.push("");
  }

  lines.push("NOT CHECKED");
  for (const n of (data["not_checked"] as string[]) ?? []) lines.push(`  ${n}`);

  const verdict = total === 0
    ? "no manifests to watch"
    : `${S(data["coverage_pct"])}% covered · ${uncovered.length} uncovered · ${disabled.length} disabled · ${stale.length} stale entries`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    root: data["root"],
    config: data["config"],
    renovate,
    ecosystems_present: total,
    coverage_pct: data["coverage_pct"],
    covered,
    uncovered,
    disabled,
    stale_entries: stale,
    problems,
    not_checked: data["not_checked"],
  });
}
