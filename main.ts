#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: repo-onboarding-brief
 * description: Onboard to an unfamiliar repository, and check its setup instructions instead of trusting them. Cross-references every command the README tells you to run against what the project actually defines — package.json scripts, Make targets, justfile recipes, Cargo bins, go run/test paths, pyproject scripts, tox envs, nox sessions, Taskfile tasks, compose services, Dockerfiles, and npx binaries declared as dependencies — and against the tools present on your machine, so a documented-but-nonexistent command is named before you lose an afternoon to it. Also reports stack and version floors, entry points, a layout map, risk flags (no lockfile, no tests, no CI, committed secret-shaped files), and an explicit list of what it could not determine. Read-only, no credentials, no adapters. A local path is inspected in place, a URL is shallow-cloned to a temp directory, and nothing the repository ships is ever executed.
 * source: https://github.com/PugarHuda/rote-repo-onboarding-brief
 * tags:
 * - domain-code-analysis
 * - job-repository-onboarding
 * - audience-developers
 * - effect-read-only
 * discoverability:
 *   tags:
 *   - domain-code-analysis
 *   - job-repository-onboarding
 *   - audience-developers
 *   - effect-read-only
 * metadata:
 *   rote_version: 0.79.0
 *   version: 0.2.0
 *   status: released
 *   kind: atomic
 *   flow_type: parallel
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
 *   tags:
 *   - domain-code-analysis
 *   - job-repository-onboarding
 *   - audience-developers
 *   - effect-read-only
 *   discoverability:
 *     tags:
 *     - domain-code-analysis
 *     - job-repository-onboarding
 *     - audience-developers
 *     - effect-read-only
 * parameters:
 * - name: repo
 *   type: string
 *   required: true
 *   description: 'Repository to brief: a git URL (https://, ssh://, git@host:owner/name) or a path to a local checkout'
 *   example: https://github.com/pallets/click
 * - name: branch
 *   type: string
 *   required: false
 *   default: ''
 *   description: Branch or tag to inspect. Ignored for a local path; defaults to the repository default branch.
 * steps:
 *   resolve:
 *     type: process.exec
 *     timeout_ms: 120000
 *     argv:
 *     - sh
 *     - '@resource{resolve.sh}'
 *     - $repo
 *     - $branch
 *   probe:
 *     type: process.exec
 *     timeout_ms: 90000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{probe.py}'
 *     - '@resolve{.stdout.text}'
 *   claims:
 *     type: process.exec
 *     timeout_ms: 90000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{claims.py}'
 *     - '@resolve{.stdout.text}'
 * ---
 */

// Why this Play exists, and why it is shaped this way:
//
// Every other repo-orientation method summarises the README. That produces a
// setup guide nobody has checked. The expensive failure when you open an
// unfamiliar project is not "I could not find the docs", it is "I followed the
// docs and command three did not exist". So the load-bearing step here is
// `claims`: it resolves each documented command against the project's real
// definitions and against this machine's PATH, and separates those two failure
// modes, because "the docs are stale" and "you are missing a tool" need
// opposite responses from the reader.
//
// `probe` and `claims` are separate steps that both depend only on `resolve`,
// so the DAG runs them in parallel and either can fail without taking the
// other's findings down with it.
//
// The brief states what it does not know (section 7). A shallow clone cannot
// know contributor counts, so it says so rather than printing a 1 that looks
// like a fact.

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

function bodyOf(step: ReturnType<typeof ctx.step>): Dict | null {
  const outcome = step.outcome;
  if (outcome.status !== "completed" && outcome.status !== "restored") return null;
  const text = (outcome.output.body as { stdout?: { text?: string } })?.stdout?.text;
  if (typeof text !== "string" || !text.trim()) return null;
  try {
    const parsed = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null ? parsed as Dict : null;
  } catch {
    return null;
  }
}

const resolveStep = ctx.step(stepName("resolve"));
const probeStep = ctx.step(stepName("probe"));
const claimsStep = ctx.step(stepName("claims"));

