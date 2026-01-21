# AGENTS.md

> Guidance and rules for agentic coding agents and LLMs contributing to this codebase (cv)
> Last updated: 2026-01-21

---
## 1. Build / Develop / Run / Lint / Test

### Build & Development
- The main entry point is `cv.tsx`. The codebase primarily renders a PDF from data in `cv.json` via React and @react-pdf/renderer.

- Core workflows:
  - **Build the PDF**: 
    ```sh
    bun run build
    # or directly:
    bun cv.tsx
    ```
  - **Development/Preview**: 
    Use the `makefile` (default and dev target):
    ```sh
    make
dev
    # or
    bun cv.tsx
    ```
    The process kills and restarts MuPDF as viewer (see makefile lines).

  - **Install**: 
    ```sh
    make install
    ```
    This moves the generated PDF to the `outputs` directory.

  - **Clean outputs**:
    ```sh
    make clean
    ```
    Removes build artifacts.

### Linting
- **Formatting**: Project uses Prettier (see package.json) for formatting. No lint scripts found, but `bun format` or external tools may be used if required in the future.
- **Enforced Prettier settings** (see below).

### Testing
- **IMPORTANT**: There is currently no formal test suite or test runner configured.
  - No scripts for `test`/`lint` present in package.json or makefile.
  - No `test` or `__tests__` directory, nor do main modules contain unit/integration tests.

#### Recommendations for Adding Tests
- Prefer Jest or Vitest for TypeScript/React/Bun.
- Place tests under `tests/`, or co-locate with modules as `<module>.test.ts(x)`.
- Aim to enable single-test execution by pattern, e.g.:
  ```sh
  bun test path/to/module.test.ts
  # or with jest
  npx jest path/to/module.test.ts -t 'test name'
  ```
- Add a `test` script to package.json when tests are introduced.

---
## 2. Code Style Guidelines

### General Principles
- TypeScript **strict mode**: Always use explicit types unless truly inferrable. See `tsconfig.json` for strictness.
- **Strong typing & enums**: Define and use types/interfaces for all data structures, even local state.
- **React best practices**: Use function components, keep components pure & presentational, prefer hooks over classes.
- **Functional composition**: Favor small, modular functions and pure transformations. Avoid side effects except at entry points.
- **Idiomatically modern**: Use ESNext features, spread syntax, destructuring, and top-level await if needed.

### Formatting & Imports
- **Formatting** is governed by Prettier (in `package.json`):
  - trailingComma: "es5"
  - tabWidth: 2
  - semi: true
  - singleQuote: false

- Use either single or double quotes as per Prettier; default is double quotes in this codebase.
- Keep imports grouped: Node/3rd-party, then internal modules, then types/interfaces.
- Prefer named imports, avoid `import * as ...` unless necessary.
- Path aliases are not configured; use relative imports for internal code, keep them clean.

### Naming Conventions
- **Functions**: `camelCase`
- **Types/Interfaces**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE` for exported, `camelCase` for local
- **Files**: `camelCase` or `kebab-case` as needed, with `.tsx` for React, `.ts` for logic/data

### Error Handling
- Fail early and clearly: throw/raise on invalid arguments. It's OK to use pragmatic checks (e.g. `if (!fs.existsSync(...))`).
- Return errors instead of throwing only in functional-style data transforms.
- Log errors to console if needed, but prefer throwing for agentic debugging (they are visible as exceptions).

### Docs & Comments
- Use **JSDoc/TSDoc** style for functions, interfaces, and complex objects.
- Inline comments: Only where logic is non-obvious, avoid clutter.
- TODOs: Use `// TODO(agent): ...` to flag actionable items for future agents/LLMs.

### TypeScript Config Highlights
See `tsconfig.json` (enforced for agents):
- `strict: true`, `forceConsistentCasingInFileNames: true`, `allowJs: true`, `downlevelIteration: true`
- Target: ESNext, Module: ESNext
- JSX: react-jsx
- Bun-specific types are included
- No emit

---
## 3. Contribution Style (for agents)
- **Atomic commits**: Group related changes together; avoid huge diffs. Use descriptive, concise commit messages, prefer imperative mood.
- **Keep it DRY**: If you need to use a pattern more than twice, refactor into a helper.
- Prefer code generation (from schema/types) over informal ad hoc scripts.
- **No secrets**: Do not commit real credentials or API keys.
- **Be proactive**: If a new dependency is needed for linting or testing, add it where appropriate and document the change.

---
## 4. Future Automation & Lint/CI Adoption
- If integrating a new test runner or linter (e.g. Vitest, Jest, ESLint):
  - Always add scripts under `package.json:scripts`
  - Update this AGENTS.md with new workflows
  - Add a one-liner in makefile for easy use
- Preference: Keep the workflow simple, reproducible, and Bun-native when possible.

---
## 5. No Cursor or Copilot Rules Currently
As of this writing, this repository does not contain any .cursor/rules, .cursorrules, or .github/copilot-instructions.md files. If any are added, agents should incorporate their requirements here.

---

# End of AGENTS.md
