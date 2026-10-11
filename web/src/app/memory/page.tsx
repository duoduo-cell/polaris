"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, ErrorNote } from "@/components/markdown";
import { FollowupList } from "@/components/result-cards";
import { api } from "@/lib/client";

type Fact = { id: string; attribute: string; value: string; for?: string; scope_label?: string; valid_from?: string; valid_to?: string };
type CardResp = { on: string; facts: Fact[]; waiting_for_confirmation?: { id: string; statement: string }[] };
type Version = Fact & { status: string; kind: string; recorded: string; superseded?: string; from_text: string };

// 卡片上显示的属性名 → 后端属性名（删和查历史要用）
const ATTRS: Record<string, string> = {
  想找的方向: "target_role", 想去的城市: "target_city", 找什么类型: "job_type", 最早什么时候能开始: "available_from",
  每周能到岗几天: "days_per_week", "能实习多久（月）": "duration_months", 毕业年份: "grad_year", 学校: "school", 学历: "degree",
  专业: "major", 会的技术: "skill", 做过的项目: "project", 想去的公司: "company_like", 不想去的公司或团队: "company_avoid",
  投递过的岗位: "applied", 面试进度: "interview_stage", 期望薪资: "salary_expect", 其他硬条件: "constraint",
};
const STATUS: Record<string, [string, "green" | "gray" | "amber" | "red"]> = {
  active: ["生效", "green"], pending: ["待确认", "amber"], superseded: ["被取代", "gray"], retracted: ["作废", "red"],
};

export default function Memory() {
  const [card, setCard] = useState<CardResp | null>(null);
  const [history, setHistory] = useState<{ label: string; versions: Version[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<CardResp>("memory").then(setCard).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function showHistory(label: string, qualifier?: string) {
    const attr = ATTRS[label];
    const q = qualifier ? `?qualifier=${encodeURIComponent(qualifier)}` : "";
    const r = await api<{ versions: Version[] }>(`memory/${attr}/history${q}`);
    setHistory({ label: label + (qualifier ? `（${qualifier}）` : ""), versions: r.versions });
  }

  async function forget(f: Fact) {
    if (!confirm(`忘掉「${f.attribute}：${f.value}」？以后也不会再推断出同样的内容。`)) return;
    const params = new URLSearchParams({ value: f.value, ...(f.for ? { qualifier: f.for } : {}) });
    await api(`memory/${ATTRS[f.attribute]}?${params}`, { method: "DELETE" });
    load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">北极星记着的关于你的事</h1>
      <p className="text-sm text-muted">都是你在对话里说过的（或推断后你确认过的）。每条能看来历、能删；删掉的旧记录留档，以后不会再推断出同样的内容。</p>
      <ErrorNote error={error} />
      {card?.waiting_for_confirmation && card.waiting_for_confirmation.length > 0 && (
        <Card className="space-y-1">
          <p className="font-medium">等你确认的</p>
          {card.waiting_for_confirmation.map((w) => (
            <p key={w.id} className="text-sm"><Badge tone="amber">{w.id}</Badge> {w.statement}（在对话里回答“对”或“不对”就行）</p>
          ))}
        </Card>
      )}
      <Card>
        {card && card.facts.length === 0 && <p className="text-sm text-muted">还没有记住任何事。去对话里说说你想找什么样的实习。</p>}
        <table className="w-full text-sm">
          <tbody>
            {card?.facts.map((f) => (
              <tr key={f.id} className="border-b border-line last:border-0">
                <td className="py-2 pr-3 text-muted whitespace-nowrap">{f.attribute}{f.for ? `（${f.scope_label ?? f.for}）` : ""}</td>
                <td className="py-2 pr-3">{f.value}{f.valid_from ? <span className="text-muted">（{f.valid_from} 起）</span> : null}</td>
                <td className="py-2 pr-3 text-muted">{f.id}</td>
                <td className="py-2 text-right whitespace-nowrap">
                  <button className="text-accent mr-3" onClick={() => showHistory(f.attribute, f.for)}>来历</button>
                  <button className="text-red-600" onClick={() => forget(f)}>忘掉</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {history && (
        <Card className="space-y-3">
          <p className="font-medium">「{history.label}」的全部版本</p>
          {history.versions.map((v) => (
            <div key={v.id} className="border-l-2 border-line pl-3">
              <p className="text-sm">
                <Badge tone={STATUS[v.status]?.[1] ?? "gray"}>{STATUS[v.status]?.[0] ?? v.status}</Badge>{" "}
                <span className="font-medium">{v.value}</span>{" "}
                <span className="text-muted">{v.kind} · {v.recorded} 记下{v.superseded ? ` · ${v.superseded} 被取代` : ""}{v.valid_from ? ` · ${v.valid_from} 起成立` : ""} · {v.id}</span>
              </p>
              <p className="text-sm text-muted">原话：“{v.from_text}”</p>
            </div>
          ))}
        </Card>
      )}
      <FollowupList />
    </div>
  );
}
