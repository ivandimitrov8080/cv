{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default";
    devenv.url = "github:cachix/devenv";
    devenv.inputs.nixpkgs.follows = "nixpkgs";
    treefmt-nix.url = "github:numtide/treefmt-nix";
  };
  outputs =
    inputs@{
      self,
      nixpkgs,
      systems,
      devenv,
      treefmt-nix,
      ...
    }:
    let
      eachSystem = nixpkgs.lib.genAttrs (import systems);

      # ---------------------------------------------------------------------
      # Python side of the repo (py/): the itempipe port.
      #
      # Dependencies are named, then filtered against the pinned nixpkgs,
      # because some of them (langchain-mcp-adapters, ddgs) land in nixpkgs
      # later than the rest.  Anything missing is printed by the dev shell;
      # `uv sync` in py/ covers those from PyPI.
      # ---------------------------------------------------------------------
      pyRuntimeDeps = [
        "langchain"
        "langchain-core"
        "langchain-openai"
        "langgraph"
        "langchain-mcp-adapters"
        "mcp"
        "pydantic"
        "httpx"
        "peewee"
        "trafilatura"
        "ddgs"
      ];
      pyDevDeps = [
        "ruff"
      ];
      pyPick = ps: names: map (name: ps.${name}) (builtins.filter (name: builtins.hasAttr name ps) names);
      pyMissing = ps: names: builtins.filter (name: !(builtins.hasAttr name ps)) names;

      # Runtime environment: what the CLI actually imports.
      pyRuntime = pkgs: pkgs.python3.withPackages (ps: pyPick ps pyRuntimeDeps);
      # Dev environment: runtime + tests + linter, and the whole toolchain.
      pyDev = pkgs: pkgs.python3.withPackages (ps: pyPick ps pyRuntimeDeps ++ pyPick ps pyDevDeps);

      # A plain install of py/ as `bin/itempipe`.  Deliberately not built with
      # buildPythonApplication: this keeps one source of truth (the tree in py/)
      # for prompts/, schema.sql and tests, and does not depend on the
      # pyproject build backend being available in the pinned nixpkgs.
      mkItempipe =
        pkgs:
        let
          python = pyRuntime pkgs;
        in
        pkgs.stdenv.mkDerivation {
          pname = "itempipe";
          version = "0.1.0";

          src = ./py;

          nativeBuildInputs = [ pkgs.makeWrapper ];

          installPhase = ''
            runHook preInstall
            mkdir -p $out/lib/itempipe $out/bin
            cp -r . $out/lib/itempipe/src
            makeWrapper ${python}/bin/python $out/bin/itempipe \
              --add-flags "-m itempipe" \
              --prefix PYTHONPATH : "$out/lib/itempipe/src"
            runHook postInstall
          '';

          meta = {
            description = "N stateless workers, one SQLite database (TODO: rename)";
            mainProgram = "itempipe";
          };
        };
    in
    {
      packages = eachSystem (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };
        in
        {
          itempipe = mkItempipe pkgs;

          default = pkgs.stdenv.mkDerivation {
            name = "cv";
            version = "1.0";
            src = ./.;
            nativeBuildInputs = with pkgs; [
              typst
              corefonts
            ];
            buildPhase = ''
              runHook preBuild
              typst c cv.typ
              typst c cv-ats.typ
              runHook postBuild
            '';
            installPhase = ''
              runHook preBuild
              mkdir -p $out
              cp cv.pdf $out/Ivan_Dimitrov_Resume_IT_Consultant.pdf
              cp cv-ats.pdf $out/Ivan_Dimitrov_Resume_IT_Consultant_ATS.pdf
              runHook postBuild
            '';
          };
        }
      );
      apps = eachSystem (
        system:
        let
          itempipe = self.packages.${system}.itempipe;
        in
        {
          itempipe = {
            type = "app";
            program = "${itempipe}/bin/itempipe";
          };
          default = {
            type = "app";
            program = "${itempipe}/bin/itempipe";
          };
        }
      );
      checks = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pyDev pkgs;
        in
        nixpkgs.lib.optionalAttrs (builtins.hasAttr "peewee" pkgs.python3.pkgs) {
          itempipe-tests = pkgs.runCommand "itempipe-tests" { nativeBuildInputs = [ python ]; } ''
            cp -r ${./py} source
            chmod -R u+w source
            cd source
            export PYTHONPATH=$PWD
            export PYTHONDONTWRITEBYTECODE=1
            python -m unittest -v test_itempipe
            touch $out
          '';
        }
      );
      devShells = eachSystem (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };
          pythonEnv = pyDev pkgs;
          pyNotPackaged = pyMissing pkgs.python3.pkgs (pyRuntimeDeps ++ pyDevDeps);
          chromium-sandboxed = pkgs.writers.writeNuBin "chromium-sandboxed"
              {
                makeWrapperArgs = [
                  "--prefix"
                  "PATH"
                  ":"
                  "${pkgs.lib.makeBinPath [
                    pkgs.chromium
                    pkgs.bubblewrap
                  ]}"
                ];
              }
              ''
                def --wrapped main [...rest] {
                  let xrd = ($env.XDG_RUNTIME_DIR? | default "/run/user/1000")
                  let wl = ($env.WAYLAND_DISPLAY? | default "wayland-1")
                  let sock = ($xrd | path join $wl)
                  let opts = ["--die-with-parent" "--unshare-all" "--share-net" "--new-session" "--proc" "/proc" "--dev" "/dev" "--dev-bind" "/dev/dri" "/dev/dri" "--tmpfs" "/dev/shm" "--tmpfs" "/tmp" "--ro-bind" "/nix/store" "/nix/store" "--ro-bind-try" "/etc/fonts" "/etc/fonts" "--ro-bind-try" "/etc/ssl" "/etc/ssl" "--ro-bind-try" "/etc/resolv.conf" "/etc/resolv.conf" "--ro-bind-try" "/etc/nsswitch.conf" "/etc/nsswitch.conf" "--ro-bind-try" "/run/opengl-driver" "/run/opengl-driver" "--dir" "/home" "--dir" "/home/ivand" "--dir" "/home/ivand/src" "--dir" "/home/ivand/src/cv" "--ro-bind" "/home/ivand/src/cv/result" "/home/ivand/src/cv/result" "--dir" "/run/user" "--tmpfs" $xrd "--bind" $sock $sock "--chdir" "/tmp" "--setenv" "HOME" "/tmp" "--setenv" "XDG_RUNTIME_DIR" $xrd "--setenv" "WAYLAND_DISPLAY" $wl]
                  exec bwrap ...$opts -- "chromium" ...$rest
                }
              '';
        in
        {
          default = devenv.lib.mkShell {
            inherit inputs pkgs;
            modules = [
              {
                devenv.root = "/home/ivand/src/cv";
                languages = {
                  typst = {
                    enable = true;
                    lsp.enable = true;
                    fontPaths = with pkgs; [ "${corefonts}/share/fonts" ];
                  };
                };
                packages = with pkgs; [
                  typst
                  pdfminer
                  playwright-mcp
                  chromium-sandboxed
                  sqlite
                  msmtp
                  uv
                  pythonEnv
                ];
                env = {
                  # `python -m itempipe` works from the repo root.
                  PYTHONPATH = "/home/ivand/src/cv/py";
                  ITEMPIPE_DB = "/home/ivand/src/cv/py/itempipe.db";
                };
                enterShell = ''
                  itempipe() { python -m itempipe "$@"; }
                  export -f itempipe
                  itempipe-check() { (cd /home/ivand/src/cv/py && python -m unittest -v test_itempipe); }
                  export -f itempipe-check
                  ${pkgs.lib.optionalString (pyNotPackaged != [ ]) ''
                    echo "itempipe: not packaged by this nixpkgs: ${builtins.concatStringsSep " " pyNotPackaged}" >&2
                    echo "itempipe: run 'uv sync' in py/ if you need them from PyPI" >&2
                  ''}
                '';
              }
            ];
          };
        }
      );
      formatter = eachSystem (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        (treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";
          programs = {
            nixfmt.enable = true;
            prettier.enable = true;
            deadnix.enable = true;
            statix.enable = true;
          };
        }).config.build.wrapper
      );
    };
}
