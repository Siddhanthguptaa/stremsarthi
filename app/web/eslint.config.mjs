// Adapted from Saximn/privastream (Apache-2.0), src/web/frontend/eslint.config.mjs. See LICENSE.privastream.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "out/**", "next-env.d.ts", "public/**"],
  },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    rules: {
      "@typescript-eslint/no-require-imports": "off",
      "react/no-unescaped-entities": "off",
    },
  },
];

export default eslintConfig;
