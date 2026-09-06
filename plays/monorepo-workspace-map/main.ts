#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: monorepo-workspace-map
 * description: Map a monorepo's package boundaries — which workspace packages exist, which depend on which, which are leaves nobody imports, and which dependency cycles exist. Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. Also reports version skew, where the same external dependency is pinned differently in different packages, which is the papercut that builds fine and then breaks once at runtime; internal version mismatch, where a package asks for a range of a sibling that the workspace copy does not satisfy, so the package manager silently installs it from the registry instead of linking it; unlisted packages, directories holding a manifest that no workspace glob covers; and, when turbo.json is present, pipeline coverage — which packages have no script for a task the pipeline expects and are therefore skipped silently. Understands npm and yarn workspaces, pnpm workspaces, Cargo workspaces, go.work, uv workspaces, and lerna. If the repository is not a monorepo it says so and lists every workspace definition it looked for, rather than returning an empty map. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
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
 *   version: 0.3.1
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
 *   description: 'Monorepo to map: a git URL (https://, ssh://, git@host:owner/name) or a path to a local checkout'
 *   example: https://github.com/vuejs/core
 * - name: branch
 *   type: string
 *   required: false
 *   default: ''
 *   description: Branch or tag to inspect. Ignored for a local path; defaults to the repository default branch.
 * presentation_fixtures:
 *   resolve: resources/presentation-fixtures/resolve/fixture.yaml
 *   map: resources/presentation-fixtures/map/fixture.yaml
 * steps:
 *   resolve:
 *     type: process.exec
 *     timeout_ms: 180000
 *     argv:
 *     - sh
 *     - '@resource{resolve.sh}'
 *     - $repo
 *     - $branch
 *   map:
 *     type: process.exec
 *     timeout_ms: 120000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{workspaces.py}'
 *     - '@resolve{.stdout.text}'
 * ---
 */

// Orientation matters most exactly where nothing covers it: a monorepo, where
// "what is this package allowed to import" is the question you actually have.
//
// Two findings carry this Play. Cycles, because a dependency cycle between
// workspace packages is a boundary that has already failed. And version skew —
// the same external dependency pinned differently in different packages — which
// installs cleanly, passes CI, and then breaks once at runtime in the one
// package that got the odd pin.
//
// A repository that is not a monorepo gets a stated answer and the list of
// definitions that were checked, never an empty map that reads like "no
// packages found".

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

const mapStep = ctx.step(stepName("map"));
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

const data = bodyOf(mapStep);
const S = (v: unknown) => String(v ?? "");

