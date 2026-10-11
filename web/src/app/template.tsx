"use client";

// 每次切换页面都会重新挂载：轻轻淡入
import { motion } from "motion/react";

export default function Template({ children }: { children: React.ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: "easeOut" }}>
      {children}
    </motion.div>
  );
}
