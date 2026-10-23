import type { ReactNode } from "react";

type Block =
  | { kind: "code"; lang: string; text: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: string[][] }
  | { kind: "quote"; lines: string[] }
  | { kind: "rule" }
  | { kind: "table"; head: string[]; align: ("left" | "center" | "right" | null)[]; rows: string[][] }
  | { kind: "para"; lines: string[] };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const TABLE_ROW = /^\s{0,3}\|.*\|\s*$/;
const TABLE_DELIM = /^\s{0,3}\|?(\s*:?-+:?\s*\|)+\s*(:?-+:?\s*)?\|?\s*$/;

function tableCells(line: string): string[] {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < body.length; i += 1) {
    if (body[i] === "\\" && body[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (body[i] === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += body[i];
    }
  }
  cells.push(cell.trim());
  return cells;
}

function isTableStart(lines: string[], index: number): boolean {
  return TABLE_ROW.test(lines[index] ?? "") && TABLE_DELIM.test(lines[index + 1] ?? "");
}

function indentOf(line: string): number {
  const match = /^\s*/.exec(line);
  return match ? match[0].replace(/\t/g, "    ").length : 0;
}

function listMarker(line: string): { indent: number; ordered: boolean; start: number; rest: string } | null {
  const bullet = BULLET.exec(line);
  if (bullet && !RULE.test(line)) {
    return { indent: indentOf(bullet[1]), ordered: false, start: 1, rest: bullet[3] };
  }
  const ordered = ORDERED.exec(line);
  if (ordered) return { indent: indentOf(ordered[1]), ordered: true, start: Number(ordered[2]), rest: ordered[3] };
  return null;
}

function isBlockStart(line: string): boolean {
  return TABLE_ROW.test(line) || FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || listMarker(line) != null;
}

function parseList(lines: string[], from: number): { block: Block; next: number } {
  const first = listMarker(lines[from])!;
  const items: string[][] = [];
  let index = from;
  while (index < lines.length) {
    const line = lines[index];
    const marker = listMarker(line);
    if (marker && marker.indent <= first.indent) {
      if (marker.ordered !== first.ordered) break;
      items.push([marker.rest]);
      index += 1;
      continue;
    }
    if (!line.trim()) {
      const ahead = lines[index + 1];
      if (ahead != null && ahead.trim() && indentOf(ahead) > first.indent) {
        items[items.length - 1].push("");
        index += 1;
        continue;
      }
      if (ahead != null) {
        const nextMarker = listMarker(ahead);
        if (nextMarker && nextMarker.indent <= first.indent && nextMarker.ordered === first.ordered) {
          index += 1;
          continue;
        }
      }
      break;
    }
    const depth = indentOf(line);
    if (depth > first.indent) {
      items[items.length - 1].push(line.replace(/\t/g, "    ").slice(Math.min(depth, first.indent + 2)));
      index += 1;
      continue;
    }
    if (!isBlockStart(line)) {
      items[items.length - 1].push(line.trim());
      index += 1;
      continue;
    }
    break;
  }
  return { block: { kind: "list", ordered: first.ordered, start: first.start, items }, next: index };
}

export function parseMarkdownBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ kind: "code", lang: fence[2], text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }
    if (isTableStart(lines, index)) {
      const head = tableCells(line);
      const align = tableCells(lines[index + 1]).map((cell) => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        return left && right ? "center" : right ? "right" : left ? "left" : null;
      });
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && TABLE_ROW.test(lines[index])) {
        const cells = tableCells(lines[index]);
        rows.push(head.map((_, col) => cells[col] ?? ""));
        index += 1;
      }
      blocks.push({ kind: "table", head, align, rows });
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ kind: "rule" });
      index += 1;
      continue;
    }
    if (listMarker(line)) {
      const { block, next } = parseList(lines, index);
      blocks.push(block);
      index = next;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (index < lines.length && QUOTE.test(lines[index])) {
        quoted.push(QUOTE.exec(lines[index])![1]);
        index += 1;
      }
      blocks.push({ kind: "quote", lines: quoted });
      continue;
    }
    const para: string[] = [];
    while (index < lines.length && lines[index].trim() && (para.length === 0 || !isBlockStart(lines[index]))) {
      para.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ kind: "para", lines: para });
  }
  return blocks;
}

const REPORT_HREF = /^\/api\/projects\/\d+\/reports\/[A-Za-z0-9][A-Za-z0-9_.-]*\.(xlsx|md|json|csv|html)$/;