if (!data) {
  const why = resolveStep.outcome.status !== "completed" && resolveStep.outcome.status !== "restored"
    ? "the repository could not be resolved — check the URL, the branch, and your network access"
    : "the repository was resolved but could not be mapped";
  out.human(`No map: ${why}.`);
  out.summary("map unavailable");
  out.result({ ok: false, unavailable: why });
} else if (data["is_monorepo"] === false) {
  const lines = [
    "NOT A MONOREPO",
    "",
    `  ${S(data["root"])}`,
    "",
    `  ${S(data["reason"])}`,
    "",
    "  If you expected a workspace here, the definition is missing or uses a",
    "  layout this Play does not read yet.",
  ];
  out.human(lines.join("\n"));
  out.summary("not a monorepo");
  out.result({ ok: true, is_monorepo: false, root: data["root"], reason: data["reason"] });
} else {
  const members = (data["members"] as Dict[]) ?? [];
  const edges = (data["internal_edges"] as string[][]) ?? [];
  const cycles = (data["cycles"] as string[][]) ?? [];
  const skew = (data["version_skew"] as Dict[]) ?? [];
  const leaves = (data["leaf_packages"] as string[]) ?? [];
  const skipped = (data["skipped"] as Dict[]) ?? [];

  const dependents = new Map<string, string[]>();
  for (const [from, to] of edges) {
    dependents.set(to, [...(dependents.get(to) ?? []), from]);
  }

  const lines: string[] = [];
  lines.push(`WORKSPACE MAP · ${S(data["workspace_kind"])} · ${S(data["member_count"])} packages`);
  lines.push("");

  lines.push("PACKAGES");
  if (Number(data["members_omitted"]) > 0) {
    lines.push(`  (showing ${members.length} of ${S(data["member_count"])} packages)`);
  }
  for (const m of members) {
    const name = S(m["name"]);
    const used = dependents.get(name)?.length ?? 0;
    const dependsOn = edges.filter(([f]) => f === name).length;
    // Pad, then always emit a separator: padEnd adds nothing when a value is
    // already at the column width, which silently glues the columns together.
    lines.push(`  ${name.padEnd(34)}  ${S(m["path"]).padEnd(32)}  ` +
      `used by ${String(used).padStart(2)} · depends on ${dependsOn}`);
  }
  lines.push("");

  if (edges.length) {
    lines.push("INTERNAL DEPENDENCIES");
    for (const [from, to] of edges) lines.push(`  ${from}  ->  ${to}`);
    if (Number(data["edges_omitted"]) > 0) lines.push(`  … ${S(data["edges_omitted"])} more edges not listed`);
    lines.push("");
  }

  lines.push("CYCLES");
  if (cycles.length) {
    for (const c of cycles) lines.push(`  ${c.join(" -> ")}`);
    lines.push("");
    lines.push("  A cycle between workspace packages is a boundary that has already failed.");
  } else {
    lines.push("  None. The package graph is acyclic.");
  }
  lines.push("");

  lines.push("VERSION SKEW");
  if (skew.length) {
    lines.push("  The same external dependency, pinned differently across packages:");
    lines.push("");
    for (const s of skew) {
      const total = Number(s["package_count"] ?? 0);
      const shown = Object.keys((s["versions"] as Dict) ?? {}).length;
      lines.push(`  ${S(s["dependency"])}  (${total} packages, ${((s["distinct_versions"] as string[]) ?? []).join(" / ")})`);
      for (const [pkg, ver] of Object.entries((s["versions"] as Dict) ?? {})) {
        lines.push(`    ${String(ver).padEnd(18)}${pkg}`);
      }
      if (total > shown) lines.push(`    … ${total - shown} more packages`);
    }
    lines.push("");
    lines.push("  `catalog:` and `workspace:` are indirections, not versions — a package");
    lines.push("  showing a literal range next to them has opted out of the shared pin.");
  } else {
    lines.push("  None. Every shared external dependency agrees across packages.");
  }
  lines.push("");

  const mism = (data["internal_version_mismatch"] as Dict[]) ?? [];
  lines.push("INTERNAL VERSION MISMATCH");
  if (mism.length) {
    lines.push("  A package asks for a range of a sibling that the workspace copy does not satisfy,");
    lines.push("  so the package manager resolves it from the registry instead of linking it:");
    for (const m of mism) {
      lines.push(`  ${S(m["package"])}  wants ${S(m["depends_on"])}@${S(m["wants"])}  ·  workspace has ${S(m["workspace_has"])}`);
    }
  } else {
    lines.push("  None. Every internal range is satisfied by the workspace copy (workspace:/catalog: links are not ranges).");
  }
  lines.push("");

  const unlisted = (data["unlisted_packages"] as string[]) ?? [];
  if (unlisted.length) {
    lines.push("UNLISTED PACKAGES");
    lines.push("  A manifest lives here but no workspace glob covers it, so it is neither linked nor built:");
    for (const u of unlisted) lines.push(`  ${u}`);
    lines.push("");
  }

  const pipe = data["pipeline"] as Dict | null;
  if (pipe && Array.isArray(pipe["tasks"]) && (pipe["tasks"] as Dict[]).length) {
    lines.push(`PIPELINE COVERAGE (${S(pipe["file"])})`);
    lines.push("  A task turbo expects that a package has no script for is skipped silently, not failed:");
    for (const t of pipe["tasks"] as Dict[]) {
      const without = (t["packages_without"] as string[]) ?? [];
      const n = Number(t["without_count"] ?? without.length);
      lines.push(`  ${S(t["task"]).padEnd(12)} ${String(t["packages_with_script"]).padStart(4)} packages run it · ${String(n).padStart(4)} skip it${n ? "  " + without.slice(0, 6).join(", ") + (n > 6 ? ", …" : "") : ""}`);
    }
    lines.push("");
  }

  if (leaves.length) {
    lines.push("LEAF PACKAGES");
    lines.push("  Nothing else in this workspace depends on these, so they are apps,");
    lines.push("  tooling, or dead weight:");
    for (const l of leaves) lines.push(`  ${l}`);
    lines.push("");
  }

  if (skipped.length) {
    lines.push("NOT MAPPED");
    for (const s of skipped) lines.push(`  ${S(s["path"])} — ${S(s["why"])}`);
    lines.push("");
  }

  const problems = cycles.length + skew.length + mism.length;
  const verdict = problems === 0
    ? `${S(data["member_count"])} packages, clean boundaries`
    : `${S(data["member_count"])} packages · ${cycles.length} cycle(s) · ${skew.length} skewed dependency(ies) · ${mism.length} internal mismatch(es)`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    is_monorepo: true,
    root: data["root"],
    workspace_kind: data["workspace_kind"],
    member_count: members.length,
    members,
    internal_edges: edges,
    cycles,
    version_skew: skew,
    leaf_packages: leaves,
    internal_version_mismatch: mism,
    unlisted_packages: unlisted,
    pipeline: pipe,
    skipped,
  });
}
