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
      nixpkgs,
      systems,
      devenv,
      treefmt-nix,
      ...
    }:
    let
      eachSystem = nixpkgs.lib.genAttrs (import systems);
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
              cp cv.pdf $out/Ivan_Dimitrov_Resume_Software_Developer.pdf
              cp cv-ats.pdf $out/Ivan_Dimitrov_Resume_Software_Developer_ATS.pdf
              runHook postBuild
            '';
          };
        }
      );
      devShells = eachSystem (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
          };
          chromium-sandboxed = (
            pkgs.writers.writeNuBin "chromium-sandboxed"
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
                  let wl = ($env.WAYLAND_DISPLAY? | default "wayland-0")
                  let sock = ($xrd | path join $wl)
                  let opts = ["--die-with-parent" "--unshare-all" "--share-net" "--new-session" "--proc" "/proc" "--dev" "/dev" "--dev-bind" "/dev/dri" "/dev/dri" "--tmpfs" "/dev/shm" "--tmpfs" "/tmp" "--ro-bind" "/nix/store" "/nix/store" "--ro-bind-try" "/etc/fonts" "/etc/fonts" "--ro-bind-try" "/etc/ssl" "/etc/ssl" "--ro-bind-try" "/etc/resolv.conf" "/etc/resolv.conf" "--ro-bind-try" "/etc/nsswitch.conf" "/etc/nsswitch.conf" "--ro-bind-try" "/run/opengl-driver" "/run/opengl-driver" "--dir" "/home" "--dir" "/home/ivand" "--dir" "/home/ivand/src" "--dir" "/home/ivand/src/cv" "--ro-bind" "/home/ivand/src/cv/result" "/home/ivand/src/cv/result" "--dir" "/run/user" "--tmpfs" $xrd "--bind" $sock $sock "--chdir" "/tmp" "--setenv" "HOME" "/tmp" "--setenv" "XDG_RUNTIME_DIR" $xrd "--setenv" "WAYLAND_DISPLAY" $wl]
                  exec bwrap ...$opts -- "chromium" ...$rest
                }
              ''
          );
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
                ];
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
