#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: import-alias-drift
 * description: TypeScript says `@/lib/db` means `src/lib/db`. Your bundler and your test runner each keep their own copy of that map, and nothing checks that the copies agree — so the editor is happy, `tsc` is happy, and the test run cannot find the module. Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. This reads every path alias declared in a tsconfig or jsconfig — the extends chain merged in, baseUrl honoured, comments and trailing commas parsed rather than choked on — counts how many files import each one and how many of those are tests, then checks each alias against the resolvers that have to mirror it by hand — vite, vitest, jest (a config file or the jest field of package.json), webpack and rollup. A resolver that reads tsconfig itself cannot drift, so vite-tsconfig-paths, ts-jest's pathsToModuleNameMapper, tsconfig-paths-webpack-plugin and Next.js are named as mirrored instead of being reported. A bundler config is a program, not data, so only the literal keys of an alias or moduleNameMapper block are read, and a block that spreads or computes its keys is reported as unreadable rather than as empty — those are different statements and only one of them is a finding. Also reports an alias whose target is not on disk, separating a path that is simply gone from one that lives under lib/, dist/ or node_modules and is absent from a fresh checkout by design; an alias no file imports; a mapping the resolver has that no tsconfig declares, which type-checks as an error while the bundle works; and a project reference pointing at a directory with no tsconfig in it. A jest moduleNameMapper key is a regular expression, so `^@/(.*)$` is matched against `@/*` in both directions and an asset stub like a css mock is never counted as an alias. Only a tsconfig at the repository root is measured against the root's bundler configs — one inside a test corpus answers to a build this play never saw. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
 * source: https://github.com/PugarHuda/rote-repo-onboarding-brief
 * tags:
 * - domain-build-tooling
 * - job-config-review
 * - audience-developers
 * - effect-read-only
 * discoverability:
 *   tags:
 *   - domain-build-tooling
 *   - job-config-review
 *   - audience-developers
 *   - effect-read-only
 * metadata:
 *   rote_version: 0.79.0
 *   version: 0.1.2
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
 *   example: https://github.com/nuxt/nuxt
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
 *     timeout_ms: 180000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{aliases.py}'
 *     - '@resolve{.stdout.text}'
 * ---
 */

