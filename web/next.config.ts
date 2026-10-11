import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,  // 开发时左下角的 Next 标记会挡住侧栏的"北极星状态"；编译和运行错误照样会弹出来
};

export default nextConfig;
