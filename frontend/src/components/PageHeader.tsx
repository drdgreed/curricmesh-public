import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import type { ReactNode } from "react";

interface PageHeaderProps {
  /** Page title rendered as the dominant heading. */
  title: string;
  /** Optional supporting line beneath the title. */
  subtitle?: string;
  /** Optional right-aligned actions slot (buttons, menus, etc.). */
  actions?: ReactNode;
}

/**
 * Consistent page header band: a title (h5), an optional subtitle, and an
 * optional right-aligned actions slot. Used at the top of each page body.
 */
export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <Stack
      direction="row"
      sx={{
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 2,
        mb: 3,
      }}
    >
      <Box>
        <Typography variant="h5" component="h1">
          {title}
        </Typography>
        {subtitle && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && (
        <Box sx={{ flexShrink: 0, display: "flex", gap: 1 }}>{actions}</Box>
      )}
    </Stack>
  );
}

export default PageHeader;
