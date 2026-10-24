import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 合成演示单独构建到 .next-demo（POLARIS_NEXT_DIST），不和个人网页的 .next 互相清掉
  distDir: process.env.POLARIS_NEXT_DIST || ".next",
  devIndicators: false,  // 开发时左下角的 Next 标记会挡住侧栏的"北极星状态"；编译和运行错误照样会弹出来
  // 手机模式：开发服务默认拦住 localhost 以外来源的开发资源，手机用局域网 IP 或 Tailscale 地址打开时要放行。
  // start.ps1 -Phone 把这台电脑的局域网 IP 写进 POLARIS_DEV_ORIGINS；*.ts.net 是 Tailscale 给的 HTTPS 地址。
  allowedDevOrigins: [...(process.env.POLARIS_DEV_ORIGINS ?? "").split(",").filter(Boolean), "**.ts.net"],
};

export default nextConfig;
