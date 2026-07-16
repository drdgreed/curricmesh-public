/**
 * LessonContent — renders a course item's stored content for the learner,
 * resolving the builder's ``![[media:{id}]]`` embed tokens into inline players.
 *
 * Reuse note: the canonical text/markdown/JSON renderer is the shared
 * ``AssetContent`` component (learning_objectives → bullets, rubric → rows,
 * markdown → headings/bullets). We do NOT reinvent it. Instead we split the raw
 * content on the media-embed token the Course Builder inserts
 * (``ObjectiveCanvas.insertMediaRef`` → ``![[media:{assetId}]]``), render each
 * text segment through ``AssetContent``, and render each token as an inline
 * player/image using the fresh presigned URL the ``/learn`` structure response
 * already froze onto ``CourseItem.media`` (matched by media asset id).
 *
 * Structured kinds (learning_objectives / rubric) carry pure JSON and never
 * contain a token, so they pass through as a single segment to AssetContent
 * unchanged — the split is a no-op there.
 */

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { AssetContent } from "../../components/AssetContent";
import type { MediaRef } from "../../api/learn";

// Matches the exact token the builder writes: ![[media:<uuid-or-id>]]
const MEDIA_TOKEN = /!\[\[media:([^\]]+)\]\]/g;

/** Render one presigned media ref inline, keyed by its kind. */
function MediaEmbed({ media }: { media: MediaRef }) {
  const kind = (media.kind ?? "").toLowerCase();
  const label = media.filename ?? "media";

  const wrap = { my: 1.5 } as const;

  if (kind === "image") {
    return (
      <Box sx={wrap}>
        <Box
          component="img"
          src={media.url}
          alt={label}
          sx={{ maxWidth: "100%", borderRadius: 1, display: "block" }}
        />
      </Box>
    );
  }
  if (kind === "video") {
    return (
      <Box sx={wrap}>
        <Box
          component="video"
          src={media.url}
          controls
          sx={{ maxWidth: "100%", borderRadius: 1, display: "block" }}
        />
      </Box>
    );
  }
  if (kind === "audio") {
    return (
      <Box sx={wrap}>
        <Box component="audio" src={media.url} controls sx={{ width: "100%" }} />
      </Box>
    );
  }
  // pdf / doc / other → a clear titled link (opens the presigned URL).
  return (
    <Box sx={wrap}>
      <Link href={media.url} target="_blank" rel="noopener noreferrer">
        {label}
      </Link>
    </Box>
  );
}

/** A subtle placeholder when a token references media not present in the refs. */
function MissingMedia({ id }: { id: string }) {
  return (
    <Typography variant="caption" color="text.secondary" sx={{ display: "block", my: 1 }}>
      [media {id} unavailable]
    </Typography>
  );
}

export function LessonContent({
  kind,
  content,
  media,
}: {
  kind: string;
  content: string;
  media: MediaRef[];
}) {
  // Fast path: no embed token → render the whole body via AssetContent.
  if (!content || !content.includes("![[media:")) {
    return <AssetContent kind={kind} content={content} />;
  }

  const byId = new Map<string, MediaRef>();
  for (const m of media) {
    if (m.id) byId.set(String(m.id), m);
  }

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  MEDIA_TOKEN.lastIndex = 0;
  let key = 0;
  while ((match = MEDIA_TOKEN.exec(content)) !== null) {
    const [token, rawId] = match;
    const before = content.slice(lastIndex, match.index);
    if (before.trim()) {
      nodes.push(<AssetContent key={`t${key}`} kind={kind} content={before} />);
    }
    const id = rawId.trim();
    const ref = byId.get(id);
    nodes.push(
      ref ? (
        <MediaEmbed key={`m${key}`} media={ref} />
      ) : (
        <MissingMedia key={`m${key}`} id={id} />
      )
    );
    lastIndex = match.index + token.length;
    key += 1;
  }
  const tail = content.slice(lastIndex);
  if (tail.trim()) {
    nodes.push(<AssetContent key={`t${key}`} kind={kind} content={tail} />);
  }

  return <Box>{nodes}</Box>;
}

export default LessonContent;
