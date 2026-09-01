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
            ];
            buildPhase = ''
              runHook preBuild
              typst c cv.typ
              runHook postBuild
            '';
            installPhase = ''
              runHook preBuild
              mkdir -p $out
              cp cv.pdf $out
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
        in
        {
          default = devenv.lib.mkShell {
            inherit inputs pkgs;
            modules = [
              {
                devenv.root = "/home/ivand/src/cv";
                packages = with pkgs; [
                  typst
                ];
                git-hooks.hooks = {
                  nixfmt.enable = true;
                  prettier.enable = true;
                  deadnix.enable = true;
                  statix.enable = true;
                };
                tasks = {
                  "clean:all" = {
                    exec = "rm -rf cv.pdf";
                  };
                  "build:cv" = {
                    exec = "typst c cv.typ";
                  };
                };
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
