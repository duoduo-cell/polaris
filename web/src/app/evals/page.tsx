"use client";

import { useEffect, useState } from "react";
import { Card, ErrorNote, Markdown } from "@/components/markdown";
import { api } from "@/lib/client";

type Report = { name: string; title: string };

export default function Evals() {
  const [reports, setReports] = useState<Report[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [markdown, setMarkdown] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Report[]>("reports").then((r) => { setReports(r); if (r.length) setCurrent(r[r.length - 1].name); })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (current) api<{ markdown: string }>(`reports/${current}`).then((r) => setMarkdown(r.markdown)).catch((e) => setError(e.message));
  }, [current]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">评测</h1>
      <p className="text-sm text-muted">每个模块的指标报告，都能用命令重新生成（报告里写着命令）。标准答案分开发集、留出集和测试集，改规则只看开发集。</p>
      <ErrorNote error={error} />
      <div className="flex flex-wrap gap-2">
        {reports.map((r) => (
          <button key={r.name} onClick={() => setCurrent(r.name)}
                  className={`rounded-full border px-3 py-1 text-sm ${current === r.name ? "border-accent text-accent" : "border-line"}`}>
            {r.title}
          </button>
        ))}
      </div>
      {markdown && <Card><Markdown text={markdown} /></Card>}
    </div>
  );
}
