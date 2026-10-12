// 手机模式的配对：电脑上运行 start.ps1 -Phone 时，网页服务才会对局域网开放，并带上配对码（环境变量 POLARIS_PAIR_CODE）。
// 手机扫电脑屏幕上的二维码（里面带配对码），换到一个只存哈希的 cookie；之后每个页面和接口都先认这个 cookie。
// 没开手机模式时网页只听本机（127.0.0.1），不需要配对。做法参照 Jupyter 的 ?token= 和 Happy 的扫码配对。
export const PAIR_COOKIE = "polaris_pair";
export const PAIR_DAYS = 90;

export function pairCode(): string | undefined {
  return process.env.POLARIS_PAIR_CODE || undefined;
}

// 用户可能输成小写、带空格或短横线（屏幕上显示成 ABCD-EFGH）
export function normalize(code: string): string {
  return code.replace(/[\s-]/g, "").toUpperCase();
}

export async function pairToken(code: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`polaris-pair:${normalize(code)}`));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function same(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
