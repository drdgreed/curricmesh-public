import Chip from "@mui/material/Chip";
import type { ChipProps } from "@mui/material/Chip";

export type LifecycleStatus =
  | "draft"
  | "review"
  | "approved"
  | "active"
  | "archived"
  | "sunset";

interface StatusBadgeProps {
  status: string;
}

const STATUS_CONFIG: Record<
  LifecycleStatus,
  { label: string; color: ChipProps["color"]; sx?: ChipProps["sx"] }
> = {
  draft: { label: "Draft", color: "default" },
  review: { label: "Review", color: "warning" },
  approved: { label: "Approved", color: "primary" },
  active: { label: "Active", color: "success" },
  archived: { label: "Archived", color: "default", sx: { opacity: 0.65 } },
  sunset: { label: "Sunset", color: "error" },
};

const FALLBACK = { label: "Unknown", color: "default" as ChipProps["color"] };

export function StatusBadge({ status }: StatusBadgeProps) {
  const cfg = STATUS_CONFIG[status as LifecycleStatus] ?? {
    ...FALLBACK,
    label: status,
  };

  return (
    <Chip
      label={cfg.label}
      color={cfg.color}
      size="small"
      sx={cfg.sx}
      data-testid={`status-badge-${status}`}
    />
  );
}
