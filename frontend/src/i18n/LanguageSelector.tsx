/**
 * LanguageSelector (T3b) — the session tutor-language picker.
 *
 * A compact dropdown the learner sets when they start a learning session; it
 * writes the choice into the session-held SessionLanguageContext, which the
 * tutor requests (ask / coach / assess) then carry as their ``language`` param.
 * Scope is the tutor's replies only — it does NOT translate the UI or content.
 */

import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";

import {
  TUTOR_LANGUAGES,
  useSessionLanguage,
} from "./SessionLanguageContext";

export function LanguageSelector({ size = "small" }: { size?: "small" | "medium" }) {
  const { language, setLanguage } = useSessionLanguage();
  return (
    <TextField
      select
      size={size}
      label="Tutor language"
      value={language}
      onChange={(e) => setLanguage(e.target.value)}
      data-testid="tutor-language-selector"
      slotProps={{ htmlInput: { "data-testid": "tutor-language-input" } }}
      sx={{ minWidth: 150 }}
      helperText="The tutor replies in this language."
    >
      {TUTOR_LANGUAGES.map((l) => (
        <MenuItem key={l.value} value={l.value} data-testid={`lang-opt-${l.value}`}>
          {l.label}
        </MenuItem>
      ))}
    </TextField>
  );
}

export default LanguageSelector;
