export type Source = {
  source: string; label: string; url: string | null; city?: string | null; raw_id?: string;
  fetched_at?: string | null; published_at?: string | null; access?: string; availability?: string;
  date_kind?: "published" | "updated" | "unknown";
};
export type Followup = {
  saved: boolean; status: string; status_label?: string; stage: string | null; note: string | null;
  next_followup: string | null; revision: number;
  history?: { before: Followup; after: Followup; source_text: string; created_at: string }[];
};
export type Question = {
  question: string; company?: string | null; role?: string | null; round?: string | null;
  category?: string; evidence?: string | null; material?: string; material_label?: string;
  interview_date?: string | null;
};
export type Check = { key: string; label: string; ok: boolean | null; detail: string };
export type Match = {
  criteria?: { city?: string; direction?: string; job_type?: string; max_days_per_week?: number };
  score: number; level: "good" | "ok" | "weak"; checks: Check[];
  eligibility?: "incompatible" | "uncertain";
  note?: string;
  skills: { required: string[]; have: string[]; missing: string[]; not_stated?: string[]; known: boolean };
};

const HARD_CHECKS = new Set(["avoid", "city", "type", "days", "months", "degree", "paper"]);

export function matchSummary(match: Match): { label: string; description: string; ok: boolean | null } {
  if (match.eligibility === "incompatible" || match.checks.some((c) => HARD_CHECKS.has(c.key) && c.ok === false)) {
    return { label: "已知硬条件不符", description: "存在明确条件冲突，匹配分不能抵消；可以查看原始要求核实。", ok: false };
  }
  if (match.checks.some((c) => c.key === "role" && c.ok === false)) {
    return { label: "方向不匹配", description: "当前方向与已记录的求职方向不符；匹配分仅供参考。", ok: false };
  }
  if (match.eligibility === "uncertain" || match.checks.length === 0 || match.checks.some((c) => c.ok === null) || !match.skills.known
      || (match.skills.not_stated ?? match.skills.missing).length > 0) {
    return { label: "信息待确认", description: "未记录或未核实不等于不符合；分数仅对照已知条件，不是录用概率。", ok: null };
  }
  return { label: "已知条件对得上", description: "已知条件一致，仍需核实岗位现状与完整任职要求；分数不是录用概率。", ok: true };
}
export type Result = {
  id: string; kind: "job" | "interview" | "question" | "referral" | "recruitment" | "study" | "post" | "raw";
  display_title: string; title?: string; company?: string | null; unit?: string | null; city?: string | null;
  type?: string | null; job_type?: string | null; sources: Source[]; summary?: string; requirements?: string[];
  directions?: string[]; salary_text?: string; days_per_week?: number; duration_months?: number;
  can_convert?: boolean | null; posted_at?: string; deadline?: string; last_seen?: string; status?: string;
  team?: string | null; has_referral?: boolean;
  published?: string; interview_company?: string | null; companies?: string[]; roles?: string[]; role?: string | null;
  round?: string | null; rounds?: string[]; category?: string; material?: string; material_label?: string;
  question_count?: number; question?: string; evidence?: string | null; interview_date?: string | null;
  body_companies?: string[]; comment_companies?: string[]; question_details?: Question[]; advice?: string[];
  conflicting_fields?: string[]; conflicts?: { field: string; chosen: unknown; rule: string;
    alternatives?: { value: unknown; sources?: string[] }[]; others?: { value: unknown; sources?: string[] }[] }[];
  jd_text?: string; text?: string; followup?: Followup; attribution?: string; match?: Match; listings?: number;
};
export const STATUSES: Record<string, string> = {
  untracked: "未投递", skipped: "暂不考虑", applied: "已投递", interview: "面试中",
  offer: "已获 Offer", rejected: "未通过", withdrawn: "已撤回",
};
export const KINDS: Record<Result["kind"], string> = {
  job: "岗位", interview: "面经", question: "面试题", referral: "内推线索",
  recruitment: "招聘线索", study: "备考整理", post: "求职资料", raw: "刚现查到的",
};
