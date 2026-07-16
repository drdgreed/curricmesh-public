import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Container from "@mui/material/Container";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import useMediaQuery from "@mui/material/useMediaQuery";

import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import CalendarMonthOutlinedIcon from "@mui/icons-material/CalendarMonthOutlined";
import InboxOutlinedIcon from "@mui/icons-material/InboxOutlined";
import PendingActionsOutlinedIcon from "@mui/icons-material/PendingActionsOutlined";
import InsightsOutlinedIcon from "@mui/icons-material/InsightsOutlined";
import HistoryOutlinedIcon from "@mui/icons-material/HistoryOutlined";
import DifferenceOutlinedIcon from "@mui/icons-material/DifferenceOutlined";
import EditNoteOutlinedIcon from "@mui/icons-material/EditNoteOutlined";
import AutoStoriesOutlinedIcon from "@mui/icons-material/AutoStoriesOutlined";
import SchoolOutlinedIcon from "@mui/icons-material/SchoolOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import LogoutOutlinedIcon from "@mui/icons-material/LogoutOutlined";
import MenuOutlinedIcon from "@mui/icons-material/MenuOutlined";
import Brightness4Icon from "@mui/icons-material/Brightness4";
import Brightness7Icon from "@mui/icons-material/Brightness7";
import Tooltip from "@mui/material/Tooltip";

import { useState, type ReactNode, type ComponentType } from "react";
import {
  Link as RouterLink,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useColorMode } from "../theme/ColorModeContext";

const AUTHOR_ROLES = new Set(["architect", "program_manager"]);
const DRAWER_WIDTH = 248;

interface NavItem {
  label: string;
  to: string;
  icon: ComponentType;
  /**
   * Roles that may see this item. `undefined` = visible to all authenticated
   * users. When set, the current user's role must be in this Set to render the
   * item. Use AUTHOR_ROLES for anything that hits an author-gated API, so a
   * `learner` never sees (and 403s on) authoring tools.
   */
  roles?: Set<string>;
}

const NAV_ITEMS: NavItem[] = [
  // Dashboard: backend has no require_roles guard → safe for all roles.
  { label: "Dashboard", to: "/", icon: DashboardOutlinedIcon },
  // Learn: the learner-facing Course Player; the `learner` role MUST always
  // reach this. Authors keep it too — they use the same delivery UI to preview.
  { label: "Learn", to: "/learn", icon: SchoolOutlinedIcon },
  // Everything below hits author-gated APIs (writer/instructor/analyst roles).
  // Gate them to AUTHOR_ROLES so a `learner` never sees a 403 surface.
  { label: "Course", to: "/course", icon: CalendarMonthOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Dependency Graph", to: "/graph", icon: AccountTreeOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Changes", to: "/changes", icon: DifferenceOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Course Builder", to: "/builder", icon: AutoStoriesOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Propose Change", to: "/propose", icon: EditNoteOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Review", to: "/review", icon: FactCheckOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "AI Inbox", to: "/ai-inbox", icon: InboxOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Monitor Queue", to: "/monitor-queue", icon: PendingActionsOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "Analytics", to: "/analytics", icon: InsightsOutlinedIcon, roles: AUTHOR_ROLES },
  { label: "History", to: "/history", icon: HistoryOutlinedIcon, roles: AUTHOR_ROLES },
];

function BrandLockup() {
  return (
    <Stack
      direction="row"
      spacing={1}
      sx={{ alignItems: "center", px: 2.5, py: 2 }}
    >
      <Box
        aria-hidden
        sx={{
          width: 28,
          height: 28,
          borderRadius: 1.5,
          bgcolor: "primary.main",
          color: "primary.contrastText",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 16,
          lineHeight: 1,
        }}
      >
        ◆
      </Box>
      <Typography variant="h6" component="div" sx={{ fontWeight: 700 }}>
        CurricMesh
      </Typography>
    </Stack>
  );
}

