import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

type ImmuneReport = {
	status?: string;
	immune_state?: string;
	score?: number;
	antigens?: Array<{ kind: string; severity: number; response: string; detail?: string }>;
	recommendation?: string;
	may_execute?: boolean;
};

const DEFAULT_CORTEX_URL = "https://cortex-pid1-production.up.railway.app";

const CORTEX_AT_COMPLETIONS = [
	{ value: "@start", label: "@start", description: "Start a Cortex block / ritual" },
	{ value: "@tool", label: "@tool", description: "SCL tool action" },
	{ value: "@memory", label: "@memory", description: "SCL memory read/write/compress" },
	{ value: "@halt", label: "@halt", description: "SCL answer/fail/defer" },
	{ value: "@repair", label: "@repair", description: "SCL rollback/diagnose/patch" },
	{ value: "@state", label: "@state", description: "SCL state update/snapshot" },
	{ value: "@verify", label: "@verify", description: "SCL verification action" },
	{ value: "@budget", label: "@budget", description: "SCL budget check/report" },
];

function cortexUrl(): string {
	return (process.env.CORTEX_URL || DEFAULT_CORTEX_URL).replace(/\/$/, "");
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
	const res = await fetch(`${cortexUrl()}${path}`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify(body),
		signal,
	});
	if (!res.ok) throw new Error(`Cortex ${path} returned ${res.status}: ${await res.text()}`);
	return (await res.json()) as T;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
	const res = await fetch(`${cortexUrl()}${path}`, { signal });
	if (!res.ok) throw new Error(`Cortex ${path} returned ${res.status}: ${await res.text()}`);
	return (await res.json()) as T;
}

function toolTask(event: { toolName: string; input: unknown }): string {
	return `Pi tool call: ${event.toolName}\nInput: ${JSON.stringify(event.input)}`;
}

type ConsentGrant = {
	scopes: Set<string>;
	expiresAt: number;
	remainingActions: number;
	witness: string;
	createdAt: number;
};

const consentGrants: ConsentGrant[] = [];

function classifyToolScope(event: { toolName: string; input: unknown }): string {
	const name = event.toolName.toLowerCase();
	const input = JSON.stringify(event.input || {}).toLowerCase();
	if (name.includes("read")) return "repo.read";
	if (name.includes("edit") || name.includes("write")) {
		if (input.includes("docs/") || input.includes("readme") || input.includes(".md")) return "docs.write";
		if (input.includes("tests/")) return "tests.write";
		return "repo.write";
	}
	if (name.includes("bash")) {
		if (/\bgh\s+issue\b/.test(input)) return "github.issue";
		if (/\bgit\s+push\b/.test(input)) return "git.push";
		if (/\bgit\s+commit\b/.test(input)) return "git.commit";
		if (/\b(pytest|npm test|pnpm test)\b/.test(input)) return "verify.run";
		return "shell.run";
	}
	return "tool.run";
}

function activeGrantFor(scope: string): ConsentGrant | undefined {
	const now = Date.now();
	for (const grant of consentGrants) {
		if (grant.expiresAt <= now || grant.remainingActions <= 0) continue;
		if (grant.scopes.has(scope) || grant.scopes.has("all")) return grant;
	}
	return undefined;
}

function consumeGrant(scope: string): ConsentGrant | undefined {
	const grant = activeGrantFor(scope);
	if (grant) grant.remainingActions -= 1;
	return grant;
}

function shouldBlock(report: ImmuneReport, scope?: string): boolean {
	if (process.env.CORTEX_PI_ENFORCE === "0") return false;
	if (scope && activeGrantFor(scope) && report.immune_state !== "quarantine") return false;
	if (report.immune_state === "quarantine") return true;
	if ((report.score ?? 0) >= 76) return true;
	const hard = new Set(["hidden_action", "credential_risk", "self_persistence", "irreversible_action", "oracle_boundary_drift"]);
	return (report.antigens || []).some((a) => hard.has(a.kind));
}

