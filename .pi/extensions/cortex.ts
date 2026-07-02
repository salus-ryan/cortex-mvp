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

function shouldBlock(report: ImmuneReport): boolean {
	if (process.env.CORTEX_PI_ENFORCE === "0") return false;
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
		try {
			const report = await postJson<ImmuneReport>("/immune/scan", {
				task: toolTask(event),
				context: { pi: true, tool: event.toolName, input: event.input },
			}, ctx.signal);
			ctx.ui.setStatus("cortex", `Cortex ${report.immune_state ?? "unknown"}:${report.score ?? "?"}`);
			if (shouldBlock(report)) {
				const antigens = (report.antigens || []).map((a) => a.kind).join(", ") || "unknown antigen";
				return { block: true, reason: `Blocked by Cortex immune system: ${antigens}. ${report.recommendation || "Narrow or ask witness."}` };
			}
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
