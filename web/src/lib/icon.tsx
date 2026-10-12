// 主屏图标：深色圆角方块 + 北极星的四角星（和侧栏的标志一样），按需画成 PNG，不往仓库里放图片文件。
const STAR = "M12 1.5l2.1 7.4L21.5 12l-7.4 2.1L12 22.5l-2.1-8.4L2.5 12l7.4-3.1z";

export function mark(px: number, { rounded, star }: { rounded: boolean; star: number }) {
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
                  background: "#1f1e1c", borderRadius: rounded ? Math.round(px * 0.22) : 0 }}>
      <svg width={Math.round(px * star)} height={Math.round(px * star)} viewBox="0 0 24 24" fill="#faf9f6">
        <path d={STAR} />
      </svg>
    </div>
  );
}
