import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["**/.venv/**", "**/venv/**", "data/**"] },
  js.configs.recommended,
  {
    files: ["app/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      // Plain <script> tags: no modules, no bundler.
      sourceType: "script",
      globals: globals.browser,
    },
    rules: {
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-var": "error",
      "prefer-const": "error",
      "no-unused-vars": ["error", { caughtErrors: "none" }],
      // `try { localStorage... } catch (_) {}` is the idiom for best-effort calls.
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
];
