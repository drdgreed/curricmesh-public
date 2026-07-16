import { createTheme, type Theme } from "@mui/material/styles";

/**
 * CurricMesh design system — "Polished SaaS + dev-tool accents".
 *
 * Light + dark, built from one `makeTheme(mode)` factory so the semantic
 * tokens (status palette, typography, component shapes) stay shared and only
 * the color surfaces diverge. `theme` (light) is exported as the default.
 */

// Monospace stack reserved for versions / SHAs / keys.
export const fontFamilyMono =
  'ui-monospace, "SFMono-Regular", Menlo, "JetBrains Mono", monospace';

export type ColorMode = "light" | "dark";

// ---------------------------------------------------------------------------
// TypeScript module augmentation: teach MUI about `palette.status.<state>`.
// Without this, `theme.palette.status.draft` would be a type error.
// ---------------------------------------------------------------------------
type StatusKey =
  | "draft"
  | "review"
  | "approved"
  | "active"
  | "archived"
  | "sunset";

type StatusPalette = Record<StatusKey, { main: string; contrastText: string }>;

declare module "@mui/material/styles" {
  interface Palette {
    status: StatusPalette;
  }
  interface PaletteOptions {
    status?: StatusPalette;
  }
}

// Semantic lifecycle-status tokens, per mode. `contrastText` is the chip
// label; colors are tuned for WCAG-AA contrast of the label against `main`.
// Dark uses brighter fills so chips read on `#161B22`-ish paper.
const statusByMode: Record<ColorMode, StatusPalette> = {
  light: {
    draft: { main: "#6B7280", contrastText: "#FFFFFF" }, // grey
    review: { main: "#B45309", contrastText: "#FFFFFF" }, // amber
    approved: { main: "#2563EB", contrastText: "#FFFFFF" }, // blue
    active: { main: "#15803D", contrastText: "#FFFFFF" }, // green
    archived: { main: "#475569", contrastText: "#FFFFFF" }, // slate
    sunset: { main: "#9F1239", contrastText: "#FFFFFF" }, // red-grey
  },
  dark: {
    draft: { main: "#9CA3AF", contrastText: "#0B0F17" },
    review: { main: "#F59E0B", contrastText: "#0B0F17" },
    approved: { main: "#60A5FA", contrastText: "#0B0F17" },
    active: { main: "#4ADE80", contrastText: "#0B0F17" },
    archived: { main: "#94A3B8", contrastText: "#0B0F17" },
    sunset: { main: "#FB7185", contrastText: "#0B0F17" },
  },
};

/**
 * Resolve a lifecycle status string to its token color for the given mode.
 * Falls back to the neutral `draft` grey for unknown states so callers never
 * crash. Defaults to light to stay backward-compatible with existing callers.
 */
export function statusColor(status: string, mode: ColorMode = "light"): string {
  const palette = statusByMode[mode];
  const key = status as StatusKey;
  return (palette[key] ?? palette.draft).main;
}

// Mode-specific surface tokens. Diff add/remove colors live here too so the
// Diff page can read them off the theme (GitHub-dark uses muted green/red).
interface SurfaceTokens {
  diffAddedBg: string;
  diffAddedText: string;
  diffRemovedBg: string;
  diffRemovedText: string;
  diffHunkBg: string;
  diffHunkText: string;
  diffGutterBg: string;
  graphNodeBg: string;
  graphNodeMisalignedBg: string;
  graphNodeBorder: string;
  graphNodeMisalignedBorder: string;
  graphEdge: string;
  graphEdgeLabel: string;
  tileShadow: string;
  tileShadowHover: string;
  tileMisalignedRing: string;
}

declare module "@mui/material/styles" {
  interface Theme {
    surfaces: SurfaceTokens;
  }
  interface ThemeOptions {
    surfaces?: SurfaceTokens;
  }
}

