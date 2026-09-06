#!/usr/bin/env -S rote play run
/**
 * @rote-frontmatter
 * ---
 * name: dependency-trust-diff
 * description: For every npm dependency your lockfile pins, compare the version you actually have with the newest version on the public registry and report the two things a version number hides — the account that published it changed, and the license changed. Read-only, no credentials, no adapters, and nothing the repository ships is ever executed. A package name stays the same through a maintainer handover, a sold project, or a takeover, so the name is not the supply-chain trust anchor; the publishing account and the license are, and neither appears in `npm outdated` or a lockfile diff. Reads package-lock.json, npm-shrinkwrap.json, pnpm-lock.yaml (v5 to v9) and yarn.lock (classic and berry) — direct dependencies by default, scope=all for the whole tree — asks registry.npmjs.org for the locked version and for `latest`, and reports each package as CURRENT, BEHIND, FLAGGED (PUBLISHER_CHANGED, LICENSE_CHANGED, MAINTAINERS_REPLACED, LATEST_DEPRECATED, INSTALL_SCRIPT_ADDED when the newer version gains a preinstall/install/postinstall hook, PROVENANCE_DROPPED when the version you have carries a Sigstore build attestation and the newer one does not, SIZE_JUMP when the unpacked tarball at least triples) or UNCHECKED when the registry did not answer — a fetch failure is never reported as "same". Every checked version is also queried against osv.dev in one batch call, so a KNOWN_VULNERABILITY against the exact version you have is reported first, and an OSV outage is printed as not checked rather than as clean. It says what it cannot know — a publisher change is the account that ran `npm publish`, so a handover to a CI token looks identical to a takeover and is a signal to look at, not a verdict; it reads metadata, never tarballs, so behaviour changes are out of scope; a bun lockfile is named as unsupported rather than silently skipped. Its only network access is anonymous GETs to registry.npmjs.org and one anonymous POST to api.osv.dev. A local path is inspected in place; a URL is shallow-cloned to a temp directory.
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
 *   version: 0.5.0
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
 *   description: 'npm project to check: a git URL (https://, ssh://, git@host:owner/name) or a path to a local checkout with an npm, pnpm or yarn lockfile'
 *   example: https://github.com/axios/axios
 * - name: branch
 *   type: string
 *   required: false
 *   default: ''
 *   description: Branch or tag to inspect. Ignored for a local path; defaults to the repository default branch.
 * - name: scope
 *   type: string
 *   required: false
 *   default: direct
 *   description: '`direct` checks only the root package.json dependencies; `all` checks every package the lockfile pins'
 * - name: max_packages
 *   type: string
 *   required: false
 *   default: '200'
 *   description: Upper bound on packages queried (two registry GETs each). Packages beyond it are counted as skipped, never assumed fine.
 * steps:
 *   resolve:
 *     type: process.exec
 *     timeout_ms: 180000
 *     argv:
 *     - sh
 *     - '@resource{resolve.sh}'
 *     - $repo
 *     - $branch
 *   diff:
 *     type: process.exec
 *     timeout_ms: 300000
 *     depends_on:
 *     - resolve
 *     argv:
 *     - python3
 *     - '@resource{trustdiff.py}'
 *     - '@resolve{.stdout.text}'
 *     - $scope
 *     - $max_packages
 * ---
 */

// `npm outdated` tells you a newer version exists. It does not tell you that the
// newer version was published by someone else, or under a different license.
// Those are the two facts that survive a package name staying the same, and
// they are the two facts this Play reads.

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

const diffStep = ctx.step(stepName("diff"));
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

const data = bodyOf(diffStep);
const S = (v: unknown) => String(v ?? "");

