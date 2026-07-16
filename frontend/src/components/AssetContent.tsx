/**
 * AssetContent — renders an asset's stored content in a friendly format instead
 * of a raw JSON/text dump. Handles the shapes the model stores:
 *   - learning_objectives: JSON array [{id, text, children?}] → nested bullet list
 *   - rubric:              JSON {criteria: [{name, weight}]}  → criterion/weight rows
 *   - text kinds:          markdown string                    → headings + bullets
 *   - any other JSON:      pretty-printed                     → readable fallback
 * No markdown dependency — the bodies are simple, so a tiny renderer suffices.
 */

import type { ReactNode } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

function tryParse(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function objectiveText(o: unknown): string {
  if (typeof o === "string") return o;
  if (o && typeof o === "object") {
    const r = o as Record<string, unknown>;
    return String(r.text ?? r.objective ?? r.name ?? JSON.stringify(o));
  }
  return String(o);
}

function objectiveChildren(o: unknown): unknown[] {
  if (o && typeof o === "object") {
    const r = o as Record<string, unknown>;
    const kids =
      r.children ?? r.subobjectives ?? r.sub_objectives ?? r.sub ?? [];
    return Array.isArray(kids) ? kids : [];
  }
  return [];
}

/** Nested bullet list (objectives + any sub-objectives). */
function Bullets({ items, nested }: { items: unknown[]; nested?: boolean }) {
  return (
    <Box component="ul" sx={{ pl: 3, my: 0, "& > li": { mb: 0.6 } }}>
      {items.map((o, i) => {
        const kids = objectiveChildren(o);
        return (
          <li key={i}>
            <Typography
              variant="body2"
              component="span"
              color={nested ? "text.secondary" : "text.primary"}
            >
              {objectiveText(o)}
            </Typography>
            {kids.length > 0 && <Bullets items={kids} nested />}
          </li>
        );
      })}
    </Box>
  );
}

function RubricView({ criteria }: { criteria: Array<Record<string, unknown>> }) {
  return (
    <Box>
      {criteria.map((c, i) => (
        <Box
          key={i}
          sx={{
            display: "flex",
            justifyContent: "space-between",
            gap: 2,
            py: 0.6,
            borderBottom: i < criteria.length - 1 ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography variant="body2">
            {String(c.name ?? c.criterion ?? "—")}
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {typeof c.weight === "number"
              ? `${Math.round(c.weight * 100)}%`
              : String(c.weight ?? "")}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

/** Inline markdown within a line: **bold** and `code` → React nodes. */
export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = new RegExp("(\\*\\*[^*]+\\*\\*|`[^`]+`)", "g");
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) {
      nodes.push(<strong key={`b${key++}`}>{tok.slice(2, -2)}</strong>);
    } else {
      nodes.push(
        <Box
          component="code"
          key={`c${key++}`}
          sx={{
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: "0.85em",
            bgcolor: "action.hover",
            px: 0.5,
            borderRadius: 0.5,
          }}
        >
          {tok.slice(1, -1)}
        </Box>
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes.length ? nodes : [text];
}

/** Minimal markdown to MUI: headings (incl. h3), inline bold + code, bullets, paragraphs. */
function MarkdownView({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      const items = bullets;
      out.push(
        <Box
          component="ul"
          key={`u${out.length}`}
          sx={{ pl: 3, my: 0.5, "& > li": { mb: 0.4 } }}
        >
          {items.map((b, j) => (
            <li key={j}>
              <Typography variant="body2" component="span">
                {renderInline(b)}
              </Typography>
            </li>
          ))}
        </Box>
      );
      bullets = [];
    }
  };
  text.split("\n").forEach((raw) => {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("### ")) {
      flush();
      out.push(
        <Typography key={`h${out.length}`} variant="subtitle2" sx={{ fontWeight: 700, mt: 1, fontSize: "0.92rem" }}>
          {renderInline(line.slice(4))}
        </Typography>
      );
    } else if (line.startsWith("## ")) {
      flush();
      out.push(
        <Typography key={`h${out.length}`} variant="subtitle2" sx={{ fontWeight: 700, mt: 1 }}>
          {renderInline(line.slice(3))}
        </Typography>
      );
    } else if (line.startsWith("# ")) {
      flush();
      out.push(
        <Typography key={`h${out.length}`} variant="subtitle1" sx={{ fontWeight: 700 }}>
          {renderInline(line.slice(2))}
        </Typography>
      );
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      bullets.push(line.slice(2));
    } else if (line.trim() === "") {
      flush();
    } else {
      flush();
      out.push(
        <Typography key={`p${out.length}`} variant="body2">
          {renderInline(line)}
        </Typography>
      );
    }
  });
  flush();
  return <Box>{out}</Box>;
}

export function AssetContent({
  kind,
  content,
}: {
  kind: string;
  content: string | null;
}) {
  if (!content || !content.trim()) {
    return (
      <Typography variant="body2" color="text.secondary">
        (no inline content)
      </Typography>
    );
  }
  const parsed = tryParse(content);

  if (kind === "learning_objectives" && Array.isArray(parsed)) {
    return <Bullets items={parsed} />;
  }
  if (
    kind === "rubric" &&
    parsed &&
    typeof parsed === "object" &&
    Array.isArray((parsed as { criteria?: unknown }).criteria)
  ) {
    return (
      <RubricView
        criteria={(parsed as { criteria: Array<Record<string, unknown>> }).criteria}
      />
    );
  }
  // Unlabeled JSON array still reads well as bullets.
  if (Array.isArray(parsed)) return <Bullets items={parsed} />;
  // Any other JSON object → readable pretty-print (better than one-line).
  if (parsed && typeof parsed === "object") {
    return (
      <Box
        component="pre"
        sx={{
          m: 0,
          whiteSpace: "pre-wrap",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          fontSize: 12.5,
          lineHeight: 1.5,
        }}
      >
        {JSON.stringify(parsed, null, 2)}
      </Box>
    );
  }
  // Plain text / markdown.
  return <MarkdownView text={content} />;
}