export const surfacesByMode: Record<ColorMode, SurfaceTokens> = {
  light: {
    diffAddedBg: "#e6ffed",
    diffAddedText: "#22543d",
    diffRemovedBg: "#ffeef0",
    diffRemovedText: "#9b2335",
    diffHunkBg: "#f0f8ff",
    diffHunkText: "#0366d6",
    diffGutterBg: "#fafafa",
    graphNodeBg: "#ffffff",
    graphNodeMisalignedBg: "#fff5f5",
    graphNodeBorder: "#334155",
    graphNodeMisalignedBorder: "#d32f2f",
    graphEdge: "#90a4ae",
    graphEdgeLabel: "#1f2937",
    tileShadow: "0 1px 2px rgba(16,24,40,0.06)",
    tileShadowHover: "0 4px 12px rgba(16,24,40,0.12)",
    tileMisalignedRing: "0 0 0 1px rgba(180,83,9,0.25)",
  },
  dark: {
    // GitHub-dark muted add/remove tints.
    diffAddedBg: "rgba(46,160,67,0.18)",
    diffAddedText: "#7ee787",
    diffRemovedBg: "rgba(248,81,73,0.18)",
    diffRemovedText: "#ffa198",
    diffHunkBg: "rgba(56,139,253,0.16)",
    diffHunkText: "#79c0ff",
    diffGutterBg: "#0D1117",
    graphNodeBg: "#1C2333",
    graphNodeMisalignedBg: "#3A1D22",
    graphNodeBorder: "#3A465C",
    graphNodeMisalignedBorder: "#F87171",
    graphEdge: "#5B6678",
    graphEdgeLabel: "#e2e8f0",
    tileShadow: "0 1px 2px rgba(0,0,0,0.5)",
    tileShadowHover: "0 4px 14px rgba(0,0,0,0.6)",
    tileMisalignedRing: "0 0 0 1px rgba(245,158,11,0.35)",
  },
};

const lightPalette = {
  mode: "light" as const,
  primary: { main: "#4F46E5" }, // indigo
  secondary: { main: "#0D9488" }, // teal
  success: { main: "#15803D" },
  warning: { main: "#B45309" },
  error: { main: "#B91C1C" },
  info: { main: "#2563EB" },
  background: {
    default: "#F7F8FA", // soft off-white
    paper: "#FFFFFF",
  },
  text: {
    primary: "#171B24",
    secondary: "#374151", // darkened from #5B6472 for legibility (esp. when the page is scaled down in demos)
    disabled: "#5B6472",
  },
  divider: "#E3E6EC",
  status: statusByMode.light,
};

const darkPalette = {
  mode: "dark" as const,
  primary: { main: "#818CF8" }, // brightened indigo for contrast on dark
  secondary: { main: "#2DD4BF" }, // brightened teal
  success: { main: "#4ADE80" },
  warning: { main: "#F59E0B" },
  error: { main: "#F87171" },
  info: { main: "#60A5FA" },
  background: {
    default: "#0B0F17",
    paper: "#161B22",
  },
  text: {
    primary: "#E6EDF3",
    secondary: "#9BA7B6",
  },
  divider: "#283040",
  status: statusByMode.dark,
};

/**
 * Build a complete theme for the requested mode. Light and dark share every
 * non-color token; only the palette + `surfaces` block differ.
 */
export function makeTheme(mode: ColorMode): Theme {
  return createTheme({
    palette: mode === "dark" ? darkPalette : lightPalette,
    surfaces: surfacesByMode[mode],
    shape: {
      borderRadius: 10,
    },
    typography: {
      fontFamily:
        'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      h4: { fontWeight: 700, letterSpacing: "-0.02em" },
      h5: { fontWeight: 700, letterSpacing: "-0.015em" },
      h6: { fontWeight: 600, letterSpacing: "-0.01em" },
      subtitle2: { fontWeight: 600 },
      button: { fontWeight: 500 },
    },
    components: {
      MuiButton: {
        defaultProps: {
          disableElevation: true,
        },
        styleOverrides: {
          root: {
            textTransform: "none",
            fontWeight: 500,
          },
        },
      },
      MuiCard: {
        defaultProps: {
          elevation: 0,
        },
        styleOverrides: {
          root: ({ theme }) => ({
            borderRadius: 12,
            border: `1px solid ${theme.palette.divider}`,
            boxShadow:
              theme.palette.mode === "dark"
                ? "0 1px 2px rgba(0,0,0,0.5), 0 1px 3px rgba(0,0,0,0.6)"
                : "0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)",
          }),
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            borderRadius: 999, // pill
            fontWeight: 500,
          },
        },
      },
      MuiAppBar: {
        defaultProps: {
          color: "default",
          elevation: 0,
        },
        styleOverrides: {
          root: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            color: theme.palette.text.primary,
            borderBottom: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: ({ theme }) => ({
            backgroundColor: theme.palette.background.paper,
            borderRight: `1px solid ${theme.palette.divider}`,
          }),
        },
      },
      MuiTableCell: {
        styleOverrides: {
          root: ({ theme }) => ({
            padding: "8px 12px",
            borderColor: theme.palette.divider,
          }),
          head: ({ theme }) => ({
            color: theme.palette.text.secondary,
            fontWeight: 600,
            fontSize: "0.75rem",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }),
        },
      },
    },
  });
}

export const lightTheme = makeTheme("light");
export const darkTheme = makeTheme("dark");

/** Default export stays the light theme so existing imports are unaffected. */
export const theme = lightTheme;

export default theme;