if (!data) {
  const why = resolveStep.outcome.status !== "completed" && resolveStep.outcome.status !== "restored"
    ? "the repository could not be resolved — check the URL, the branch, and your network access"
    : "the repository was resolved but the lockfile could not be checked";
  out.human(`No diff: ${why}.`);
  out.summary("diff unavailable");
  out.result({ ok: false, unavailable: why });
} else if (data["ok"] === false) {
  const lines = ["NOT CHECKED", "", `  ${S(data["root"])}`, "", `  ${S(data["reason"])}`];
  out.human(lines.join("\n"));
  out.summary("not an npm lockfile project");
  out.result({ ok: false, root: data["root"], lockfile: data["lockfile"], reason: data["reason"] });
} else {
  const flagged = (data["flagged"] as Dict[]) ?? [];
  const unchecked = (data["unchecked"] as Dict[]) ?? [];
  const behind = (data["behind"] as Dict[]) ?? [];
  const current = (data["current"] as string[]) ?? [];
  const counts = (data["counts"] as Dict) ?? {};

  const lines: string[] = [];
  lines.push(`DEPENDENCY TRUST DIFF · ${S(data["lockfile"])} · scope ${S(data["scope"])} · ` +
    `${S(data["checked"])} of ${S(data["scope"]) === "all" ? S(data["pinned_total"]) : S(data["direct_total"])} packages checked`);
  if (Number(data["skipped_over_max"]) > 0) {
    lines.push(`  ${S(data["skipped_over_max"])} packages over max_packages were NOT checked.`);
  }
  lines.push("");

  lines.push("FLAGGED");
  if (flagged.length) {
    for (const f of flagged) {
      const have = (f["have"] as Dict) ?? {};
      const latest = (f["latest"] as Dict) ?? {};
      lines.push(`  ${S(f["name"])}  ${S(f["locked"])} -> ${S(latest["version"])}   ` +
        `${((f["findings"] as string[]) ?? []).join(", ")}`);
      if (have["publisher"] !== latest["publisher"]) {
        lines.push(`      publisher   ${S(have["publisher"]) || "?"}  ->  ${S(latest["publisher"]) || "?"}`);
      }
      if (have["license"] !== latest["license"]) {
        lines.push(`      license     ${S(have["license"]) || "none"}  ->  ${S(latest["license"]) || "none"}`);
      }
      const hm = (have["maintainers"] as string[]) ?? [];
      const lm = (latest["maintainers"] as string[]) ?? [];
      if (hm.length && lm.length && !hm.some((m) => lm.includes(m))) {
        lines.push(`      maintainers ${hm.join(" ")}  ->  ${lm.join(" ")}`);
      }
      const vulns = (f["vulns"] as string[]) ?? [];
      if (vulns.length) {
        lines.push(`      KNOWN VULNERABILITY in the version you have: ${vulns.join(", ")}  (OSV)`);
      }
      const hooks = (f["install_scripts_added"] as string[]) ?? [];
      if (hooks.length) {
        lines.push(`      install hooks the newer version ADDS: ${hooks.join(", ")} — code that runs on npm install`);
      }
      const sj = f["size_jump"] as Dict | undefined;
      if (sj) {
        lines.push(`      unpacked size ${Math.round(Number(sj["from"]) / 1024)} KB  ->  ${Math.round(Number(sj["to"]) / 1024)} KB  (${S(sj["factor"])}x)`);
      }
      if (have["provenance"] === true && latest["provenance"] === false) {
        lines.push(`      provenance  attested build  ->  none (the publish path changed)`);
      }
    }
    lines.push("");
    lines.push("  A changed publisher is the account that ran `npm publish`. A handover to a CI");
    lines.push("  token or a co-maintainer looks identical to a takeover: look, do not assume.");
  } else {
    lines.push("  None among the checked packages.");
  }
  lines.push("");

  lines.push(`BEHIND  ${behind.length} package(s) have a newer version, same publisher and license`);
  for (const b of behind.slice(0, 20)) {
    lines.push(`  ${S(b["name"]).padEnd(36)}  ${S(b["locked"]).padEnd(12)} -> ${S(b["latest"])}`);
  }
  if (behind.length > 20) lines.push(`  … and ${behind.length - 20} more`);
  lines.push("");

  lines.push(`CURRENT  ${current.length} package(s) already at latest`);
  lines.push("");

  if (unchecked.length) {
    lines.push(`UNCHECKED  ${unchecked.length} package(s) the registry did not answer for — not "same"`);
    for (const u of unchecked.slice(0, 10)) lines.push(`  ${S(u["name"])}  ${S(u["why"])}`);
    lines.push("");
  }

  const osv = (data["osv"] as Dict) ?? {};
  lines.push(osv["checked"] === true
    ? `OSV  every checked version was queried against osv.dev · ${S(osv["vulnerable_packages"])} with a known advisory`
    : `OSV  vulnerabilities were NOT checked — ${S(osv["note"]) || "no answer"}`);
  lines.push("");

  lines.push("NOT CHECKED");
  for (const n of (data["not_checked"] as string[]) ?? []) lines.push(`  ${n}`);

  const verdict = flagged.length === 0
    ? `${S(data["checked"])} checked · nothing flagged · ${unchecked.length} unchecked`
    : `${flagged.length} flagged of ${S(data["checked"])} checked · ${unchecked.length} unchecked`;

  out.human(lines.join("\n"));
  out.summary(verdict);
  out.result({
    ok: true,
    root: data["root"],
    lockfile: data["lockfile"],
    scope: data["scope"],
    checked: data["checked"],
    skipped_over_max: data["skipped_over_max"],
    counts,
    osv,
    flagged,
    behind,
    current,
    unchecked,
    not_checked: data["not_checked"],
  });
}