export default function cortexExtension(pi: ExtensionAPI) {
	pi.registerProvider("cortex", {
		name: "Cortex",
		baseUrl: `${cortexUrl()}/v1`,
		apiKey: process.env.CORTEX_API_KEY || "cortex-local",
		api: "openai-completions",
		models: [
			{
				id: "cortex-local-mind-v1",
				name: "Cortex Local Mind v1",
				reasoning: false,
				input: ["text"],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow: 32000,
				maxTokens: 4096,
			},
			{
				id: "cortex-deliberative-v1",
				name: "Cortex Deliberative v1",
				reasoning: false,
				input: ["text"],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow: 32000,
				maxTokens: 4096,
			},
		],
	});

	pi.on("session_start", async (_event, ctx) => {
		ctx.ui.addAutocompleteProvider((current) => ({
			triggerCharacters: ["@"],
			async getSuggestions(lines, line, col, options) {
				const beforeCursor = (lines[line] ?? "").slice(0, col);
				const match = beforeCursor.match(/(?:^|[\s{])@([A-Za-z_-]*)$/);
				if (!match) return current.getSuggestions(lines, line, col, options);

				const prefix = `@${match[1] ?? ""}`;
				const query = prefix.toLowerCase();
				return {
					prefix,
					items: CORTEX_AT_COMPLETIONS.filter((item) => item.value.toLowerCase().startsWith(query)),
				};
			},
			applyCompletion(lines, line, col, item, prefix) {
				return current.applyCompletion(lines, line, col, item, prefix);
			},
			shouldTriggerFileCompletion(lines, line, col) {
				return current.shouldTriggerFileCompletion?.(lines, line, col) ?? true;
			},
		}));

		try {
			const report = await getJson<ImmuneReport>("/immune/report", ctx.signal);
			ctx.ui.setStatus("cortex", `Cortex ${report.immune_state ?? "unknown"}:${report.score ?? "?"}`);
			ctx.ui.notify(`Cortex extension loaded (${cortexUrl()})`, "info");
		} catch (err) {
			ctx.ui.setStatus("cortex", "Cortex offline");
			ctx.ui.notify(`Cortex unavailable: ${String(err)}`, "warning");
		}
	});

	pi.on("tool_call", async (event, ctx) => {
		const scope = classifyToolScope(event);
		try {
			const report = await postJson<ImmuneReport>("/immune/scan", {
				task: toolTask(event),
				context: { pi: true, tool: event.toolName, input: event.input, consent_scope: scope },
			}, ctx.signal);
			ctx.ui.setStatus("cortex", `Cortex ${report.immune_state ?? "unknown"}:${report.score ?? "?"}`);
			if (shouldBlock(report, scope)) {
				const antigens = (report.antigens || []).map((a) => a.kind).join(", ") || "unknown antigen";
				return { block: true, reason: `Blocked by Cortex immune system: ${antigens}. Scope=${scope}. Use /cortex-grant ${scope} 10 30 to approve a bounded batch.` };
			}
			const grant = consumeGrant(scope);
			if (grant) ctx.ui.setStatus("cortex", `Cortex grant ${scope}:${grant.remainingActions}`);
		} catch (err) {
			if (process.env.CORTEX_PI_FAIL_OPEN === "1") return undefined;
			return { block: true, reason: `Cortex immune scan unavailable; fail-closed. ${String(err)}` };
		}
		return undefined;
	});

	pi.registerCommand("cortex-status", {
		description: "Show Cortex PID-1 status",
		handler: async (_args, ctx) => {
			const status = await getJson<any>("/pid1", ctx.signal);
			ctx.ui.notify(`PID1=${status.is_pid1} children=${Object.keys(status.children || {}).join(", ")}`, "info");
		},
	});

	pi.registerCommand("cortex-immune", {
		description: "Show Cortex immune report",
		handler: async (_args, ctx) => {
			const report = await getJson<ImmuneReport>("/immune/report", ctx.signal);
			const antigens = (report.antigens || []).map((a) => a.kind).join(", ") || "none";
			ctx.ui.notify(`Immune ${report.immune_state}:${report.score}\nAntigens: ${antigens}\n${report.recommendation || ""}`, "info");
		},
	});

	pi.registerCommand("cortex-grant", {
		description: "Grant scoped Cortex consent: /cortex-grant <scope[,scope]> [actions=10] [minutes=30]",
		handler: async (args, ctx) => {
			const parts = args.trim().split(/\s+/).filter(Boolean);
			if (!parts.length) {
				ctx.ui.notify("Usage: /cortex-grant docs.write,github.issue 10 30", "warning");
				return;
			}
			const scopes = new Set(parts[0].split(",").map((s) => s.trim()).filter(Boolean));
			const actions = Math.max(1, Math.min(50, Number(parts[1] || 10)));
			const minutes = Math.max(1, Math.min(240, Number(parts[2] || 30)));
			const grant: ConsentGrant = {
				scopes,
				expiresAt: Date.now() + minutes * 60_000,
				remainingActions: actions,
				witness: process.env.USER || "human-user",
				createdAt: Date.now(),
			};
			consentGrants.push(grant);
			ctx.ui.notify(`Cortex grant active: ${Array.from(scopes).join(", ")} for ${actions} actions / ${minutes}m`, "success");
		},
	});

	pi.registerCommand("cortex-grants", {
		description: "Show active Cortex scoped consent grants",
		handler: async (_args, ctx) => {
			const now = Date.now();
			const active = consentGrants.filter((g) => g.expiresAt > now && g.remainingActions > 0);
			if (!active.length) {
				ctx.ui.notify("No active Cortex grants", "info");
				return;
			}
			const lines = active.map((g) => `${Array.from(g.scopes).join(",")}: ${g.remainingActions} actions, ${Math.ceil((g.expiresAt - now) / 60000)}m left`);
			ctx.ui.notify(`Active Cortex grants:\n${lines.join("\n")}`, "info");
		},
	});

	pi.registerCommand("cortex-verify", {
		description: "Ask Cortex to run allowlisted repo verification",
		handler: async (args, ctx) => {
			const scope = args.trim() || "quick";
			const report = await postJson<any>("/repo/verify", { scope }, ctx.signal);
			ctx.ui.notify(`Repo verification ${report.status}\n${report.command?.join(" ") || ""}`, report.status === "pass" ? "success" : "warning");
		},
	});

	pi.registerCommand("cortex-deliberate", {
		description: "Ask Cortex to deliberate on text",
		handler: async (args, ctx) => {
			const task = args.trim();
			if (!task) {
				ctx.ui.notify("Usage: /cortex-deliberate <task>", "warning");
				return;
			}
			const report = await postJson<any>("/deliberate", { task, authority: "interpret", context: { pi: true } }, ctx.signal);
			ctx.ui.notify(`Recommendation: ${report.recommendation?.kind}\n${report.recommendation?.summary}`, "info");
		},
	});
}
