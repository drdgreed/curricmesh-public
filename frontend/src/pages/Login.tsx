import { useState, type FormEvent } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import { useAuth } from "../auth/AuthContext";

const VALUE_BULLETS = [
  "Immutable, content-addressed versions",
  "Executable releases — merge a change like a PR",
  "Dependency-aware alignment across the curriculum",
];

const DEMO_LOGINS = [
  { label: "Architect", email: "architect@careerforge.demo" },
  { label: "Program Manager", email: "program_manager@careerforge.demo" },
  { label: "Instructor", email: "instructor@careerforge.demo" },
];
const DEMO_PASSWORD = "demo-pass-123";

/** The ◆ CurricMesh lockup, mirroring BrandLockup in Layout for consistency. */
function BrandMark({ contrast = false }: { contrast?: boolean }) {
  return (
    <Stack direction="row" spacing={1.25} sx={{ alignItems: "center" }}>
      <Box
        aria-hidden
        sx={{
          width: 34,
          height: 34,
          borderRadius: 1.5,
          bgcolor: contrast ? "common.white" : "primary.main",
          color: contrast ? "primary.main" : "primary.contrastText",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 20,
          lineHeight: 1,
        }}
      >
        ◆
      </Box>
      <Typography variant="h5" component="div" sx={{ fontWeight: 700 }}>
        CurricMesh
      </Typography>
    </Stack>
  );
}

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: Location })?.from?.pathname || "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch {
      setError("Invalid email or password. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function fillDemo(demoEmail: string) {
    setEmail(demoEmail);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "1.05fr 1fr" },
        bgcolor: "background.default",
      }}
    >
      {/* Brand panel — top on mobile, left on desktop */}
      <Box
        sx={{
          position: "relative",
          color: "primary.contrastText",
          px: { xs: 4, md: 8 },
          py: { xs: 6, md: 10 },
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          overflow: "hidden",
          background: (theme) =>
            `linear-gradient(150deg, ${theme.palette.primary.main} 0%, ${theme.palette.primary.dark} 60%, ${theme.palette.secondary.dark} 130%)`,
        }}
      >
        {/* Subtle decorative glow */}
        <Box
          aria-hidden
          sx={{
            position: "absolute",
            top: -120,
            right: -120,
            width: 360,
            height: 360,
            borderRadius: "50%",
            bgcolor: "rgba(255,255,255,0.10)",
            filter: "blur(8px)",
          }}
        />
        <Box sx={{ position: "relative", maxWidth: 460 }}>
          <BrandMark contrast />
          <Typography
            variant="h4"
            sx={{ mt: 4, fontWeight: 700, lineHeight: 1.15 }}
          >
            Version control for curriculum
          </Typography>
          <Typography
            variant="body1"
            sx={{ mt: 1.5, opacity: 0.85, maxWidth: 420 }}
          >
            Treat your courses like code — branch, review, and release changes
            with full lineage and dependency awareness.
          </Typography>

          <Stack spacing={1.5} sx={{ mt: 4 }}>
            {VALUE_BULLETS.map((b) => (
              <Stack
                key={b}
                direction="row"
                spacing={1.25}
                sx={{ alignItems: "flex-start" }}
              >
                <CheckCircleOutlineIcon
                  sx={{ fontSize: 20, mt: "1px", opacity: 0.9 }}
                />
                <Typography variant="body1" sx={{ opacity: 0.95 }}>
                  {b}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      </Box>

      {/* Login card column */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          px: { xs: 3, md: 6 },
          py: { xs: 5, md: 8 },
        }}
      >
        <Paper
          elevation={0}
          sx={{
            p: { xs: 3, sm: 4 },
            width: "100%",
            maxWidth: 420,
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="h5" sx={{ fontWeight: 700 }} gutterBottom>
            Sign in
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
            Welcome back. Sign in to continue to your workspace.
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              label="Email"
              type="email"
              fullWidth
              margin="normal"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              autoFocus
              slotProps={{
                htmlInput: {
                  autoCapitalize: "none",
                  autoCorrect: "off",
                  spellCheck: false,
                },
              }}
            />
            <TextField
              label="Password"
              type="password"
              fullWidth
              margin="normal"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
            <Button
              type="submit"
              fullWidth
              variant="contained"
              size="large"
              sx={{ mt: 3 }}
              disabled={loading}
            >
              {loading ? "Signing in…" : "Sign In"}
            </Button>
          </Box>

          {/* Demo-login hints */}
          <Box sx={{ mt: 3 }}>
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ display: "block", mb: 1 }}
            >
              Demo logins (password: {DEMO_PASSWORD})
            </Typography>
            <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }}>
              {DEMO_LOGINS.map((d) => (
                <Button
                  key={d.email}
                  size="small"
                  variant="outlined"
                  onClick={() => fillDemo(d.email)}
                >
                  {d.label}
                </Button>
              ))}
            </Stack>
          </Box>
        </Paper>
      </Box>
    </Box>
  );
}
