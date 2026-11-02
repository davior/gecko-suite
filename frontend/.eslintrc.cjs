/**
 * ESLint, which gecko-notes does not have at all — its only trace is a couple of
 * stray `eslint-disable` comments for a tool that was never configured.
 *
 * Flat config is the future, but eslint-plugin-react-hooks' flat support lagged, and
 * a lint setup that half-works is worse than none. Revisit when the ecosystem settles.
 */
module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', 'node_modules', '.eslintrc.cjs', 'src/assets/fonts'],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['react-refresh'],
  rules: {
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    // An unused parameter prefixed with _ is documentation, not dead code.
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    // Empty catch blocks are deliberate throughout: localStorage throws in private
    // mode, and a lost preference must never break a render.
    'no-empty': ['error', { allowEmptyCatch: true }],
  },
  overrides: [
    {
      files: ['*.test.ts', '*.test.tsx', 'src/test-setup.ts'],
      env: { node: true },
    },
  ],
}
