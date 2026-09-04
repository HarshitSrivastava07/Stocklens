/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./hooks/**/*.{js,ts,jsx,tsx}",
    "./store/**/*.{js,ts}",
    "./lib/**/*.{js,ts}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#2188ff",
          dark: "#0d6efd",
        },
        surface: {
          base: "#080c10",
          DEFAULT: "#0d1117",
          elevated: "#161b22",
          border: "#21262d",
          hover: "#1c2128",
        },
        signal: {
          green: "#3fb950",
          yellow: "#e3b341",
          red: "#f85149",
          grey: "#6e7681",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      borderRadius: {
        sm: "6px",
        md: "10px",
        lg: "16px",
        xl: "24px",
      },
    },
  },
  plugins: [],
};