const probe = bodyOf(probeStep);
const claims = bodyOf(claimsStep);

if (!probe) {
  const why = resolveStep.outcome.status !== "completed" && resolveStep.outcome.status !== "restored"
    ? "the repository could not be resolved — check the URL, the branch, and your network access"
    : "the repository could be resolved but not inspected";
  out.human(`No brief: ${why}.`);
  out.summary("brief unavailable");
  out.result({ ok: false, unavailable: why });
} else {
  const S = (v: unknown) => String(v ?? "");
  const eco = (probe["ecosystems"] as string[]) ?? [];
  const locks = (probe["lockfiles_present"] as string[]) ?? [];
  const git = (probe["git"] as Dict) ?? {};
  const readme = probe["readme"] as Dict | null;
  const lines: string[] = [];
  const unclear: string[] = [];

  let name: string | null = null;
  let desc: string | null = null;
  for (const key of ["node", "python", "go", "rust"]) {
    const d = probe[key] as Dict | undefined;
    if (d?.["name"] && !name) name = S(d["name"]);
    if (d?.["description"] && !desc) desc = S(d["description"]);
  }
  const title = name ?? S(probe["root"]).split("/").pop() ?? "repository";

  lines.push(`ONBOARDING BRIEF · ${title}`);
  lines.push("");

  // 1 -----------------------------------------------------------------
  lines.push("WHAT THIS IS");
  if (desc) {
    lines.push(`  ${desc}`);
    lines.push("  (source: project manifest)");
  } else if (readme?.["first_paragraph"]) {
    lines.push(`  ${S(readme["first_paragraph"])}`);
    lines.push(`  (source: first prose line of ${S(readme["file"])})`);
  } else {
    lines.push("  Not stated in the manifest or the README.");
    unclear.push("What the project is for — neither the manifest nor the README says.");
  }
  lines.push("");

  // 2 -----------------------------------------------------------------
  lines.push("STACK");
  lines.push(`  ecosystems      ${eco.length ? eco.join(", ") : "none detected"}`);
  lines.push(`  manifests       ${((probe["manifests_present"] as string[]) ?? []).join(", ") || "none"}`);
  lines.push(`  lockfiles       ${locks.length ? locks.join(", ") : "NONE COMMITTED"}`);
  for (const [key, label] of [["node", "Node"], ["python", "Python"], ["go", "Go"], ["rust", "Rust"]]) {
    const d = probe[key] as Dict | undefined;
    if (!d) continue;
    const floor = (d["engines"] as Dict | undefined)?.["node"] ?? d["requires_python"]
      ?? d["go_version"] ?? d["rust_edition"];
    if (floor) lines.push(`  ${label.padEnd(15)} requires ${S(floor)}`);
    if (d["dependency_count"] != null) {
      lines.push(`  ${(label + " deps").padEnd(15)} ${S(d["dependency_count"])} direct`);
    }
  }
  if (!locks.length) {
    unclear.push("Exact dependency versions — no lockfile is committed, so two installs on different days can resolve differently.");
  }
  lines.push("");

  // 3 -- the section that separates this from a README summary ---------
  lines.push("HOW TO RUN IT");
  const claimList = (claims?.["claims"] as Dict[]) ?? [];
  if (!claims) {
    lines.push("  Command check unavailable — the claims step did not complete.");
    unclear.push("Whether the documented commands are real — the check did not run.");
  } else if (!claimList.length) {
    lines.push("  The README quotes no runnable commands.");
    unclear.push("How to set the project up — the README quotes no commands at all.");
  } else {
    lines.push("  Every command the README tells you to run, checked against what");
    lines.push("  this project actually defines:");
    lines.push("");
    const LABEL: Record<string, string> = {
      defined: "works",
      tool_missing: "tool missing",
      undefined: "NOT DEFINED",
      unknown: "unresolved",
    };
    for (const c of claimList) {
      const status = S(c["status"]);
      lines.push(`  [${(LABEL[status] ?? status).padEnd(12)}] ${S(c["command"])}`);
      lines.push(`  ${" ".repeat(16)}${S(c["evidence"])}`);
    }
    const rot = claimList.filter((c) => c["status"] === "undefined");
    if (rot.length) {
      lines.push("");
      lines.push(`  ${rot.length} documented command(s) do not exist in this project.`);
      lines.push("  Treat the README as out of date, not as a setup guide.");
    }
    const missing = claimList.filter((c) => c["status"] === "tool_missing");
    if (missing.length) {
      lines.push("");
      lines.push(`  ${missing.length} command(s) are real but need a tool you do not have installed.`);
    }
  }
  const defined = (claims?.["defined_scripts"] as string[]) ?? [];
  if (defined.length) {
    lines.push("");
    lines.push(`  Defined but undocumented: ${defined.join(", ")}`);
  }
  for (const [key, label] of [["just_recipes", "justfile recipes"], ["make_targets", "make targets"]]) {
    const v = (claims?.[key] as string[]) ?? [];
    if (v.length) lines.push(`  ${label}: ${v.join(", ")}`);
  }
  lines.push("");

  // 4 -----------------------------------------------------------------
  lines.push("ENTRY POINTS");
  const entries = (probe["entry_candidates"] as string[]) ?? [];
  if (entries.length) {
    for (const e of entries) lines.push(`  ${e}`);
  } else {
    lines.push("  No conventional entry point at the top level.");
    unclear.push("Where execution starts — no conventional entry point at the top level.");
  }
  lines.push("");

  // 5 -----------------------------------------------------------------
  lines.push("LAYOUT");
  for (const s of (probe["structure"] as Dict[]) ?? []) {
    const files = s["files"] == null ? "" : `${S(s["files"])} files`;
    lines.push(`  ${S(s["path"]).padEnd(32)}${files}`);
  }
  lines.push("");

  // 6 -----------------------------------------------------------------
  lines.push("RISK FLAGS");
  const risks: string[] = [];
  if (!locks.length) risks.push("No lockfile committed — builds are not reproducible.");
  if (!((probe["test_dirs"] as string[]) ?? []).length) risks.push("No test directory found.");
  if (!((probe["ci"] as string[]) ?? []).length) risks.push("No CI configuration found.");
  const secrets = (probe["committed_secret_files"] as string[]) ?? [];
  if (secrets.length) risks.push(`Secret-shaped files committed: ${secrets.join(", ")}`);
  if (probe["has_license"] === false) risks.push("No LICENSE file — reuse terms are unstated.");
  if (git["last_commit_date"]) risks.push(`Last commit: ${S(git["last_commit_date"])}`);
  if (risks.length) { for (const r of risks) lines.push(`  ${r}`); }
  else lines.push("  None of the usual flags tripped.");
  lines.push("");

  // 7 -- never bluff --------------------------------------------------
  lines.push("WHAT IS UNCLEAR");
  if (git["shallow_clone"] === true) {
    unclear.push("Project history and contributor count — this is a shallow clone, so history was deliberately not fetched.");
  }
  if (unclear.length) { for (const u of unclear) lines.push(`  ${u}`); }
  else lines.push("  Nothing material was left unresolved.");

  const rotCount = claimList.filter((c) => c["status"] === "undefined").length;
  const verdict = rotCount
    ? `${title}: ${rotCount} documented command(s) do not exist`
    : claimList.length
      ? `${title}: documented commands check out`
      : `${title}: briefed, no commands documented`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    repository: probe["root"],
    name: title,
    ecosystems: eco,
    lockfiles: locks,
    entry_points: entries,
    command_claims: claimList,
    claim_summary: claims?.["summary"] ?? null,
    documented_but_undefined: rotCount,
    risk_flags: risks,
    unclear,
    git,
  });
}
