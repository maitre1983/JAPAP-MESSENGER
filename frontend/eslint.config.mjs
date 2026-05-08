// Flat ESLint config used by the pre-commit hook (iter189-hotfix follow-up).
// CRA's internal lint during `yarn start` still uses the legacy config baked
// into eslint-config-react-app — this file is ONLY what `npx eslint` +
// lint-staged use. Its sole job is to block commits that violate the React
// rule-of-hooks (the bug we just patched in MarketplaceProductPage.js)
// AND — iter209 — block any reintroduction of bare `t(...)` calls at module
// level (the iter208 emergency).
import reactHooks from 'eslint-plugin-react-hooks';
import babelParser from '@babel/eslint-parser';
import { createRequire } from 'module';

const require = createRequire(import.meta.url);
const localRules = require('./eslint-rules/index.cjs');

export default [
  {
    files: ['src/**/*.{js,jsx,ts,tsx}'],
    ignores: [
      'node_modules/**',
      'build/**',
      'dist/**',
      'public/**',
      '**/*.test.{js,jsx}',
    ],
    languageOptions: {
      parser: babelParser,
      parserOptions: {
        requireConfigFile: false,
        ecmaVersion: 'latest',
        sourceType: 'module',
        babelOptions: {
          presets: ['@babel/preset-react'],
        },
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'local-rules': localRules,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'off',
      'local-rules/no-module-level-t': 'error',
      'local-rules/no-i18n-t-shadow':  'error',
    },
  },
];
