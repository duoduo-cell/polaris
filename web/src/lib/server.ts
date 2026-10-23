// 只在 Next.js 服务端用：后端地址和口令。口令从仓库根目录的 .env 读（和后端同一份），不进浏览器、不另存一份。
import "server-only";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

function fromRootEnv(name: string): string | undefined {
  try {
    const envPath = process.env.POLARIS_ENV_FILE
      ? resolve(process.env.POLARIS_ENV_FILE)
      : join(process.cwd(), "..", ".env");
    const text = readFileSync(envPath, "utf-8");
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m && m[1] === name) return m[2].replace(/^["']|["']$/g, "");
    }
  } catch {
    // 没有 .env 就当没配
  }
  return undefined;
}

const configuredApi = process.env.POLARIS_API ?? fromRootEnv("POLARIS_API");
if (demoMode() && !configuredApi) {
  throw new Error("合成演示必须指定独立的POLARIS_API，拒绝回退到个人API");
}
export const API_BASE = configuredApi ?? "http://127.0.0.1:8000";

export function ownerToken(): string | undefined {
  return process.env.OWNER_TOKEN ?? fromRootEnv("OWNER_TOKEN");
}

export function demoMode(): boolean {
  return /^(1|true|t|yes|y|on)$/i.test(process.env.DEMO_MODE ?? fromRootEnv("DEMO_MODE") ?? "false");
}
