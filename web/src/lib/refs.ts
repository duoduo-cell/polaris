// 编号（J-12 岗位、P-3 帖子、Q-8 面试题、R-9 刚现查到的原文、M-5 记忆）只在内部用：回答里查到的换成名字并能点开，其余的去掉。

export const REF = /(?<![A-Za-z0-9])([JPQRM]-\d+)(?!\d)/g;

// 去掉编号后留下的空括号、多余的顿号
export function tidy(text: string): string {
  return text
    .replace(/[（(]\s*(?:编号)?[：:]?\s*[、，,;；/\s]*[)）]/g, "")
    .replace(/\[\s*[、，,;；/\s]*\]/g, "")  // 10-24 演示：“本科在读 [M-3]”去掉编号后留下“[]”
    .replace(/([（(])\s*[、，,;；/]\s*/g, "$1")
    .replace(/([（(])[ \t]+/g, "$1")
    .replace(/[ \t]+([)）])/g, "$1")
    .replace(/\s*[、，,;；/]\s*([)）])/g, "$1")
    .replace(/、{2,}/g, "、")
    .replace(/[、，,]\s*(?=[。.！!？?；;）)])/g, "")
    .replace(/([：:（(]\s*)[、，,]/g, "$1")
    .replace(/编号[：:]\s*(?=[，。；;,.\s]|$)/g, "")
    .replace(/(?:编号为|编号是)\s*的/g, "这个")  // “没有编号为 J-999999 的岗位”去掉编号后读得通
    .replace(/[ \t]{2,}/g, " ");
}

export function hideRefs(text: string | null | undefined): string {
  return tidy((text ?? "").replace(REF, ""));
}

type Node = { type: string; value?: string; url?: string; children?: Node[] };

// remark 插件：known 里有的编号换成可点的名字，没有的直接去掉
export function refsPlugin(known: Map<string, string>) {
  return function plugin() {
    return function transform(tree: Node) {
      function walk(parent: Node) {
        if (!parent.children || ["link", "linkReference", "code", "inlineCode"].includes(parent.type)) return;
        parent.children = parent.children.flatMap((node) => {
          if (node.type !== "text" || !node.value) { walk(node); return [node]; }
          const parts: Node[] = [];
          let buffer = "";
          let start = 0;
          for (const m of node.value.matchAll(REF)) {
            buffer += node.value.slice(start, m.index);
            start = m.index + m[0].length;
            const label = known.get(m[0]);
            if (!label) continue;
            if (buffer) parts.push({ type: "text", value: buffer });
            buffer = "";
            parts.push({ type: "link", url: `#entity-${m[0]}`, children: [{ type: "text", value: label }] });
          }
          buffer += node.value.slice(start);
          if (buffer) parts.push({ type: "text", value: buffer });
          // 去掉编号后清理同一段文字里留下的空括号
          return parts.map((p) => (p.type === "text" ? { ...p, value: tidy(p.value ?? "") } : p))
            .filter((p) => p.type !== "text" || p.value);
        });
      }
      walk(tree);
    };
  };
}
