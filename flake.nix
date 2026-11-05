{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default";
    devenv.url = "github:cachix/devenv";
    devenv.inputs.nixpkgs.follows = "nixpkgs";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    pyproject-nix.url = "github:pyproject-nix/pyproject.nix";
    pyproject-nix.inputs.nixpkgs.follows = "nixpkgs";
    uv2nix.url = "github:pyproject-nix/uv2nix";
    uv2nix.inputs.nixpkgs.follows = "nixpkgs";
    pyproject-build-systems.url = "github:pyproject-nix/build-system-pkgs";
    pyproject-build-systems.inputs.nixpkgs.follows = "nixpkgs";
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
              cp cv.pdf $out/Ivan_Dimitrov_Resume_IT_Consultant.pdf
              cp cv-ats.pdf $out/Ivan_Dimitrov_Resume_IT_Consultant_ATS.pdf
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
          inherit (import ./nix { inherit pkgs; }) chromium-sandboxed;
        in
        {
          default = devenv.lib.mkShell {
            inherit inputs pkgs;
            modules = [
              (
                { config, ... }:
                let
                  itempipe = config.languages.python.import ./py { };
                in
                {
                  devenv.root = "/home/ivand/src/cv";
                  languages = {
                    typst = {
                      enable = true;
                      lsp.enable = true;
                      fontPaths = with pkgs; [ "${corefonts}/share/fonts" ];
                    };
                    python = {
                      enable = true;
                      uv.enable = true;
                    };
                  };
                  packages = with pkgs; [
                    typst
                    pdfminer
                    chromium-sandboxed
                    itempipe
                  ];
                }
              )
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
