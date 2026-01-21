# AGENTS.md — Agent Coding Guidelines for the `cv` Repository

Welcome, agent! This file provides authoritative conventions to help both human and AI agents work productively and safely in this repository. It covers:

- How to build, lint, format, and test code (and how to run single tests/pipelines)
- Style guidelines for code, imports, types, naming, error handling
- Integration with CI tooling and git hooks

---

## 1. Project Structure & Overview

This is a Nix flakes-based, Haskell-driven project—primarily using `cv.hs` as the entrypoint. Building, development shell, formatting, and much of the infra is managed through Nix (`flake.nix`) and [devenv](https://devenv.sh/). The repo generates a PDF (via Haskell GHC) as its primary product. Some system-level checks are included.

## 2. Build, Lint, and Format Commands

Use these commands inside the Nix dev shell (recommended: always open a dev shell first):

### 2.1 Build

- **Full Build:**
  ```sh
  devenv tasks run build
  # or (see specific tasks)
  devenv task build:init
  devenv task build:cv
  devenv task build:bin
  # Or the default Nix build:
  nix build
  ```
- **Clean:**
  ```sh
  devenv task clean:all
  ```

### 2.2 Linting/Static Analysis

These are managed by pre-commit hooks and available manually:

- **Nix (format, lint):**
  ```sh
  nix fmt      # Runs all formatters (treefmt, nixfmt, ormolu, prettier, statix, deadnix)
  ```

### 2.3 Pre-commit Hooks

Pre-commit hooks are mandatory and defined in `.pre-commit-config.yaml` (auto-generated). They will:

- Run `nixfmt`, `statix`, `deadnix` on Nix files automatically
- Run `ormolu` on `.hs` files
- Run `prettier` on anything else (by extension)

You **must** pass pre-commit before merging/committing code. Always run `nix fmt` before submission.

## 3. Test Commands

- **Haskell Testing:**
  There is no explicit standard test harness here. If/when unit/integration tests are present, follow standard Haskell practices (e.g., `cabal test` or `stack test`).
  > For a _single test_, consult the relevant tool; typically: `cabal test --test-options="-m 'test name'"`, or run the specific file/module with `runghc`, but this codebase does not currently expose such tests by default.

## 4. Code Style & Formatting

### 4.1 Imports

- Prefer qualified imports (`import qualified Foo.Bar as Bar`)
- Use `ImportQualifiedPost` extension for post-qualified imports (see `ormolu` config)
- Group standard, third-party, and local-name imports
- Remove unused imports (hooks run dead code checks via deadnix/statix)

### 4.2 Formatting

- All code must be formatted with `nix fmt`
- Indentation: follow tool output; do **not** hand-format

### 4.3 Types

- Strongly prefer explicit type signatures on all top-level Haskell functions
- Use `CamelCase` for types and type variables
- Use `camelCase` for functions and values
- Use `snake_case` for filenames and configuration files
- Avoid ambiguous or overloaded names

### 4.4 Naming Conventions

- Modules/files: `CamelCase` as per Haskell best practices
- Constants: all-caps with underscores (e.g., `PDF_PATH`)

### 4.5 Error Handling

- Prefer typed errors (`Either`, `Maybe`, or `ErrorT` monads for effectful code)
- Emit explicit error messages upon failure
- Avoid `error`/`undefined` in production code (use only in stubs)
- Surface errors to calling code if possible, don’t swallow silently

### 4.6 Comments & Documentation

- Use Haddock-style comments `-- |` for exported/top-level functions
- Add clarifying in-line comments only where logic is non-obvious
- Avoid block/prolix comments; make code readable instead
- Document public interface and main points of entry in `cv.hs`

## 5. Working with Nix & devenv

- Use the existing `devenv` tasks; add new ones for build/test if extending the repo
- Keep all config defaults in `flake.nix`; do **not** fork one-off scripts unless necessary
- If you extend this repo’s formatters or hooks, update documentation accordingly
- Always use system flakes for dependency pinning and devshell environments

## 6. Agentic Coding Recommendations

- Never commit code that fails format or pre-commit; always run `nix fmt` and pre-commit locally first
- Do not hand-format or bypass enforced tool output
- If single-test running is required and not obvious, prefer running the smallest affected set possible
- Comment all exported or agent-facing code thoroughly
- Avoid overengineering; bias to idiomatic Haskell/Nix
- Make minimal, atomic diffs; never combine style, refactor, or feature changes in one commit

## 7. References

- [NixOS Manual: flake](https://nixos.org/manual/nix/stable/command-ref/new-cli/nix3-flake)
- [Ormolu Haskell Formatter](https://github.com/tweag/ormolu)
- [Devenv: repeatable, declarative development shells](https://devenv.sh/)
- [Statix and Deadnix](https://github.com/nerdypepper/statix) / https://github.com/hercules-ci/Deadnix
- [Prettier](https://prettier.io/)

---

This document is living. Update it as tools/procedures evolve. Always check that the above is enforced **before** merging or committing changes.
