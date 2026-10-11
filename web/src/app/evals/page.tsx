"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ErrorNote, Markdown, PageHeader, Skeleton } from "@/components/markdown";
import { api } from "@/lib/client";

type Report = { name: string; title: string };

export default function Evals() {
  const [reports, setReports] = useState<Report[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Report[]>("reports").then((r) => { setReports(r); if (r.length) setCurrent(r[r.length - 1].name); })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (current) api<{ markdown: string }>(`reports/${current}`).then((r) => setMarkdown((m) => ({ ...m, [current]: r.markdown })))
      .catch((e) => setError(e.message));
  }, [current]);

  const text = current ? markdown[current] : undefined;
  return (
    <div className="space-y-6">
      <PageHeader title="评测">
        每个模块的指标报告，都能用命令重新生成（报告里写着命令）。标准答案分开发集、留出集和测试集，改规则只看开发集。
      </PageHeader>
      <ErrorNote error={error} />
      <div role="tablist" aria-label="报告" className="flex flex-wrap gap-1 border-b border-line">
        {reports.map((r) => (
          <button key={r.name} role="tab" aria-selected={current === r.name} onClick={() => setCurrent(r.name)}
                  className={`relative px-3 py-2 text-sm transition-colors ${current === r.name ? "text-foreground font-medium" : "text-muted hover:text-foreground"}`}>
            {r.title}
            {current === r.name && <motion.span layoutId="report-underline" className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-foreground" />}
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={current ?? "none"} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}
                    className="rounded-xl border border-line bg-card px-6 py-5">
          {text ? <Markdown text={text} /> : <div className="space-y-3">{[40, 90, 75, 85, 60].map((w) => <Skeleton key={w} className="h-4" style={{ width: `${w}%` }} />)}</div>}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
