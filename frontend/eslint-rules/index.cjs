/**
 * iter209 — ESLint plugin entry point. Bundles all `local-rules/*` rules.
 */
'use strict';

module.exports = {
  rules: {
    'no-module-level-t': require('./no-module-level-t.cjs'),
    'no-i18n-t-shadow':  require('./no-i18n-t-shadow.cjs'),
  },
};
