#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: repo-onboarding-brief
 * description: Onboard to an unfamiliar repository, and check its setup instructions instead of trusting them. Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. Cross-references every command the README tells you to run against what the project actually defines — package.json scripts, Make targets, justfile recipes, Cargo bins, go run/test paths, pyproject scripts, tox envs, nox sessions, Taskfile tasks, compose services, Dockerfiles, and npx binaries declared as dependencies — and against the tools present on your machine — including their versions against the floors the project declares in engines, .nvmrc, requires-python, .python-version, go.mod, rust-toolchain and .tool-versions, read by running `--version` on your own tools, never the project's code — so a documented-but-nonexistent command, or a Node two majors too old, is named before you lose an afternoon to it. Reads README, CONTRIBUTING and docs/ and cites file and line for every command. Lists the environment variables the code reads that no .env.example or doc admits to, split into the ones with no fallback (the process dies on first use) and the ones with a default. Names the floors that contradict each other (a .nvmrc pin an engines range rejects), a second committed lockfile with nothing choosing between them, relative doc links that point at files the tree does not have, and the files that exist in your checkout but git does not track — a stranger's clone will not have them. Ends with FIRST RUN, IN ORDER — toolchain, the install command the committed lockfile implies, env, run, test — so the brief is a sequence, not a list. Also reports stack and version floors, entry points, a layout map, risk flags (no lockfile, no tests, no CI, committed secret-shaped files), and an explicit list of what it could not determine. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
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
 *   version: 0.5.2
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
 * presentation_fixtures:
 *   resolve: resources/presentation-fixtures/resolve/fixture.yaml
 *   probe: resources/presentation-fixtures/probe/fixture.yaml
 *   claims: resources/presentation-fixtures/claims/fixture.yaml
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
  const tc = (claims?.["toolchain"] as Dict[]) ?? [];
  if (tc.length) {
    lines.push("YOUR MACHINE vs THE FLOORS THIS PROJECT DECLARES");
    for (const t of tc) {
      const tag = t["status"] === "ok" ? "ok         " : t["status"] === "below_floor" ? "TOO OLD    "
        : t["status"] === "missing" ? "MISSING    " : "unparsed   ";
      lines.push(`  [${tag}] ${S(t["tool"]).padEnd(8)} wants ${S(t["declared"]).padEnd(14)} you have ${S(t["installed"]) || "nothing on PATH"}   (${S(t["source"])})`);
    }
    const bad = tc.filter((t) => t["status"] === "below_floor" || t["status"] === "missing");
    if (bad.length) lines.push(`  ${bad.length} floor(s) this machine does not meet. Fix these before trusting any command below.`);
    const fc = (claims?.["floor_conflicts"] as Dict[]) ?? [];
    for (const c of fc) {
      lines.push(`  CONTRADICTION  ${S(c["tool"])}: ${S(c["a"])}, but ${S(c["b"])} — both cannot be met.`);
      unclear.push(`Which ${S(c["tool"])} version is actually required — the repository declares two that contradict each other.`);
    }
    lines.push("");
  }
  const lc = claims?.["lockfile_conflict"] as Dict | null;
  if (lc) {
    lines.push(`LOCKFILE CONFLICT  ${((lc["lockfiles"] as string[]) ?? []).join(" + ")} are all committed; decided by ${S(lc["decided_by"])}.`);
    if (!S(lc["decided_by"]).startsWith("packageManager")) unclear.push("Which package manager this project really uses — two lockfiles are committed and nothing picks one.");
    lines.push("");
  }

  lines.push("HOW TO RUN IT");
  const claimList = (claims?.["claims"] as Dict[]) ?? [];
  if (!claims) {
    lines.push("  Command check unavailable — the claims step did not complete.");
    unclear.push("Whether the documented commands are real — the check did not run.");
  } else if (!claimList.length) {
    lines.push("  The README, CONTRIBUTING and docs/ quote no runnable commands.");
    unclear.push("How to set the project up — the README quotes no commands at all.");
  } else {
    const docs = (claims?.["docs_scanned"] as string[]) ?? [];
    lines.push(`  Every command the docs tell you to run (${docs.length ? docs.join(", ") : "README"}), checked`);
    lines.push("  against what this project actually defines:");
    lines.push("");
    const LABEL: Record<string, string> = {
      defined: "works",
      tool_missing: "tool missing",
      undefined: "NOT DEFINED",
      unknown: "unresolved",
    };
    for (const c of claimList) {
      const status = S(c["status"]);
      const where = c["line"] != null ? `${S(c["source"])}:L${S(c["line"])}` : S(c["source"]);
      lines.push(`  [${(LABEL[status] ?? status).padEnd(12)}] ${S(c["command"])}   (${where})`);
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

  // 3b -- the trap that kills a first run --------------------------------
  const env = (claims?.["env"] as Dict) ?? null;
  if (env && Number(env["read_count"]) > 0) {
    const req = (env["undocumented_required"] as Dict[]) ?? [];
    const fb = (env["undocumented_with_fallback"] as string[]) ?? [];
    const dead = (env["documented_never_read"] as string[]) ?? [];
    const srcs = (env["documented_sources"] as string[]) ?? [];
    lines.push(`ENVIRONMENT  ${S(env["read_count"])} variables read by the code · documented in ${srcs.length ? srcs.join(", ") : "no .env.example"}`);
    if (req.length) {
      lines.push(`  ${req.length} read with NO fallback and documented nowhere — the process dies on first use:`);
      for (const r of req.slice(0, 15)) lines.push(`    ${S(r["name"]).padEnd(32)} ${((r["files"] as string[]) ?? []).slice(0, 2).join(", ")}`);
      if (req.length > 15) lines.push(`    … ${req.length - 15} more`);
      unclear.push(`What ${req.length} required environment variable(s) should be set to — the code reads them, nothing documents them.`);
    }
    if (fb.length) lines.push(`  ${fb.length} undocumented but with a default in code: ${fb.slice(0, 10).join(", ")}${fb.length > 10 ? ", …" : ""}`);
    if (dead.length) lines.push(`  ${dead.length} in the example file that nothing reads: ${dead.slice(0, 8).join(", ")}`);
    if (!req.length && !fb.length) lines.push("  Every variable the code reads is documented.");
    if (env["scan_truncated"] === true) lines.push("  (scan stopped at 3000 source files; counts are a floor)");
    lines.push("");
  }

  // 3c -- the sequence, not the list ---------------------------------------
  const fr = (claims?.["first_run"] as Dict[]) ?? [];
  if (fr.length || tc.length) {
    lines.push("FIRST RUN, IN ORDER");
    let n = 1;
    const badTc = tc.filter((t) => t["status"] === "below_floor" || t["status"] === "missing");
    if (tc.length) lines.push(`  ${n++}. toolchain   ${badTc.length ? `fix ${badTc.map((t) => S(t["tool"])).join(", ")} first (see YOUR MACHINE)` : "every declared floor is met"}`);
    for (const st of fr) lines.push(`  ${n++}. ${S(st["step"]).padEnd(11)} ${S(st["command"]).padEnd(34)} ${S(st["why"])}`);
    const reqN = ((env?.["undocumented_required"] as Dict[]) ?? []).length;
    if (reqN) lines.push(`  ${n++}. env         set the ${reqN} undocumented variable(s) above before the run step`);
    lines.push("");
  }

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

  // 5b -- links the docs make that the tree cannot honour --------------------
  const dl = (claims?.["doc_links"] as Dict) ?? null;
  if (dl && Number(dl["checked"]) > 0) {
    const broken = (dl["broken"] as Dict[]) ?? [];
    lines.push(`DOC LINKS  ${S(dl["checked"])} relative links checked · ${S(dl["broken_count"])} point at files that do not exist`);
    for (const b of broken.slice(0, 10)) lines.push(`  ${S(b["doc"])}:L${S(b["line"])}  ${S(b["target"])}`);
    if (Number(dl["broken_count"]) > 10) lines.push(`  … ${Number(dl["broken_count"]) - 10} more`);
    lines.push("");
  }

  // 6 -----------------------------------------------------------------
  lines.push("RISK FLAGS");
  const risks: string[] = [];
  const lof = probe["local_only_files"] as Dict | null;
  if (lof && Number(lof["count"]) > 0) {
    const secretish = (lof["secret_shaped"] as string[]) ?? [];
    risks.push(`${S(lof["count"])} file(s) exist here but git does not track them — a fresh clone will not have them` +
      (secretish.length ? ` (including ${secretish.join(", ")})` : "") + `. e.g. ${((lof["sample"] as string[]) ?? []).slice(0, 4).join(", ")}`);
  }
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
    docs_scanned: claims?.["docs_scanned"] ?? [],
    toolchain: tc,
    environment: env,
    first_run: fr,
    floor_conflicts: claims?.["floor_conflicts"] ?? [],
    lockfile_conflict: lc,
    doc_links: dl,
    local_only_files: lof,
    claim_summary: claims?.["summary"] ?? null,
    documented_but_undefined: rotCount,
    risk_flags: risks,
    unclear,
    git,
  });
}