export function safeHref(raw: string): string | null {
  const url = raw.trim();
  if (REPORT_HREF.test(url) && !url.includes("..")) return url;
  if (!/^https?:\/\/[^\s]+$/i.test(url)) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

type InlineRule = {
  re: RegExp;
  render: (match: RegExpExecArray, key: string) => ReactNode;
};

const LINK_CLASS = "text-sky-300 underline underline-offset-2 hover:text-sky-200";

function link(href: string | null, label: ReactNode, key: string): ReactNode {
  if (!href) return <span key={key}>{label}</span>;
  return (
    <a key={key} href={href} target="_blank" rel="noopener noreferrer nofollow" className={LINK_CLASS}>
      {label}
    </a>
  );
}

const INLINE_RULES: InlineRule[] = [
  {
    re: /`([^`\n]+)`/,
    render: (m, key) => (
      <code key={key} className="rounded bg-white/[0.08] px-1 py-px font-mono text-[12px] text-white/90">
        {m[1]}
      </code>
    ),
  },
  {
    re: /\[([^\]\n]+)\]\(([^()\s]+)\)/,
    render: (m, key) => link(safeHref(m[2]), renderInline(m[1], `${key}l`), key),
  },
  {
    re: /\bhttps?:\/\/[^\s<>()"'«»]*[^\s<>()"'«».,;:!?]/,
    render: (m, key) => link(safeHref(m[0]), m[0], key),
  },
  {
    re: /\*\*(?=\S)([^\n]*?\S)\*\*/,
    render: (m, key) => (
      <strong key={key} className="font-semibold text-white">
        {renderInline(m[1], `${key}b`)}
      </strong>
    ),
  },
  {
    re: /(?<![\w])__(?=\S)([^\n]*?\S)__(?![\w])/,
    render: (m, key) => (
      <strong key={key} className="font-semibold text-white">
        {renderInline(m[1], `${key}b`)}
      </strong>
    ),
  },
  {
    re: /(?<![*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/,
    render: (m, key) => <em key={key}>{renderInline(m[1], `${key}i`)}</em>,
  },
  {
    re: /(?<![\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\w])/,
    render: (m, key) => <em key={key}>{renderInline(m[1], `${key}i`)}</em>,
  },
  {
    re: /~~(?=\S)([^\n]*?\S)~~/,
    render: (m, key) => <del key={key}>{renderInline(m[1], `${key}s`)}</del>,
  },
];

export function renderInline(text: string, keyBase = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let part = 0;
  while (rest) {
    let best: { match: RegExpExecArray; rule: InlineRule } | null = null;
    for (const rule of INLINE_RULES) {
      const match = rule.re.exec(rest);
      if (match && (!best || match.index < best.match.index)) best = { match, rule };
    }
    if (!best) {
      out.push(rest);
      break;
    }
    if (best.match.index > 0) out.push(rest.slice(0, best.match.index));
    out.push(best.rule.render(best.match, `${keyBase}-${part}`));
    part += 1;
    rest = rest.slice(best.match.index + best.match[0].length);
  }
  return out;
}

function renderLines(lines: string[], key: string): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, index) => {
    if (index > 0) out.push(<br key={`${key}-br${index}`} />);
    out.push(...renderInline(line, `${key}-${index}`));
  });
  return out;
}

const HEADING_CLASS: Record<number, string> = {
  1: "text-[15px] font-semibold",
  2: "text-[14px] font-semibold",
  3: "text-[13px] font-semibold",
};

function renderBlocks(blocks: Block[], key: string, nested: boolean): ReactNode[] {
  return blocks.map((block, index) => {
    const k = `${key}-${index}`;
    if (block.kind === "code") {
      return (
        <pre
          key={k}
          data-lang={block.lang || undefined}
          className="my-1.5 max-h-80 overflow-auto rounded-lg border border-white/[0.08] bg-black/40 px-2.5 py-2 font-mono text-[12px] leading-snug text-white/85"
        >
          <code>{block.text}</code>
        </pre>
      );
    }
    if (block.kind === "heading") {
      const Tag = (`h${Math.min(6, block.level + 1)}` as "h2" | "h3" | "h4" | "h5" | "h6");
      return (
        <Tag key={k} className={`mb-1 mt-2 text-white first:mt-0 ${HEADING_CLASS[block.level] ?? "text-[13px] font-medium"}`}>
          {renderInline(block.text, k)}
        </Tag>
      );
    }
    if (block.kind === "table") {
      const cellAlign = (col: number) =>
        block.align[col] === "right" ? "text-right" : block.align[col] === "center" ? "text-center" : "text-left";
      return (
        <div key={k} className="my-1.5 max-w-full overflow-x-auto rounded-lg border border-white/[0.08]" data-testid="orch-md-table">
          <table className="w-full border-collapse text-[12.5px]">
            <thead className="bg-white/[0.05]">
              <tr>
                {block.head.map((cell, col) => (
                  <th key={col} className={`whitespace-nowrap px-2.5 py-1.5 font-medium text-white/70 ${cellAlign(col)}`}>
                    {renderInline(cell, `${k}-h${col}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-t border-white/[0.06] odd:bg-white/[0.015]">
                  {row.map((cell, col) => (
                    <td key={col} className={`px-2.5 py-1.5 align-top text-white/90 ${cellAlign(col)}`}>
                      {renderInline(cell, `${k}-${rowIndex}-${col}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    if (block.kind === "rule") return <hr key={k} className="my-2 border-white/10" />;
    if (block.kind === "quote") {
      return (
        <blockquote key={k} className="my-1.5 border-l-2 border-white/20 pl-2.5 text-white/70">
          {renderBlocks(parseMarkdownBlocks(block.lines.join("\n")), k, true)}
        </blockquote>
      );
    }
    if (block.kind === "list") {
      const items = block.items.map((item, itemIndex) => {
        const inner = parseMarkdownBlocks(item.join("\n"));
        const ik = `${k}-${itemIndex}`;
        const simple = inner.length === 1 && inner[0].kind === "para";
        return (
          <li key={ik} className="pl-0.5">
            {simple && inner[0].kind === "para" ? renderLines(inner[0].lines, ik) : renderBlocks(inner, ik, true)}
          </li>
        );
      });
      const cls = `my-1 space-y-0.5 pl-5 ${block.ordered ? "list-decimal" : "list-disc"} marker:text-white/40`;
      return block.ordered ? (
        <ol key={k} className={cls} start={block.start !== 1 ? block.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul key={k} className={cls}>
          {items}
        </ul>
      );
    }
    return (
      <p key={k} className={nested ? "my-0.5" : "my-1 first:mt-0 last:mb-0"}>
        {renderLines(block.lines, k)}
      </p>
    );
  });
}

export function ChatMarkdown({ text }: { text: string }) {
  return <>{renderBlocks(parseMarkdownBlocks(text), "md", false)}</>;
}
