#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: dependabot-coverage
 * description: Which package ecosystems in a repository does the update bot actually watch? Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. A dependabot.yml is written once for the root package.json and then the repository grows a Dockerfile, a GitHub Actions workflow, a Python service in api/, a Terraform module and three workspace packages — none of which the bot ever updates, because each ecosystem and directory needs its own entry. This walks the tree for every manifest directory (npm, pip, gomod, cargo, bundler, composer, maven, gradle, docker, github-actions, terraform, nuget, mix, pub, swift, devcontainers), reads the updates entries in .github/dependabot.yml — directory, directories globs, interval, open-pull-requests-limit — and a renovate config when present (enabledManagers and ignorePaths honoured), read as the json5 it actually is — comments, bare keys and single-quoted strings — because a config this play cannot parse would make it call a fully covered repository 0% covered, and reports which ecosystem/directory pairs are covered and at what cadence, which are uncovered, which are matched only by an entry with open-pull-requests-limit 0 (security updates only, no version updates), which are matched by an entry that also carries `ignore` with `dependency-name` set to a bare wildcard and nothing narrowing it, so the entry runs on schedule and drops every version update it finds while still reading as covered (the same wildcard narrowed by update-types is the ordinary no-majors rule and is never reported), and which entries point at directories that hold no manifest of that ecosystem any more. It also reports an update entry naming a registry that no top-level `registries:` block declares, which Dependabot rejects outright. Git submodules and compose files are counted as the ecosystems they are, because both are supported by the bot and almost nobody adds an entry for them. A coverage percentage closes it. What it cannot know is printed — whether Dependabot is switched on in the repository settings, private registry credentials, and manifests deeper than four directories. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
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
 *   version: 0.3.0
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
 * presentation_fixtures:
 *   resolve: resources/presentation-fixtures/resolve/fixture.yaml
 *   audit: resources/presentation-fixtures/audit/fixture.yaml
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
  const muted = (data["muted"] as Dict[]) ?? [];
  const stale = (data["stale_entries"] as Dict[]) ?? [];
  const problems = (data["problems"] as Dict[]) ?? [];
  const renovate = data["renovate"] as Dict | null;
  const total = Number(data["ecosystems_present"] ?? 0);
  // covered/uncovered are capped at 80 rows each so a 700-manifest repo fits rote's
  // 64 KB stdout budget; the headline must count the whole tree, not the visible slice
  const coveredN = Number(data["covered_count"] ?? covered.length);
  const uncoveredN = Number(data["uncovered_count"] ?? uncovered.length);
  const more = (shown: number, all: number) => all > shown ? `  … ${all - shown} more not listed` : "";

  const lines: string[] = [];
  const cfg = data["config"] ? S(data["config"]) : renovate ? S(renovate["file"]) : "no dependabot.yml, no renovate config";
  lines.push(`UPDATE-BOT COVERAGE · ${cfg} · ${total} manifest directories · ${S(data["entries"])} update entries`);
  lines.push("");
  if (total === 0) {
    lines.push("  No package manifests found in the tree (to a depth of 4), so there is nothing for a bot to watch.");
  } else {
    lines.push(`COVERAGE  ${S(data["coverage_pct"])}%  (${coveredN} covered · ${uncoveredN} uncovered · ${disabled.length} version updates disabled · ${muted.length} muted by an ignore rule)`);
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
    if (more(uncovered.length, uncoveredN)) lines.push(more(uncovered.length, uncoveredN));
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

  if (muted.length) {
    lines.push("COVERED ON PAPER, MUTED BY AN IGNORE RULE");
    lines.push("  A matching entry runs on schedule and drops every version update it finds:");
    for (const u of muted) lines.push(`  L${S(u["line"])}  ${S(u["ecosystem"]).padEnd(16)} ${S(u["directory"])}`);
    lines.push("");
  }

  lines.push("COVERED");
  for (const c of covered) lines.push(`  ${S(c["ecosystem"]).padEnd(16)} ${S(c["directory"]).padEnd(32)} ${S(c["interval"]) || "no interval"}`);
  if (more(covered.length, coveredN)) lines.push(more(covered.length, coveredN));
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
    : `${S(data["coverage_pct"])}% covered · ${uncoveredN} uncovered · ${disabled.length} disabled · ${muted.length} muted · ${stale.length} stale entries`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    root: data["root"],
    config: data["config"],
    renovate,
    ecosystems_present: total,
    covered_count: coveredN,
    uncovered_count: uncoveredN,
    coverage_pct: data["coverage_pct"],
    covered,
    uncovered,
    disabled,
    muted,
    stale_entries: stale,
    problems,
    not_checked: data["not_checked"],
  });
}