// One alias, several maps: tsconfig for the type checker, resolve.alias for the
// bundler, moduleNameMapper for the test runner. Nothing keeps them in step, and
// the failure only shows up in whichever of the three you run last.

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
    : "the repository was resolved but its import aliases could not be audited";
  out.human(`No audit: ${why}.`);
  out.summary("audit unavailable");
  out.result({ ok: false, unavailable: why });
} else {
  const aliases = (data["aliases"] as Dict[]) ?? [];
  const resolvers = (data["resolvers"] as Dict[]) ?? [];
  const drift = (data["drift"] as Dict[]) ?? [];
  const dead = (data["dead_targets"] as Dict[]) ?? [];
  const unbuilt = (data["unbuilt_targets"] as Dict[]) ?? [];
  const unused = (data["unused"] as string[]) ?? [];
  const resolverOnly = (data["resolver_only"] as Dict[]) ?? [];
  const badRefs = (data["bad_references"] as Dict[]) ?? [];
  const notes = (data["notes"] as string[]) ?? [];
  const aliasN = Number(data["alias_count"] ?? aliases.length);
  const driftN = Number(data["drift_count"] ?? drift.length);
  const configs = (data["configs"] as string[]) ?? [];

  const lines: string[] = [];
  const where = configs.length ? configs.slice(0, 3).join(", ") + (configs.length > 3 ? `, +${configs.length - 3} more` : "")
    : "no tsconfig or jsconfig declares paths";
  lines.push(`IMPORT ALIAS DRIFT · ${where} · ${aliasN} aliases · ${resolvers.length} resolver config(s)`);
  lines.push("");

  if (!aliasN) {
    lines.push("  No path aliases are declared, so there is nothing for a bundler to mirror.");
    lines.push("  Every import in this repository is either relative or a package name.");
    lines.push("");
  }

  if (resolvers.length) {
    lines.push("RESOLVERS");
    for (const r of resolvers) {
      const state = r["mirrored"]
        ? "mirrors tsconfig — " + S(r["mirrored"])
        : r["unreadable"]
        ? "alias block is computed or spread in — not read, so nothing here is reported as missing"
        : `${((r["keys"] as string[]) ?? []).length} literal alias key(s)`;
      lines.push(`  ${S(r["resolver"]).padEnd(10)} ${S(r["file"]).padEnd(28)} ${state}`);
    }
    lines.push("");
  }

  if (aliasN) lines.push("DRIFT");
  if (!aliasN) {
    // nothing declared, so "no drift" would be a claim about a question nobody asked
  } else if (drift.length) {
    lines.push("  Declared for the type checker, absent from a resolver that has to mirror it by hand:");
    for (const d of drift) {
      const used = Number(d["files"] ?? 0);
      const tests = Number(d["test_files"] ?? 0);
      const usage = used === 0
        ? "no file imports it, so nothing fails today"
        : `${used} file(s) import it${tests ? `, ${tests} of them under a test path` : ""}`;
      lines.push(`  ${S(d["alias"]).padEnd(28)} in ${S(d["config"])}, not in ${S(d["resolver_file"])} (${S(d["resolver"])})`);
      lines.push(`  ${" ".repeat(28)} ${usage}`);
    }
    if (driftN > drift.length) lines.push(`  … ${driftN - drift.length} more not listed`);
  } else {
    lines.push("  None. Every alias a readable resolver config had to mirror is in it.");
  }
  lines.push("");

  if (resolverOnly.length) {
    lines.push("MAPPED BY THE RESOLVER, DECLARED NOWHERE");
    lines.push("  The bundle resolves these; the type checker does not, so they are errors in the editor:");
    for (const r of resolverOnly) lines.push(`  ${S(r["alias"]).padEnd(28)} ${S(r["resolver_file"])} (${S(r["resolver"])})`);
    lines.push("");
  }

  if (dead.length) {
    lines.push("TARGET NOT ON DISK");
    for (const a of dead) lines.push(`  ${S(a["alias"]).padEnd(28)} -> ${((a["targets"] as string[]) ?? []).join(", ")}`);
    lines.push("");
  }
  if (unbuilt.length) {
    lines.push("TARGET NOT IN THIS CHECKOUT (built or installed, so not a finding)");
    for (const a of unbuilt) lines.push(`  ${S(a["alias"]).padEnd(28)} -> ${((a["targets"] as string[]) ?? []).join(", ")}`);
    lines.push("");
  }

  if (aliases.length) {
    lines.push("ALIASES");
    for (const a of aliases.slice(0, 40)) {
      const used = Number(a["files"] ?? 0);
      lines.push(`  ${S(a["alias"]).padEnd(28)} -> ${((a["targets"] as string[]) ?? []).join(", ").padEnd(34)} ${used} file(s)`);
    }
    if (aliasN > Math.min(aliases.length, 40)) lines.push(`  … ${aliasN - Math.min(aliases.length, 40)} more not listed`);
    lines.push("");
  }

  if (unused.length) {
    lines.push(`UNUSED  ${unused.length} alias(es) no file imports: ${unused.slice(0, 12).join(", ")}`);
    lines.push("");
  }
  if (badRefs.length) {
    lines.push("PROJECT REFERENCES POINTING AT NOTHING");
    for (const b of badRefs) lines.push(`  ${S(b["from"])} -> ${S(b["path"])}`);
    lines.push("");
  }
  if (notes.length) {
    lines.push("COULD NOT BE READ");
    for (const n of notes) lines.push(`  ${n}`);
    lines.push("");
  }
  if (data["scan_capped"]) {
    lines.push(`SCAN CAPPED  ${S(data["files_scanned"])} source files read, then stopped. Import counts are a floor, not a total,`);
    lines.push("             and no alias is called unused on a capped scan.");
    lines.push("");
  }

  lines.push("NOT CHECKED");
  for (const n of (data["not_checked"] as string[]) ?? []) lines.push(`  ${n}`);

  const verdict = !aliasN
    ? "no path aliases declared"
    : `${aliasN} aliases · ${driftN} drifted · ${resolverOnly.length} resolver-only · ${dead.length} target missing`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    root: data["root"],
    configs,
    alias_count: aliasN,
    aliases,
    resolvers,
    drift,
    drift_count: driftN,
    resolver_only: resolverOnly,
    dead_targets: dead,
    unbuilt_targets: unbuilt,
    unused,
    bad_references: badRefs,
    notes,
    files_scanned: data["files_scanned"],
    scan_capped: data["scan_capped"],
    not_checked: data["not_checked"],
  });
}
