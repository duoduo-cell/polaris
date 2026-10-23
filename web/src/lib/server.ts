// 只在 Next.js 服务端用：后端地址和口令。口令从仓库根目录的 .env 读（和后端同一份），不进浏览器、不另存一份。
import "server-only";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

function rootEnvText(): string {
  const selected = process.env.POLARIS_ENV_FILE;
  if (selected !== undefined && !selected.trim()) {
    throw new Error("POLARIS_ENV_FILE为空，拒绝回退到个人配置");
  }
  try {
    const envPath = selected !== undefined
      ? resolve(selected)
      : join(process.cwd(), "..", ".env");
    return readFileSync(envPath, "utf-8");
  } catch {
    if (selected !== undefined) throw new Error("无法读取POLARIS_ENV_FILE，拒绝回退到个人配置或API");
    return ""; // 未显式选择文件时，保留纯环境变量配置。
  }
}

const envText = rootEnvText();
function fromRootEnv(name: string): string | undefined {
  for (const line of envText.split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && m[1] === name) return m[2].replace(/^["']|["']$/g, "");
  }
  return undefined;
}

const configuredApi = process.env.POLARIS_API ?? fromRootEnv("POLARIS_API");
if (demoMode() && !configuredApi) {
  throw new Error("合成演示必须指定独立的POLARIS_API，拒绝回退到个人API");
}
function demoOrigin(value: string): string {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error();
    return url.origin;
  } catch {
    throw new Error("本机演示API必须是无账号、路径、查询或片段的本机HTTP(S)地址");
  }
}
export const API_BASE = demoMode() ? demoOrigin(configuredApi!) : configuredApi ?? "http://127.0.0.1:8000";

export function ownerToken(): string | undefined {
  return process.env.OWNER_TOKEN ?? fromRootEnv("OWNER_TOKEN");
}

export function demoMode(): boolean {
  const value = process.env.DEMO_MODE ?? fromRootEnv("DEMO_MODE") ?? "false";
  if (/^(1|true|t|yes|y|on)$/i.test(value)) return true;
  if (/^(0|false|f|no|n|off)$/i.test(value)) return false;
  throw new Error("DEMO_MODE必须是明确的布尔值，拒绝猜测配置模式");
}