/** The shared sidebar contents, reused by both the permanent and mobile drawers. */
function SidebarContent({
  onNavigate,
}: {
  onNavigate?: () => void;
}) {
  const { orgName, role, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const canAuthorCCR = role != null && AUTHOR_ROLES.has(role);

  // Filter nav items to those the current role is permitted to see.
  // Items without a `roles` Set are visible to all authenticated users.
  const visibleItems = NAV_ITEMS.filter(
    (item) => !item.roles || (role != null && item.roles.has(role))
  );

  function handleLogout() {
    logout();
    navigate("/login");
  }

  function isActive(to: string) {
    if (to === "/") return location.pathname === "/";
    return location.pathname.startsWith(to);
  }

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <BrandLockup />
      <Divider />

      <List sx={{ px: 1.5, py: 1, flexGrow: 1 }}>
        {visibleItems.map((item) => {
          const Icon = item.icon;
          return (
            <ListItem key={item.to} disablePadding sx={{ mb: 0.5 }}>
              <ListItemButton
                component={RouterLink}
                to={item.to}
                selected={isActive(item.to)}
                onClick={onNavigate}
                sx={{ borderRadius: 2 }}
              >
                <ListItemIcon sx={{ minWidth: 38 }}>
                  <Icon />
                </ListItemIcon>
                <ListItemText
                  primary={item.label}
                  slotProps={{ primary: { sx: { fontSize: "0.925rem" } } }}
                />
              </ListItemButton>
            </ListItem>
          );
        })}

        {canAuthorCCR && (
          <ListItem disablePadding sx={{ mt: 1.5, px: 0.5 }}>
            <Button
              fullWidth
              variant="contained"
              component={RouterLink}
              to="/ccrs/new"
              onClick={onNavigate}
              startIcon={<AddOutlinedIcon />}
            >
              New Change Request
            </Button>
          </ListItem>
        )}
      </List>

      <Divider />
      {/* Log Out as a clear menu item (not just an icon). */}
      <List sx={{ px: 1.5, py: 1 }}>
        <ListItem disablePadding>
          <ListItemButton onClick={handleLogout} sx={{ borderRadius: 2 }}>
            <ListItemIcon sx={{ minWidth: 38 }}>
              <LogoutOutlinedIcon />
            </ListItemIcon>
            <ListItemText
              primary="Log Out"
              slotProps={{ primary: { sx: { fontSize: "0.925rem" } } }}
            />
          </ListItemButton>
        </ListItem>
      </List>

      <Divider />
      {/* Identity footer — org NAME + role only (never the raw org id).
          Allow text to wrap rather than ellipsis-truncate: translated org names
          can be ~40% longer and noWrap produces a hard clip in narrow sidebars.
          The title attribute keeps the full value accessible on hover. */}
      <Box sx={{ px: 2.5, py: 1.5, minWidth: 0 }}>
        {orgName && (
          <Typography variant="body2" title={orgName} sx={{ fontWeight: 600 }}>
            {orgName}
          </Typography>
        )}
        {role && (
          <Typography variant="caption" color="text.secondary" title={role}>
            {role}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { orgName, role } = useAuth();
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));
  const [mobileOpen, setMobileOpen] = useState(false);
  const { mode, toggle } = useColorMode();

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
      {/* Permanent sidebar on md+ */}
      <Box
        component="nav"
        sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }}
        aria-label="main navigation"
      >
        {isDesktop ? (
          <Drawer
            variant="permanent"
            open
            sx={{
              "& .MuiDrawer-paper": {
                width: DRAWER_WIDTH,
                boxSizing: "border-box",
              },
            }}
          >
            <SidebarContent />
          </Drawer>
        ) : (
          <Drawer
            variant="temporary"
            open={mobileOpen}
            onClose={() => setMobileOpen(false)}
            ModalProps={{ keepMounted: true }}
            sx={{
              "& .MuiDrawer-paper": {
                width: DRAWER_WIDTH,
                boxSizing: "border-box",
              },
            }}
          >
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </Drawer>
        )}
      </Box>

      {/* Main column: slim topbar + content */}
      <Box
        sx={{
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
        }}
      >
        <AppBar position="sticky">
          <Toolbar>
            {!isDesktop && (
              <IconButton
                aria-label="open navigation"
                edge="start"
                onClick={() => setMobileOpen(true)}
                sx={{ mr: 1 }}
              >
                <MenuOutlinedIcon />
              </IconButton>
            )}
            <Box sx={{ flexGrow: 1 }} />
            <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
              <Tooltip
                title={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              >
                <IconButton
                  onClick={toggle}
                  aria-label="toggle dark mode"
                  data-testid="color-mode-toggle"
                  color="inherit"
                >
                  {mode === "dark" ? <Brightness7Icon /> : <Brightness4Icon />}
                </IconButton>
              </Tooltip>
              {role && (
                <Chip label={role} size="small" color="primary" variant="outlined" />
              )}
              {orgName && (
                <Typography variant="body2" color="text.secondary">
                  {orgName}
                </Typography>
              )}
            </Stack>
          </Toolbar>
        </AppBar>

        <Box component="main" sx={{ flex: 1 }}>
          <Container maxWidth="lg" sx={{ mt: 4, mb: 4 }}>
            {children}
          </Container>
        </Box>
      </Box>
    </Box>
  );
}
