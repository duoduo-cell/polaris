export type Source = {
  source: string; label: string; url: string | null; city?: string | null; raw_id?: string;
  fetched_at?: string | null; published_at?: string | null; access?: string; availability?: string;
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
  score: number; level: "good" | "ok" | "weak"; checks: Check[];
  skills: { required: string[]; have: string[]; missing: string[]; known: boolean };
};
export type Result = {
  id: string; kind: "job" | "interview" | "question" | "referral" | "recruitment" | "study" | "post";
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
  recruitment: "招聘线索", study: "备考整理", post: "求职资料",
};
