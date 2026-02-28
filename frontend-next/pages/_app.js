import "../styles/style.css";
import "../styles/new-project.css";
import "../styles/lecture.css";
import { useCallback, useEffect, useState } from "react";

const THEME_STORAGE_KEY = "nn_theme";
const THEME_LIGHT = "light";
const THEME_DARK = "dark";

function normalizeTheme(value) {
  return value === THEME_DARK ? THEME_DARK : THEME_LIGHT;
}

function detectInitialTheme() {
  if (typeof window === "undefined") {
    return THEME_LIGHT;
  }

  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (saved) {
      return normalizeTheme(saved);
    }
  } catch {
    // Ignore localStorage failures.
  }

  try {
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return THEME_DARK;
    }
  } catch {
    // Ignore media query failures.
  }

  return THEME_LIGHT;
}

export default function App({ Component, pageProps }) {
  const [theme, setThemeState] = useState(THEME_LIGHT);

  const applyTheme = useCallback((nextTheme, persist = true) => {
    const normalized = normalizeTheme(nextTheme);
    setThemeState(normalized);

    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", normalized);
      document.documentElement.style.colorScheme = normalized;
    }

    if (!persist || typeof window === "undefined") {
      return;
    }

    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch {
      // Ignore localStorage failures.
    }
  }, []);

  useEffect(() => {
    const initial = detectInitialTheme();
    applyTheme(initial, false);

    const onThemeSet = (event) => {
      const nextTheme = event?.detail?.theme;
      if (nextTheme === THEME_DARK || nextTheme === THEME_LIGHT) {
        applyTheme(nextTheme, true);
      }
    };

    window.addEventListener("nn:theme:set", onThemeSet);
    return () => {
      window.removeEventListener("nn:theme:set", onThemeSet);
    };
  }, [applyTheme]);

  const setTheme = useCallback(
    (nextTheme) => {
      applyTheme(nextTheme, true);
    },
    [applyTheme]
  );

  return <Component {...pageProps} theme={theme} setTheme={setTheme} />;
}
